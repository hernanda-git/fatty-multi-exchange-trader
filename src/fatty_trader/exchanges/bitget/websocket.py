"""Reconnecting Bitget Classic WebSocket transport.

The Classic REST adapter uses V2 endpoints, while the documented Classic WebSocket
protocol remains the ``/mix/v1/stream`` endpoint with ``mc``/``UMCBL`` channel
identifiers.  This module keeps that protocol boundary explicit and never places or
cancels orders from the reader.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Protocol, cast

from fatty_trader.exchanges.bitget.ws_models import (
    BitgetWebSocketEvent,
    WebSocketProtocolError,
    build_login_message,
    build_subscription_message,
    normalize_ws_message,
)

CLASSIC_WS_URL = "wss://ws.bitget.com/mix/v1/stream"


class WebSocketConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    STALE = "STALE"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"


class WebSocketConnection(Protocol):
    async def send(self, value: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


class WebSocketTransport(Protocol):
    async def connect(self, url: str) -> WebSocketConnection: ...


class WebsocketsTransport:
    """Production transport kept behind a lazy dependency import."""

    def __init__(self, *, open_timeout: float = 10.0, close_timeout: float = 5.0) -> None:
        self._open_timeout = open_timeout
        self._close_timeout = close_timeout

    async def connect(self, url: str) -> WebSocketConnection:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("Bitget WebSocket transport dependency is not installed") from exc
        return cast(
            WebSocketConnection,
            await websockets.connect(
                url,
                open_timeout=self._open_timeout,
                close_timeout=self._close_timeout,
                ping_interval=None,
            ),
        )


class BitgetClassicWebSocket:
    """One Classic connection for mark prices and private account events."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        passphrase: str,
        symbols: list[str] | tuple[str, ...],
        endpoint: str = CLASSIC_WS_URL,
        transport: WebSocketTransport | None = None,
        clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
        stale_after: float = 5.0,
        heartbeat_interval: float = 25.0,
        reconnect_base_delay: float = 1.0,
        reconnect_max_delay: float = 30.0,
    ) -> None:
        if not endpoint.strip():
            raise ValueError("Bitget websocket endpoint is required")
        if stale_after <= 0:
            raise ValueError("Bitget websocket stale_after must be positive")
        if heartbeat_interval <= 0:
            raise ValueError("Bitget websocket heartbeat_interval must be positive")
        if reconnect_base_delay <= 0 or reconnect_max_delay < reconnect_base_delay:
            raise ValueError("Bitget websocket reconnect delays are invalid")
        # build_subscription_message validates and deduplicates the symbol set.
        subscription = build_subscription_message(symbols, allow_empty=True)
        normalized = [
            str(arg["instId"]) for arg in subscription["args"] if arg["channel"] == "ticker"
        ]
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase
        self._symbols = tuple(normalized)
        self._endpoint = endpoint
        self._transport = transport or WebsocketsTransport()
        self._clock = clock or time.monotonic
        self._wall_clock = wall_clock or time.time
        self._stale_after = stale_after
        self._heartbeat_interval = heartbeat_interval
        self._reconnect_base_delay = reconnect_base_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._connection: WebSocketConnection | None = None
        self._state = WebSocketConnectionState.DISCONNECTED
        self._last_event_at: float | None = None
        self._last_mark_event_at: dict[str, float] = {}
        self._last_ping_at: float | None = None
        self._last_pong_at: float | None = None
        self._missed_heartbeat_windows = 0

    @property
    def state(self) -> WebSocketConnectionState:
        return self._state

    @property
    def last_event_at(self) -> float | None:
        return self._last_event_at

    @property
    def last_pong_at(self) -> float | None:
        return self._last_pong_at

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def last_event_age(self) -> float | None:
        if self._last_event_at is None:
            return None
        return max(0.0, self._clock() - self._last_event_at)

    def last_mark_event_at(self, symbol: str) -> float | None:
        """Return the local receive time of the latest mark event for one symbol."""
        return self._last_mark_event_at.get(symbol.strip().upper())

    def last_mark_event_age(self, symbol: str) -> float | None:
        received_at = self.last_mark_event_at(symbol)
        if received_at is None:
            return None
        return max(0.0, self._clock() - received_at)

    async def connect(self) -> None:
        """Open, authenticate, and subscribe once."""
        self._state = WebSocketConnectionState.CONNECTING
        try:
            connection = await self._transport.connect(self._endpoint)
            self._connection = connection
            timestamp = int(self._wall_clock())
            login = build_login_message(
                api_key=self._api_key,
                passphrase=self._passphrase,
                secret=self._api_secret,
                timestamp_seconds=timestamp,
            )
            await connection.send(_json(login))
            await self._receive_login_ack(connection)
            await connection.send(
                _json(build_subscription_message(self._symbols, allow_empty=True))
            )
        except Exception:
            self._state = WebSocketConnectionState.FAILED
            await self._close_connection()
            raise
        now = self._clock()
        self._last_event_at = now
        self._last_mark_event_at = {}
        self._last_ping_at = now
        self._last_pong_at = now
        self._missed_heartbeat_windows = 0
        self._state = WebSocketConnectionState.CONNECTED

    async def reconnect(self) -> None:
        """Close the old socket and perform a fresh login/subscription."""
        self._state = WebSocketConnectionState.RECONNECTING
        await self._close_connection()
        await self.connect()

    async def close(self) -> None:
        await self._close_connection()
        self._state = WebSocketConnectionState.DISCONNECTED

    async def subscribe_symbols(self, symbols: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        """Subscribe ticker channels for newly active symbols, idempotently."""
        normalized = self._normalize_symbols(symbols)
        additions = tuple(symbol for symbol in normalized if symbol not in self._symbols)
        if not additions:
            return ()
        self._symbols = (*self._symbols, *additions)
        if self._connection is not None and self._state in {
            WebSocketConnectionState.CONNECTED,
            WebSocketConnectionState.STALE,
        }:
            await self._connection.send(
                _json(
                    {
                        "op": "subscribe",
                        "args": [
                            {"instType": "mc", "channel": "ticker", "instId": symbol}
                            for symbol in additions
                        ],
                    }
                )
            )
        return additions

    async def unsubscribe_symbols(self, symbols: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        """Unsubscribe ticker channels for symbols with no active fallback position."""
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
        if self._connection is not None and self._state in {
            WebSocketConnectionState.CONNECTED,
            WebSocketConnectionState.STALE,
        }:
            await self._connection.send(
                _json(
                    {
                        "op": "unsubscribe",
                        "args": [
                            {"instType": "mc", "channel": "ticker", "instId": symbol}
                            for symbol in removals
                        ],
                    }
                )
            )
        return removals

    async def receive_once(self) -> list[BitgetWebSocketEvent]:
        if self._connection is None or self._state not in {
            WebSocketConnectionState.CONNECTED,
            WebSocketConnectionState.STALE,
        }:
            raise RuntimeError("Bitget websocket is not connected")
        connection = self._connection
        try:
            raw = await asyncio.wait_for(connection.recv(), timeout=self._heartbeat_interval)
        except TimeoutError as exc:
            self._missed_heartbeat_windows += 1
            if self._missed_heartbeat_windows >= 2:
                self._state = WebSocketConnectionState.RECONNECTING
                raise ConnectionError("Bitget websocket heartbeat acknowledgement missing") from exc
            self._state = WebSocketConnectionState.STALE
            await connection.send("ping")
            self._last_ping_at = self._clock()
            return []
        except Exception:
            self._state = WebSocketConnectionState.RECONNECTING
            raise
        if isinstance(raw, str) and raw.strip() == "pong":
            self._last_pong_at = self._clock()
            self._last_event_at = self._last_pong_at
            self._missed_heartbeat_windows = 0
            self._state = WebSocketConnectionState.CONNECTED
            return []
        try:
            events = normalize_ws_message(raw)
        except Exception:
            self._state = WebSocketConnectionState.RECONNECTING
            raise
        now = self._clock()
        self._last_event_at = now
        for event in events:
            if event.kind == "mark_price":
                self._last_mark_event_at[event.symbol] = now
        self._state = WebSocketConnectionState.CONNECTED
        return events

    def check_freshness(self, symbol: str | None = None) -> bool:
        """Mark the stream stale when no valid frame arrived within the bound."""
        if symbol is not None:
            normalized_symbol = symbol.strip().upper()
            if normalized_symbol not in self._symbols:
                return False
            age = self.last_mark_event_age(normalized_symbol)
            return (
                self._state is WebSocketConnectionState.CONNECTED
                and age is not None
                and age <= self._stale_after
            )
        fresh = self._state is WebSocketConnectionState.CONNECTED and all(
            (age := self.last_mark_event_age(symbol)) is not None and age <= self._stale_after
            for symbol in self._symbols
        )
        if not fresh and self._state in {
            WebSocketConnectionState.CONNECTED,
            WebSocketConnectionState.STALE,
        }:
            self._state = WebSocketConnectionState.STALE
        return fresh

    async def send_heartbeat_if_due(self) -> bool:
        """Send the Classic text ``ping`` when its heartbeat interval elapses."""
        if self._connection is None or self._state not in {
            WebSocketConnectionState.CONNECTED,
            WebSocketConnectionState.STALE,
        }:
            return False
        now = self._clock()
        if self._last_ping_at is not None and now - self._last_ping_at < self._heartbeat_interval:
            return False
        await self._connection.send("ping")
        self._last_ping_at = now
        return True

    def reconnect_delay(self, attempt: int) -> float:
        """Return capped exponential backoff without mutating transport state."""
        if attempt < 1:
            raise ValueError("reconnect attempt must be positive")
        delay = self._reconnect_base_delay * float(2 ** (attempt - 1))
        return min(
            self._reconnect_max_delay,
            delay,
        )

    async def run(
        self,
        on_event: Callable[[BitgetWebSocketEvent], Awaitable[None]],
        stop_event: asyncio.Event,
    ) -> None:
        """Run the reader until stopped, reconnecting after protocol/transport errors."""
        attempt = 0
        try:
            while not stop_event.is_set():
                if self._connection is None or self._state in {
                    WebSocketConnectionState.DISCONNECTED,
                    WebSocketConnectionState.RECONNECTING,
                    WebSocketConnectionState.FAILED,
                }:
                    try:
                        if attempt:
                            await asyncio.sleep(self.reconnect_delay(attempt))
                        await self.connect()
                        attempt = 0
                    except Exception:
                        attempt += 1
                        continue
                try:
                    await self.send_heartbeat_if_due()
                    events = await self.receive_once()
                    for event in events:
                        await on_event(event)
                    self.check_freshness()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    attempt += 1
                    await self._close_connection()
                    self._state = WebSocketConnectionState.RECONNECTING
        finally:
            await self.close()

    async def _receive_login_ack(self, connection: WebSocketConnection) -> None:
        raw = await connection.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise WebSocketProtocolError("Bitget websocket login response is invalid") from exc
        if not isinstance(payload, dict):
            raise WebSocketProtocolError("Bitget websocket login response is invalid")
        if payload.get("event") == "error":
            code = str(payload.get("code", ""))
            raise WebSocketProtocolError(
                f"Bitget websocket login failed: {code}", code=code or None
            )
        if payload.get("event") != "login" or str(payload.get("code", "")) not in {"0", "00000"}:
            raise WebSocketProtocolError("Bitget websocket login was not acknowledged")

    async def _close_connection(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.close()

    @staticmethod
    def _normalize_symbols(symbols: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for raw_symbol in symbols:
            symbol = str(raw_symbol).strip().upper()
            if not symbol:
                raise ValueError("Bitget websocket symbol is required")
            if symbol not in normalized:
                normalized.append(symbol)
        return tuple(normalized)


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)
