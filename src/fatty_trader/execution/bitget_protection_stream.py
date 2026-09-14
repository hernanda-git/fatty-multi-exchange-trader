"""Event-driven fallback protection engine, isolated from transport and persistence."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fatty_trader.exchanges.bitget.protection_capability import StreamState
from fatty_trader.exchanges.bitget.websocket import BitgetClassicWebSocket
from fatty_trader.exchanges.bitget.ws_models import BitgetWebSocketEvent
from fatty_trader.execution.bitget_fallback_protection import check_thresholds

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreamCloseRequest:
    fallback_id: str
    symbol: str
    direction: str
    mark_price: Decimal
    reason: str
    quantity: Decimal


@dataclass(frozen=True)
class StreamCloseResult:
    fallback_id: str
    submitted: bool
    reason: str


CloseCallback = Callable[[StreamCloseRequest], Awaitable[StreamCloseResult]]


class BitgetFallbackStreamEngine:
    """Evaluate fallback thresholds on fresh mark events with duplicate fencing."""

    def __init__(
        self,
        entries: Callable[[], Iterable[Mapping[str, Any]]],
        close: CloseCallback,
    ) -> None:
        self._entries = entries
        self._close = close
        self._last_event_ms: dict[str, int] = {}
        self._pending_fallback_ids: set[str] = set()

    @property
    def pending_fallback_ids(self) -> frozenset[str]:
        return frozenset(self._pending_fallback_ids)

    def last_event_ms(self, symbol: str) -> int | None:
        return self._last_event_ms.get(symbol.strip().upper())

    async def handle_event(self, event: BitgetWebSocketEvent) -> list[StreamCloseResult]:
        """Process one normalized event; only mark-price events can trigger a close."""
        if event.kind != "mark_price" or event.mark_price is None:
            return []
        symbol = event.symbol.strip().upper()
        previous = self._last_event_ms.get(symbol)
        if previous is not None and event.event_time_ms <= previous:
            return []
        self._last_event_ms[symbol] = event.event_time_ms

        results: list[StreamCloseResult] = []
        for raw_entry in self._entries():
            entry = _normalize_entry(raw_entry)
            if entry is None or entry["symbol"] != symbol or entry["state"] != "active":
                continue
            fallback_id = entry["id"]
            if fallback_id in self._pending_fallback_ids:
                continue
            should_close, reason = check_thresholds(
                entry["direction"],
                event.mark_price,
                entry["entry_price"],
                entry["stop_loss"],
                entry["take_profits"],
            )
            if not should_close:
                continue
            self._pending_fallback_ids.add(fallback_id)
            request = StreamCloseRequest(
                fallback_id=fallback_id,
                symbol=symbol,
                direction=entry["direction"],
                mark_price=event.mark_price,
                reason=reason,
                quantity=entry["quantity"],
            )
            try:
                result = await self._close(request)
            except Exception:
                # Keep the fence set: an unknown close result must be reconciled, not retried.
                result = StreamCloseResult(fallback_id, False, "close-result-unknown")
            results.append(result)
        return results


class BitgetProtectionStreamRuntime:
    """Observe-only bridge from the socket to per-symbol stream capability state."""

    def __init__(
        self,
        socket: BitgetClassicWebSocket,
        repository: Any,
        *,
        environment: str,
        now: Callable[[], datetime] | None = None,
        active_symbol_source: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self._socket = socket
        self._repository = repository
        self._environment = environment.strip().upper()
        if self._environment not in {"DEMO", "LIVE"}:
            raise ValueError("Bitget protection stream environment must be DEMO or LIVE")
        self._now = now or (lambda: datetime.now(UTC))
        self._active_symbol_source = active_symbol_source

    @property
    def socket(self) -> BitgetClassicWebSocket:
        """Expose the transport state to the paired REST watchdog."""
        return self._socket

    @property
    def repository(self) -> Any:
        """Return the capability store shared with admission and watchdogs."""
        return self._repository

    @property
    def symbols(self) -> tuple[str, ...]:
        """Return the exact normalized symbol subscription."""
        return self._socket.symbols

    async def run(self, stop_event: Any) -> None:
        """Run the reader and sync ticker subscriptions to active fallback positions."""
        if self._active_symbol_source is None:
            await self._socket.run(self._on_event, stop_event)
            return
        reader = asyncio.create_task(self._socket.run(self._on_event, stop_event))
        try:
            while not stop_event.is_set():
                await self.sync_active_symbols()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=1.0)
        finally:
            if not reader.done():
                reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    async def sync_active_symbols(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Subscribe active fallback pairs and remove pairs that are no longer active."""
        if self._active_symbol_source is None:
            return (), ()
        try:
            active_symbols = tuple(
                dict.fromkeys(
                    symbol.strip().upper()
                    for symbol in self._active_symbol_source()
                    if str(symbol).strip()
                )
            )
        except Exception as exc:
            logger.warning(
                "Bitget protection stream active-symbol read failed: %s",
                type(exc).__name__,
            )
            return (), ()
        symbols = active_symbols
        subscribed = await self._socket.subscribe_symbols(symbols)
        current = set(self._socket.symbols)
        stale = tuple(symbol for symbol in current if symbol not in symbols)
        unsubscribed = await self._socket.unsubscribe_symbols(stale)
        return subscribed, unsubscribed

    async def _on_event(self, event: BitgetWebSocketEvent) -> None:
        if event.kind != "mark_price":
            return
        get_capability = getattr(self._repository, "get", None)
        upsert_capability = getattr(self._repository, "upsert", None)
        if (
            callable(get_capability)
            and callable(upsert_capability)
            and get_capability("bitget", self._environment, event.symbol) is None
        ):
            from fatty_trader.exchanges.bitget.protection_capability import (
                BitgetProtectionCapability,
            )

            upsert_capability(
                BitgetProtectionCapability(
                    exchange="bitget",
                    environment=self._environment,
                    symbol=event.symbol,
                )
            )
        self._repository.update_stream(
            "bitget",
            self._environment,
            event.symbol,
            state=StreamState.HEALTHY,
            last_stream_at=self._now(),
            last_error=None,
        )


def _normalize_entry(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        fallback_id = str(raw["id"]).strip()
        symbol = str(raw["symbol"]).strip().upper()
        direction = str(raw["direction"]).strip().upper()
        entry_price = Decimal(str(raw["entry_price"]))
        stop_loss = Decimal(str(raw["stop_loss"]))
        quantity = Decimal(str(raw["quantity"]))
        take_profits = tuple(Decimal(str(value)) for value in raw.get("take_profits", []))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None
    if (
        not fallback_id
        or not symbol
        or direction not in {"LONG", "SHORT"}
        or entry_price <= 0
        or stop_loss <= 0
        or quantity <= 0
        or any(value <= 0 for value in take_profits)
    ):
        return None
    return {
        "id": fallback_id,
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profits": take_profits,
        "quantity": quantity,
        "state": str(raw.get("state", "active")).strip().lower(),
    }
