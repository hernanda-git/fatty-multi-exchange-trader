"""Offline tests for event-driven Bitget fallback protection."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.protection_capability import NativeProtectionState
from fatty_trader.exchanges.bitget.ws_models import BitgetWebSocketEvent
from fatty_trader.execution.bitget_protection_stream import (
    BitgetFallbackStreamEngine,
    BitgetProtectionStreamRuntime,
    StreamCloseRequest,
    StreamCloseResult,
)
from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository


@pytest.fixture
def active_entry() -> dict[str, object]:
    return {
        "id": "fallback-1",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "entry_price": Decimal("100"),
        "stop_loss": Decimal("95"),
        "take_profits": [Decimal("110")],
        "quantity": Decimal("0.01"),
        "state": "active",
    }


def mark(symbol: str, price: str, event_time_ms: int) -> BitgetWebSocketEvent:
    return BitgetWebSocketEvent(
        kind="mark_price",
        symbol=symbol,
        mark_price=Decimal(price),
        event_time_ms=event_time_ms,
    )


@pytest.mark.asyncio
async def test_mark_price_sl_event_creates_one_close_request(
    active_entry: dict[str, object],
) -> None:
    requests: list[StreamCloseRequest] = []

    async def close(request: StreamCloseRequest) -> StreamCloseResult:
        requests.append(request)
        return StreamCloseResult(request.fallback_id, True, "submitted")

    engine = BitgetFallbackStreamEngine(lambda: [active_entry], close)
    results = await engine.handle_event(mark("BTCUSDT", "94", 10))

    assert results == [StreamCloseResult("fallback-1", True, "submitted")]
    assert len(requests) == 1
    assert requests[0].reason == "sl_hit"
    assert requests[0].mark_price == Decimal("94")
    assert requests[0].quantity == Decimal("0.01")


@pytest.mark.asyncio
async def test_duplicate_or_newer_threshold_events_never_post_twice(
    active_entry: dict[str, object],
) -> None:
    calls: list[StreamCloseRequest] = []

    async def close(request: StreamCloseRequest) -> StreamCloseResult:
        calls.append(request)
        return StreamCloseResult(request.fallback_id, False, "close-result-unknown")

    engine = BitgetFallbackStreamEngine(lambda: [active_entry], close)
    first = await engine.handle_event(mark("BTCUSDT", "94", 10))
    duplicate = await engine.handle_event(mark("BTCUSDT", "93", 10))
    newer = await engine.handle_event(mark("BTCUSDT", "92", 11))

    assert len(first) == 1
    assert duplicate == []
    assert newer == []
    assert len(calls) == 1
    assert engine.pending_fallback_ids == frozenset({"fallback-1"})


@pytest.mark.asyncio
async def test_out_of_order_event_cannot_trigger_after_newer_event(
    active_entry: dict[str, object],
) -> None:
    calls: list[StreamCloseRequest] = []

    async def close(request: StreamCloseRequest) -> StreamCloseResult:
        calls.append(request)
        return StreamCloseResult(request.fallback_id, True, "submitted")

    engine = BitgetFallbackStreamEngine(lambda: [active_entry], close)
    assert await engine.handle_event(mark("BTCUSDT", "100", 20)) == []
    assert await engine.handle_event(mark("BTCUSDT", "94", 19)) == []
    assert calls == []
    assert await engine.handle_event(mark("BTCUSDT", "94", 21))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_non_mark_event_and_other_symbol_do_not_trigger(
    active_entry: dict[str, object],
) -> None:
    calls: list[StreamCloseRequest] = []

    async def close(request: StreamCloseRequest) -> StreamCloseResult:
        calls.append(request)
        return StreamCloseResult(request.fallback_id, True, "submitted")

    engine = BitgetFallbackStreamEngine(lambda: [active_entry], close)
    position_event = BitgetWebSocketEvent(
        kind="position",
        symbol="BTCUSDT",
        quantity=Decimal("0.01"),
        event_time_ms=10,
    )

    assert await engine.handle_event(position_event) == []
    assert await engine.handle_event(mark("ETHUSDT", "1", 11)) == []
    assert calls == []


@pytest.mark.asyncio
async def test_stream_runtime_marks_capability_healthy_on_mark_event() -> None:
    class FakeSocket:
        async def run(self, on_event: object, stop_event: asyncio.Event) -> None:
            del stop_event
            await on_event(mark("BTCUSDT", "100", 20))  # type: ignore[misc]

    class FakeRepository:
        def __init__(self) -> None:
            self.updates: list[tuple[str, str, str, object, datetime | None]] = []

        def update_stream(
            self,
            exchange: str,
            environment: str,
            symbol: str,
            *,
            state: object,
            last_stream_at: datetime | None,
            last_error: str | None = None,
        ) -> None:
            assert last_error is None
            self.updates.append((exchange, environment, symbol, state, last_stream_at))

    repository = FakeRepository()
    now = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)
    runtime = BitgetProtectionStreamRuntime(
        FakeSocket(),
        repository,
        environment="LIVE",
        now=lambda: now,  # type: ignore[arg-type]
    )

    await runtime.run(asyncio.Event())

    assert len(repository.updates) == 1
    assert repository.updates[0][0:3] == ("bitget", "LIVE", "BTCUSDT")
    assert str(repository.updates[0][3]) == "HEALTHY"
    assert repository.updates[0][4] == now


@pytest.mark.asyncio
async def test_stream_runtime_creates_unknown_capability_before_marking_stream_healthy() -> None:
    class FakeSocket:
        async def run(self, on_event: object, stop_event: asyncio.Event) -> None:
            del stop_event
            await on_event(mark("BTCUSDT", "100", 20))  # type: ignore[misc]

    repository = InMemoryProtectionCapabilityRepository()
    now = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)
    runtime = BitgetProtectionStreamRuntime(
        FakeSocket(),
        repository,
        environment="LIVE",
        now=lambda: now,  # type: ignore[arg-type]
    )

    await runtime.run(asyncio.Event())

    capability = repository.get("bitget", "LIVE", "BTCUSDT")
    assert capability is not None
    assert capability.native_state is NativeProtectionState.UNKNOWN
    assert str(capability.stream_state) == "HEALTHY"
    assert capability.last_stream_at == now


@pytest.mark.asyncio
async def test_stream_runtime_syncs_only_active_fallback_symbols() -> None:
    class FakeSocket:
        def __init__(self) -> None:
            self._symbols: tuple[str, ...] = ()
            self.subscribed: list[tuple[str, ...]] = []
            self.unsubscribed: list[tuple[str, ...]] = []

        @property
        def symbols(self) -> tuple[str, ...]:
            return self._symbols

        async def subscribe_symbols(self, symbols: tuple[str, ...]) -> tuple[str, ...]:
            additions = tuple(symbol for symbol in symbols if symbol not in self._symbols)
            self._symbols += additions
            self.subscribed.append(additions)
            return additions

        async def unsubscribe_symbols(self, symbols: tuple[str, ...]) -> tuple[str, ...]:
            removals = tuple(symbol for symbol in symbols if symbol in self._symbols)
            self._symbols = tuple(symbol for symbol in self._symbols if symbol not in removals)
            self.unsubscribed.append(removals)
            return removals

    socket = FakeSocket()
    repository = InMemoryProtectionCapabilityRepository()
    active = ["WLDUSDT"]
    runtime = BitgetProtectionStreamRuntime(
        socket, repository, environment="LIVE", active_symbol_source=lambda: active
    )

    assert await runtime.sync_active_symbols() == (("WLDUSDT",), ())
    active[:] = ["BTCUSDT"]
    assert await runtime.sync_active_symbols() == (("BTCUSDT",), ("WLDUSDT",))
    assert socket.symbols == ("BTCUSDT",)


@pytest.mark.asyncio
async def test_symbol_source_failure_preserves_existing_subscription() -> None:
    class FakeSocket:
        symbols = ("WLDUSDT",)

        async def subscribe_symbols(self, symbols: tuple[str, ...]) -> tuple[str, ...]:
            raise AssertionError("subscribe must not run after source failure")

        async def unsubscribe_symbols(self, symbols: tuple[str, ...]) -> tuple[str, ...]:
            raise AssertionError("unsubscribe must not run after source failure")

    def failing_source() -> list[str]:
        raise RuntimeError("temporary database failure")

    runtime = BitgetProtectionStreamRuntime(
        FakeSocket(),
        InMemoryProtectionCapabilityRepository(),
        environment="LIVE",
        active_symbol_source=failing_source,
    )

    assert await runtime.sync_active_symbols() == ((), ())


@pytest.mark.asyncio
async def test_symbol_iterator_failure_preserves_existing_subscription() -> None:
    class FakeSocket:
        symbols = ("WLDUSDT",)

        async def subscribe_symbols(self, symbols: tuple[str, ...]) -> tuple[str, ...]:
            raise AssertionError("subscribe must not run after iterator failure")

        async def unsubscribe_symbols(self, symbols: tuple[str, ...]) -> tuple[str, ...]:
            raise AssertionError("unsubscribe must not run after iterator failure")

    def failing_source():
        yield "BTCUSDT"
        raise RuntimeError("temporary iterator failure")

    runtime = BitgetProtectionStreamRuntime(
        FakeSocket(),
        InMemoryProtectionCapabilityRepository(),
        environment="LIVE",
        active_symbol_source=failing_source,
    )

    assert await runtime.sync_active_symbols() == ((), ())
