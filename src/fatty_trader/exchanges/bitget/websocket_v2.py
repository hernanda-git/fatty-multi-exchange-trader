"""Bitget V2 dual-connection WebSocket transport.

Bitget v2 requires two distinct sockets: ticker lives only on the public
endpoint and positions/orders live only on the private endpoint. Subscribing
ticker on the private socket returns ``30016 Param error``; subscribing a
private channel on the public socket returns the same. That was verified
against the LIVE endpoints, so the split here is a protocol requirement, not
a design preference.

Failure is fail-closed by construction: the connection state is derived from
BOTH sockets. A live public ticker with a dead private leg reports unhealthy,
because mark price alone cannot protect a position.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from fatty_trader.exchanges.bitget.websocket import (
    WebSocketConnection,
    WebsocketsTransport,
    WebSocketTransport,
)
from fatty_trader.exchanges.bitget.ws_models import (
    BitgetWebSocketEvent,
    WebSocketProtocolError,
)
from fatty_trader.exchanges.bitget.ws_v2_models import (
    build_v2_login_message,
    build_v2_private_subscription,
    build_v2_public_subscription,
    normalize_v2_frame,
)

V2_PUBLIC_WS_URL = "wss://ws.bitget.com/v2/ws/public"
V2_PRIVATE_WS_URL = "wss://ws.bitget.com/v2/ws/private"


class V2ConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    STALE = "STALE"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"


class BitgetV2WebSocket:
    """Public ticker + authenticated private account stream as one logical link."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        passphrase: str,
        symbols: list[str] | tuple[str, ...],
        public_url: str = V2_PUBLIC_WS_URL,
        private_url: str = V2_PRIVATE_WS_URL,
        transport: WebSocketTransport | None = None,
        clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
        stale_after: float = 5.0,
        heartbeat_interval: float = 25.0,
        reconnect_base_delay: float = 1.0,
        reconnect_max_delay: float = 30.0,
    ) -> None:
        if not public_url.strip() or not private_url.strip():
            raise ValueError("Bitget v2 websocket endpoints are required")
        if stale_after <= 0:
            raise ValueError("Bitget v2 websocket stale_after must be positive")
        if heartbeat_interval <= 0:
            raise ValueError("Bitget v2 websocket heartbeat_interval must be positive")
        if reconnect_base_delay <= 0 or reconnect_max_delay < reconnect_base_delay:
            raise ValueError("Bitget v2 websocket reconnect delays are invalid")
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase
        self._symbols = self._normalize_symbols(symbols)
        self._public_url = public_url
        self._private_url = private_url
        self._transport = transport or WebsocketsTransport()
        self._clock = clock or time.monotonic
        self._wall_clock = wall_clock or time.time
        self._stale_after = stale_after
        self._heartbeat_interval = heartbeat_interval
        self._reconnect_base_delay = reconnect_base_delay
        self._reconnect_max_delay = reconnect_max_delay

        self._public: WebSocketConnection | None = None
        self._private: WebSocketConnection | None = None
        self._state = V2ConnectionState.DISCONNECTED
        self._last_event_at: float | None = None
        self._last_mark_event_at: dict[str, float] = {}
        self._last_pong_at: float | None = None
        self._last_ping_at: float | None = None
        self._missed_heartbeat_windows = 0

    # --- introspection -----------------------------------------------------

    @property
    def state(self) -> V2ConnectionState:
        return self._state

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    @property
    def public_endpoint(self) -> str:
        return self._public_url

    @property
    def private_endpoint(self) -> str:
        return self._private_url

    @property
    def last_event_at(self) -> float | None:
        return self._last_event_at

    @property
    def last_pong_at(self) -> float | None:
        return self._last_pong_at

    @property
    def last_event_age(self) -> float | None:
        if self._last_event_at is None:
            return None
        return max(0.0, self._clock() - self._last_event_at)

    def last_mark_event_at(self, symbol: str) -> float | None:
        return self._last_mark_event_at.get(symbol.strip().upper())

    def last_mark_event_age(self, symbol: str) -> float | None:
        received_at = self.last_mark_event_at(symbol)
        if received_at is None:
            return None
        return max(0.0, self._clock() - received_at)

    def _private_subscription_args(self) -> list[dict[str, str]]:
        """Validate credentials before we claim the private leg is available."""
        if not (self._api_key and self._api_secret and self._passphrase):
            raise ValueError("Bitget v2 private stream requires api key, secret and passphrase")
        return list(build_v2_private_subscription()["args"])

    # --- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Open both legs, authenticate the private one, subscribe both."""
        self._state = V2ConnectionState.CONNECTING
        try:
            self._private_subscription_args()  # fail before opening sockets
            public = await self._transport.connect(self._public_url)
            self._public = public
            private = await self._transport.connect(self._private_url)
            self._private = private

            await public.send(
                _json(build_v2_public_subscription(self._symbols, allow_empty=True))
            )
            await private.send(
                _json(
                    build_v2_login_message(
                        api_key=self._api_key,
                        passphrase=self._passphrase,
                        secret=self._api_secret,
                        timestamp_seconds=int(self._wall_clock()),
                    )
                )
            )
            await self._receive_login_ack(private)
            await private.send(_json(build_v2_private_subscription()))
        except Exception:
            # Never leave a half-open pair looking healthy.
            self._state = V2ConnectionState.FAILED
            await self._close_public()
            await self._close_private()
            raise

        now = self._clock()
        self._last_event_at = now
        self._last_mark_event_at = {}
        self._last_ping_at = now
        self._last_pong_at = now
        self._missed_heartbeat_windows = 0
        self._state = V2ConnectionState.CONNECTED

    async def reconnect(self) -> None:
        self._state = V2ConnectionState.RECONNECTING
        await self._close_public()
        await self._close_private()
        await self.connect()

    async def close(self) -> None:
        await self._close_public()
        await self._close_private()
        self._state = V2ConnectionState.DISCONNECTED

    async def subscribe_symbols(self, symbols: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        """Subscribe tickers on the PUBLIC socket only."""
        normalized = self._normalize_symbols(symbols)
        additions = tuple(symbol for symbol in normalized if symbol not in self._symbols)
        if not additions:
            return ()
        self._symbols = (*self._symbols, *additions)
        if self._public is not None and self._state in {
            V2ConnectionState.CONNECTED,
            V2ConnectionState.STALE,
        }:
            await self._public.send(_json(build_v2_public_subscription(additions)))
        return additions

    async def unsubscribe_symbols(self, symbols: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        """Unsubscribe tickers on the PUBLIC socket only."""
        normalized = self._normalize_symbols(symbols)
        removals = tuple(symbol for symbol in normalized if symbol in self._symbols)
        if not removals:
            return ()
        self._symbols = tuple(symbol for symbol in self._symbols if symbol not in removals)
        self._last_mark_event_at = {
            symbol: received_at
            for symbol, received_at in self._last_mark_event_at.items()
            if symbol in self._symbols
        }
        if self._public is not None and self._state in {
            V2ConnectionState.CONNECTED,
            V2ConnectionState.STALE,
        }:
            await self._public.send(
                _json(
                    {
                        "op": "unsubscribe",
                        "args": [
                            {"instType": "USDT-FUTURES", "channel": "ticker", "instId": symbol}
                            for symbol in removals
                        ],
                    }
                )
            )
        return removals

    async def receive_once(self) -> list[BitgetWebSocketEvent]:
        """Read both legs concurrently, returning the first result.

        FIRST_COMPLETED, not FIRST_EXCEPTION: a private leg may legitimately be
        quiet for a while, and it must not hold back public mark events. An
        errored leg still wins immediately and propagates.
        """
        if not self._both_legs_open():
            raise RuntimeError("Bitget v2 websocket is not connected")
        tasks = [
            asyncio.ensure_future(self._read_one(self._public, "public")),
            asyncio.ensure_future(self._read_one(self._private, "private")),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        events: list[BitgetWebSocketEvent] = []
        for task in done:
            error = task.exception()
            if error is not None:
                raise error
            events.extend(task.result())
        return events

    async def send_heartbeat_if_due(self) -> bool:
        """Send the bare text ``ping`` that v2 answers with ``pong``."""
        public, private = self._public, self._private
        if public is None or private is None:
            return False
        now = self._clock()
        if self._last_ping_at is not None and now - self._last_ping_at < self._heartbeat_interval:
            return False
        with contextlib.suppress(Exception):
            await public.send("ping")
            await private.send("ping")
        self._last_ping_at = now
        return True

    def reconnect_delay(self, attempt: int) -> float:
        """Capped exponential backoff, matching the legacy transport."""
        if attempt < 1:
            raise ValueError("reconnect attempt must be positive")
        exponent = min(attempt - 1, 30)
        return min(
            self._reconnect_max_delay,
            self._reconnect_base_delay * float(2**exponent),
        )

    async def run(
        self,
        on_event: Callable[[BitgetWebSocketEvent], Awaitable[None]],
        stop_event: asyncio.Event,
    ) -> None:
        attempt = 0
        try:
            while not stop_event.is_set():
                if not self._both_legs_open():
                    try:
                        if attempt:
                            await asyncio.sleep(self.reconnect_delay(attempt))
                        await self.connect()
                        if attempt:
                            print(
                                f"component=bitget-ws-v2 state=recovered "
                                f"public={self._public_url} private={self._private_url} "
                                f"consecutive_failures={attempt} symbols={len(self._symbols)}",
                                flush=True,
                            )
                        attempt = 0
                    except Exception as exc:
                        attempt += 1
                        print(
                            f"component=bitget-ws-v2 state=connect_error "
                            f"public={self._public_url} private={self._private_url} "
                            f"consecutive_failures={attempt} "
                            f"error_type={type(exc).__name__} error={exc}",
                            flush=True,
                        )
                        continue
                try:
                    await self.send_heartbeat_if_due()
                    for event in await self.receive_once():
                        await on_event(event)
                    self.check_freshness()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    attempt += 1
                    await self._close_public()
                    await self._close_private()
                    self._state = V2ConnectionState.RECONNECTING
        finally:
            await self.close()

    def check_freshness(self, symbol: str | None = None) -> bool:
        """Fresh requires BOTH legs open and fresh public mark events."""
        if not self._both_legs_open():
            if self._state is not V2ConnectionState.DISCONNECTED:
                self._state = V2ConnectionState.FAILED
            return False
        if symbol is not None:
            normalized = symbol.strip().upper()
            if normalized not in self._symbols:
                return False
            age = self.last_mark_event_age(normalized)
            return (
                self._state is V2ConnectionState.CONNECTED
                and age is not None
                and age <= self._stale_after
            )
        fresh = self._state is V2ConnectionState.CONNECTED and all(
            (age := self.last_mark_event_age(sym)) is not None and age <= self._stale_after
            for sym in self._symbols
        )
        if not fresh and self._state in {V2ConnectionState.CONNECTED, V2ConnectionState.STALE}:
            self._state = V2ConnectionState.STALE
        return fresh

    # --- internals ---------------------------------------------------------

    def _both_legs_open(self) -> bool:
        return self._public is not None and self._private is not None

    async def _read_one(
        self, connection: WebSocketConnection | None, leg: str
    ) -> list[BitgetWebSocketEvent]:
        if connection is None:
            raise RuntimeError(f"Bitget v2 {leg} socket is closed")
        raw = await asyncio.wait_for(connection.recv(), timeout=self._heartbeat_interval)
        if isinstance(raw, str) and raw.strip() == "pong":
            now = self._clock()
            self._last_pong_at = now
            self._last_event_at = now
            self._missed_heartbeat_windows = 0
            return []
        try:
            events = normalize_v2_frame(raw)
        except Exception:
            self._state = V2ConnectionState.RECONNECTING
            raise
        now = self._clock()
        self._last_event_at = now
        for event in events:
            if event.kind == "mark_price":
                self._last_mark_event_at[event.symbol] = now
        if events or self._state is V2ConnectionState.RECONNECTING:
            self._state = V2ConnectionState.CONNECTED
        return events

    async def _receive_login_ack(self, connection: WebSocketConnection) -> None:
        raw = await connection.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise WebSocketProtocolError("Bitget v2 login response is invalid") from exc
        if not isinstance(payload, dict):
            raise WebSocketProtocolError("Bitget v2 login response is invalid")
        if payload.get("event") == "error":
            raise WebSocketProtocolError(
                f"Bitget v2 login failed: {payload.get('code')}",
                code=str(payload.get("code", "")) or None,
            )
        if payload.get("event") != "login" or str(payload.get("code", "0")) not in {"0", "00000"}:
            raise WebSocketProtocolError("Bitget v2 login was not acknowledged")

    async def _close_public(self) -> None:
        connection, self._public = self._public, None
        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.close()

    async def _close_private(self) -> None:
        connection, self._private = self._private, None
        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.close()

    @staticmethod
    def _normalize_symbols(symbols: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for raw_symbol in symbols:
            symbol = str(raw_symbol).strip().upper()
            if not symbol:
                raise ValueError("Bitget v2 websocket symbol is required")
            if symbol not in normalized:
                normalized.append(symbol)
        return tuple(normalized)


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)
