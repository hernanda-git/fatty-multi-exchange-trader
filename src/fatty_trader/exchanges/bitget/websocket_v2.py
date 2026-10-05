"""Bitget V2 dual-connection WebSocket transport.

Bitget v2 requires two distinct sockets: ticker lives only on the public
endpoint and positions/orders live only on the private endpoint. Subscribing
ticker on the private socket returns ``30016 Param error``; subscribing a
private channel on the public socket returns the same. That was verified
against the LIVE endpoints, so the split here is a protocol requirement, not
a design preference.

Failure is fail-closed by construction. Liveness is judged PER LEG:

* The public leg must be authenticated-by-subscription and delivering fresh
  mark events for every subscribed symbol.
* The private leg must have an acknowledged login AND have been alive within
  ``_private_silent_after``. The private leg is quiet by nature, so liveness is
  proven by the bare-text ``pong`` answering our heartbeat, not by expecting
  unsolicited frames.

A live public ticker with a silent or unauthenticated private leg is NOT
healthy. Mark price alone cannot protect a position, and an order or
position-change event that silently stopped arriving is worse than a total
outage, because nothing downstream would notice.
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


class _LegError:
    """A failure raised by a background leg reader, tagged with its leg."""

    def __init__(self, leg: str, error: BaseException) -> None:
        self.leg = leg
        self.error = error

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"_LegError(leg={self.leg!r}, error={self.error!r})"


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
        private_silent_after: float | None = None,
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
        # The private leg is expected to be quiet, so allow a missed heartbeat
        # plus slack before declaring it dead. Default to 2.5 heartbeat windows.
        self._private_silent_after = (
            private_silent_after if private_silent_after is not None else heartbeat_interval * 2.5
        )
        if self._private_silent_after <= 0:
            raise ValueError("Bitget v2 websocket private_silent_after must be positive")

        self._public: WebSocketConnection | None = None
        self._private: WebSocketConnection | None = None
        self._state = V2ConnectionState.DISCONNECTED
        self._last_event_at: float | None = None
        self._last_mark_event_at: dict[str, float] = {}
        self._last_mark_event_ms: dict[str, int] = {}
        self._last_pong_at: float | None = None
        self._last_ping_at: float | None = None
        # Per-leg liveness. The private leg is only "live" once its login has
        # been acknowledged AND it has been heard from within the bound.
        self._private_authenticated = False
        self._last_private_activity_at: float | None = None
        self._readers: list[asyncio.Task[None]] = []
        self._queues: dict[str, asyncio.Queue[Any]] = {}
        self._any_event = asyncio.Event()
        self._reader_error: _LegError | None = None
        self._pending_error: _LegError | None = None

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
    def private_authenticated(self) -> bool:
        return self._private_authenticated

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
        self._private_authenticated = False
        try:
            self._private_subscription_args()  # fail before opening sockets
            public = await self._transport.connect(self._public_url)
            self._public = public
            private = await self._transport.connect(self._private_url)
            self._private = private

            # A flat fallback registry needs private account updates but no tickers.
            # Bitget rejects a public subscribe with args=[] (30002), causing a
            # perpetual reconnect loop before the first protected entry arrives.
            if self._symbols:
                await public.send(_json(build_v2_public_subscription(self._symbols)))
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
            self._private_authenticated = True
            await private.send(_json(build_v2_private_subscription()))
        except Exception:
            # Never leave a half-open pair looking healthy.
            self._state = V2ConnectionState.FAILED
            self._private_authenticated = False
            await self._close_public()
            await self._close_private()
            raise

        now = self._clock()
        self._last_event_at = now
        self._last_mark_event_at = {}
        self._last_ping_at = now
        self._last_pong_at = now
        self._last_private_activity_at = now
        self._state = V2ConnectionState.CONNECTED
        self._start_readers()

    async def reconnect(self) -> None:
        self._state = V2ConnectionState.RECONNECTING
        await self._close_public()
        await self._close_private()
        await self.connect()

    async def close(self) -> None:
        await self._close_public()
        await self._close_private()
        self._private_authenticated = False
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
        """Drain both legs' queues, waiting for the next event on either.

        Each leg has a dedicated long-lived reader task pushing into its own
        queue, so a frame that arrived while we were handling the previous
        batch is queued rather than discarded. An earlier version raced two
        ``recv()`` calls and cancelled the loser, which silently threw away
        private position/order frames that happened to land concurrently.
        A safety event lost that way is an unprotected stop.
        """
        if not self._both_legs_open() or not self._readers:
            raise RuntimeError("Bitget v2 websocket is not connected")
        self._any_event.clear()
        # Re-check after clearing: an event may have arrived in between.
        ready = self._drain_ready()
        if ready:
            return ready
        # Quiet accounts still need pings. Waiting for a frame for two heartbeat
        # windows before sending the next ping starves both otherwise healthy legs.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._heartbeat_interval * 2
        while not self._any_event.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"Bitget v2 websocket produced no frame for "
                    f"{self._heartbeat_interval * 2:.0f}s on either leg"
                )
            try:
                async with asyncio.timeout(min(remaining, self._heartbeat_interval)):
                    await self._any_event.wait()
            except TimeoutError:
                await self.send_heartbeat_if_due()
        return self._drain_ready()

    async def send_heartbeat_if_due(self) -> bool:
        """Send the bare text ``ping`` that v2 answers with ``pong``.

        A send failure is NOT swallowed. Swallowing it would stamp a fresh
        ``_last_ping_at`` on a leg that is already gone and hide the only
        evidence distinguishing "quiet" from "dead".
        """
        public, private = self._public, self._private
        if public is None or private is None:
            return False
        now = self._clock()
        if self._last_ping_at is not None and now - self._last_ping_at < self._heartbeat_interval:
            return False
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
                    # The freshness verdict must DRIVE the reconnect path. A
                    # stale stream that is still quietly delivering frames
                    # would otherwise never heal.
                    if not self.check_freshness():
                        reason = self.stale_reason()
                        attempt += 1
                        print(
                            f"component=bitget-ws-v2 state=stale "
                            f"public={self._public_url} private={self._private_url} "
                            f"consecutive_failures={attempt} reason={reason}",
                            flush=True,
                        )
                        await self._close_public()
                        await self._close_private()
                        self._state = V2ConnectionState.RECONNECTING
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    attempt += 1
                    print(
                        f"component=bitget-ws-v2 state=read_error "
                        f"consecutive_failures={attempt} "
                        f"error_type={type(exc).__name__} error={exc}",
                        flush=True,
                    )
                    await self._close_public()
                    await self._close_private()
                    self._state = V2ConnectionState.RECONNECTING
        finally:
            await self.close()

    def stale_reason(self) -> str:
        """Human-readable reason for the current non-healthy verdict."""
        if self._reader_error is not None:
            return f"reader-error:{self._reader_error.leg}"
        if not self._both_legs_open():
            return "leg-closed"
        if not self._private_authenticated:
            return "private-unauthenticated"
        if self._private_silent_age() is not None and (
            self._private_silent_age() > self._private_silent_after  # type: ignore[operator]
        ):
            return "private-silent"
        for symbol in self._symbols:
            age = self.last_mark_event_age(symbol)
            if age is None:
                return f"no-mark-event:{symbol}"
            if age > self._stale_after:
                return f"stale-mark:{symbol}"
        return "fresh"

    def _private_silent_age(self) -> float | None:
        if self._last_private_activity_at is None:
            return None
        return max(0.0, self._clock() - self._last_private_activity_at)

    def check_freshness(self, symbol: str | None = None) -> bool:
        """Fresh requires BOTH legs live AND fresh public mark events.

        A live public ticker is not sufficient: the private leg carries
        positions and orders, so an unauthenticated or silent private leg
        means we cannot see the account state we are supposed to protect.
        """
        if self._reader_error is not None:
            return False
        if not self._both_legs_open():
            if self._state is not V2ConnectionState.DISCONNECTED:
                self._state = V2ConnectionState.FAILED
            return False
        if not self._private_leg_live():
            if self._state is V2ConnectionState.CONNECTED:
                self._state = V2ConnectionState.STALE
            return False
        if symbol is not None:
            normalized = symbol.strip().upper()
            if normalized not in self._symbols:
                return False
            age = self.last_mark_event_age(normalized)
            return age is not None and age <= self._stale_after
        fresh = all(
            (age := self.last_mark_event_age(sym)) is not None and age <= self._stale_after
            for sym in self._symbols
        )
        if fresh and self._state is V2ConnectionState.STALE:
            # Recovery must be observable: a stale verdict is not a latch.
            self._state = V2ConnectionState.CONNECTED
        if not fresh and self._state in {V2ConnectionState.CONNECTED, V2ConnectionState.STALE}:
            self._state = V2ConnectionState.STALE
        return fresh

    # --- internals ---------------------------------------------------------

    def _private_leg_live(self) -> bool:
        """Private leg must be authenticated AND recently heard from."""
        if self._private is None or not self._private_authenticated:
            return False
        age = self._private_silent_age()
        return age is not None and age <= self._private_silent_after

    def _both_legs_open(self) -> bool:
        return self._public is not None and self._private is not None

    def _start_readers(self) -> None:
        self._reader_error = None
        self._pending_error = None
        self._queues = {"public": asyncio.Queue(), "private": asyncio.Queue()}
        self._any_event = asyncio.Event()
        self._readers = [
            asyncio.ensure_future(self._reader_loop("public", self._public)),
            asyncio.ensure_future(self._reader_loop("private", self._private)),
        ]

    def _drain_ready(self) -> list[BitgetWebSocketEvent]:
        """Deliver both legs' queued observations once, then raise the error.

        A terminal marker must not discard earlier observations or the other
        leg's finite batch. Health is fail-closed during observation delivery.
        """
        if self._pending_error is not None:
            raise self._pending_error.error
        events: list[BitgetWebSocketEvent] = []
        for leg in ("public", "private"):
            queue = self._queues.get(leg)
            if queue is None:
                continue
            while not queue.empty():
                item = queue.get_nowait()
                if isinstance(item, _LegError):
                    self._record_reader_error(item)
                    if self._pending_error is None:
                        self._pending_error = item
                else:
                    events.extend(item)
        if not events and self._pending_error is not None:
            raise self._pending_error.error
        return events

    def _record_reader_error(self, error: _LegError) -> None:
        if self._reader_error is None:
            self._reader_error = error
        self._state = V2ConnectionState.RECONNECTING

    async def _reader_loop(self, leg: str, connection: WebSocketConnection | None) -> None:
        """Long-lived per-leg reader. Never cancels an in-flight recv()."""
        if connection is None:
            error = _LegError(leg, RuntimeError(f"Bitget v2 {leg} socket is closed"))
            self._record_reader_error(error)
            self._queues.setdefault(leg, asyncio.Queue()).put_nowait(error)
            self._any_event.set()
            return
        queue = self._queues[leg]
        try:
            while True:
                raw = await connection.recv()
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                now = self._clock()
                self._last_event_at = now
                if isinstance(raw, str) and raw.strip() == "pong":
                    self._last_pong_at = now
                    if leg == "private":
                        self._last_private_activity_at = now
                    # Wake the main receive loop even with no business events;
                    # a pong is the account stream's idle liveness evidence.
                    self._any_event.set()
                    continue
                events = normalize_v2_frame(raw)
                if leg == "private":
                    # Any private frame proves the socket is alive.
                    self._last_private_activity_at = now
                from fatty_trader.exchanges.bitget.websocket import fresh_mark

                accepted = []
                for event in events:
                    if event.kind == "mark_price":
                        if leg != "public" or not fresh_mark(
                            event,
                            self._wall_clock(),
                            self._stale_after,
                            self._last_mark_event_ms.get(event.symbol),
                        ):
                            continue
                        self._last_mark_event_ms[event.symbol] = event.event_time_ms
                        self._last_mark_event_at[event.symbol] = now - (
                            self._wall_clock() - event.event_time_ms / 1000
                        )
                    accepted.append(event)
                events = accepted
                if (
                    events
                    and self._state is V2ConnectionState.RECONNECTING
                    and self._reader_error is None
                ):
                    self._state = V2ConnectionState.CONNECTED
                if events:
                    queue.put_nowait(events)
                    self._any_event.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = _LegError(leg, exc)
            self._record_reader_error(error)
            queue.put_nowait(error)
            self._any_event.set()

    async def _close_public(self) -> None:
        await self._stop_readers()
        connection, self._public = self._public, None
        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.close()

    async def _close_private(self) -> None:
        await self._stop_readers()
        connection, self._private = self._private, None
        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.close()

    async def _stop_readers(self) -> None:
        readers, self._readers = self._readers, []
        for task in readers:
            task.cancel()
        if readers:
            await asyncio.gather(*readers, return_exceptions=True)

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
