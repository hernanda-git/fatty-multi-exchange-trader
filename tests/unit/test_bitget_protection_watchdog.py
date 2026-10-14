"""Offline tests for the Bitget protection REST watchdog."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fatty_trader.exchanges.bitget.protection_capability import (
    BitgetProtectionCapability,
    NativeProtectionState,
    StreamState,
)
from fatty_trader.exchanges.bitget.websocket import WebSocketConnectionState
from fatty_trader.execution.bitget_protection_watchdog import (
    BitgetProtectionWatchdog,
    WatchdogStatus,
)
from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository

_NOW = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)


class FakeSocket:
    """Socket double for the per-symbol freshness tests.

    These tests exercise staleness and provider reads, so the socket reports a
    live connection by default. `state` is a plain attribute because the
    watchdog reads it with `getattr(socket, "state", None)` and treats its
    absence as a dead socket (fail closed).
    """

    def __init__(
        self,
        fresh: bool | dict[str, bool],
        *,
        state: WebSocketConnectionState = (WebSocketConnectionState.CONNECTED),
    ) -> None:
        self.fresh = fresh
        self.state = state

    def check_freshness(self, symbol: str | None = None) -> bool:
        if isinstance(self.fresh, dict):
            return bool(symbol and self.fresh.get(symbol, False))
        return self.fresh


@pytest.fixture
def repository() -> InMemoryProtectionCapabilityRepository:
    repository = InMemoryProtectionCapabilityRepository()
    repository.upsert(
        BitgetProtectionCapability(
            exchange="bitget",
            environment="LIVE",
            symbol="BTCUSDT",
            native_state=NativeProtectionState.UNSUPPORTED,
            fallback_allowed=True,
            stream_state=StreamState.HEALTHY,
            last_stream_at=_NOW,
        )
    )
    return repository


@pytest.mark.asyncio
async def test_healthy_stream_and_rest_read_allow_symbol(
    repository: InMemoryProtectionCapabilityRepository,
) -> None:
    reads: list[str] = []

    async def read_position(symbol: str) -> list[dict[str, str]]:
        reads.append(symbol)
        return []

    watchdog = BitgetProtectionWatchdog(
        FakeSocket(True),
        repository,
        environment="LIVE",
        symbols=["BTCUSDT"],
        read_position=read_position,
        now=lambda: _NOW,
    )

    report = await watchdog.run_once()

    assert report.status is WatchdogStatus.HEALTHY
    assert report.allow_new_entries == {"BTCUSDT": True}
    assert reads == ["BTCUSDT"]


@pytest.mark.asyncio
async def test_connecting_socket_with_empty_symbols_is_not_healthy() -> None:
    repository = InMemoryProtectionCapabilityRepository()

    async def read_position(_: str) -> list[dict[str, str]]:
        return []

    watchdog = BitgetProtectionWatchdog(
        FakeSocket(True, state=WebSocketConnectionState.CONNECTING),
        repository,
        environment="LIVE",
        symbols=[],
        read_position=read_position,
        now=lambda: _NOW,
    )
    report = await watchdog.run_once()
    assert report.status is WatchdogStatus.FAILED
    assert report.reasons == ("socket-not-connected",)
    assert report.allow_new_entries == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["lookup", "insert", "update", "latch-read", "latch-write"])
async def test_watchdog_storage_does_not_latch_monitor_for_clock_unavailability(
    monkeypatch, operation
) -> None:
    import asyncio
    import threading
    import time

    from fatty_trader.exchanges.bitget.client import BitgetRestClient
    from fatty_trader.execution.bitget_monitor import BitgetMonitor
    from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository

    entered = threading.Event()
    release = threading.Event()
    request_started = asyncio.Event()
    respond = asyncio.Event()
    loop_thread = threading.get_ident()
    repository = InMemoryProtectionCapabilityRepository()
    kill_switch = InMemoryReconciliationRepository()

    class Socket(FakeSocket):
        def check_freshness(self, symbol=None):
            assert threading.get_ident() == loop_thread
            return super().check_freshness(symbol)

    socket = Socket(True)
    symbols = ["BTCUSDT"]
    if operation.startswith("latch-"):
        socket.state = WebSocketConnectionState.DISCONNECTED
        symbols = []
        target = kill_switch
        method = "is_active" if operation == "latch-read" else "latch_kill_switch"
    else:
        target = repository
        method = {"lookup": "get", "insert": "upsert", "update": "update_stream"}[operation]
    original = getattr(target, method)

    def block_store(*args, **kwargs):
        entered.set()
        release.wait(timeout=2)
        return original(*args, **kwargs)

    monkeypatch.setattr(target, method, block_store)

    async def read_position(symbol):
        return []

    async def read_only_response(method, path, **kwargs):
        assert method == "GET"
        if path == "/api/v2/public/time":
            request_started.set()
            await respond.wait()
            return {"serverTime": int(time.time() * 1000)}
        return []

    client = BitgetRestClient("", "", "")
    monkeypatch.setattr(client, "_request", read_only_response)
    monitor_repository = InMemoryReconciliationRepository()
    watchdog = BitgetProtectionWatchdog(
        socket,
        repository,
        environment="LIVE",
        symbols=symbols,
        read_position=read_position,
        now=lambda: _NOW,
        kill_switch=kill_switch,
        stale_cycles_before_latch=1,
    )
    monitor_task = asyncio.create_task(BitgetMonitor(client, monitor_repository).run_once())
    watchdog_task = None
    try:
        await asyncio.wait_for(request_started.wait(), timeout=1)
        watchdog_task = asyncio.create_task(watchdog.run_once())
        assert await asyncio.to_thread(entered.wait, 1)
        respond.set()
        report = await asyncio.wait_for(monitor_task, timeout=1)
        assert report.status == "ok"
        assert not monitor_repository.kill_switch_active("bitget")
        assert not watchdog_task.done(), "watchdog storage must not delay the monitor clock"
    finally:
        release.set()
        respond.set()
        await asyncio.gather(
            monitor_task, *([watchdog_task] if watchdog_task else []), return_exceptions=True
        )
        await client.aclose()


@pytest.mark.asyncio
async def test_stale_stream_blocks_symbol_and_marks_capability_stale(
    repository: InMemoryProtectionCapabilityRepository,
) -> None:
    async def read_position(_: str) -> list[dict[str, str]]:
        return []

    watchdog = BitgetProtectionWatchdog(
        FakeSocket(False),
        repository,
        environment="LIVE",
        symbols=["BTCUSDT"],
        read_position=read_position,
        now=lambda: _NOW,
    )

    report = await watchdog.run_once()

    assert report.status is WatchdogStatus.STALE
    assert report.allow_new_entries == {"BTCUSDT": False}
    assert report.reasons == ("protection-stream-stale",)
    capability = repository.get("bitget", "LIVE", "BTCUSDT")
    assert capability is not None
    assert capability.stream_state is StreamState.STALE


@pytest.mark.asyncio
async def test_provider_read_failure_blocks_symbol_without_latching_global_switch(
    repository: InMemoryProtectionCapabilityRepository,
    caplog,
) -> None:
    async def read_position(_: str) -> list[dict[str, str]]:
        raise TimeoutError("provider unavailable")

    watchdog = BitgetProtectionWatchdog(
        FakeSocket(True),
        repository,
        environment="LIVE",
        symbols=["BTCUSDT"],
        read_position=read_position,
        now=lambda: _NOW,
    )

    report = await watchdog.run_once()

    assert report.status is WatchdogStatus.FAILED
    assert report.allow_new_entries == {"BTCUSDT": False}
    assert report.reasons == ("provider-position-read-failed",)
    assert not hasattr(repository, "latch_kill_switch")
    assert "reason=provider-position-read-failed exception=TimeoutError" in caplog.text
    assert "provider unavailable" not in caplog.text


@pytest.mark.asyncio
async def test_provider_read_failure_blocks_even_native_verified_symbol(
    repository: InMemoryProtectionCapabilityRepository,
) -> None:
    repository.upsert(
        BitgetProtectionCapability(
            exchange="bitget",
            environment="LIVE",
            symbol="ETHUSDT",
            native_state=NativeProtectionState.VERIFIED,
        )
    )

    async def read_position(_: str) -> list[dict[str, str]]:
        raise TimeoutError("provider unavailable")

    watchdog = BitgetProtectionWatchdog(
        FakeSocket(True),
        repository,
        environment="LIVE",
        symbols=["ETHUSDT"],
        read_position=read_position,
        now=lambda: _NOW,
    )

    report = await watchdog.run_once()

    assert report.status is WatchdogStatus.FAILED
    assert report.allow_new_entries == {"ETHUSDT": False}


@pytest.mark.asyncio
async def test_stale_stream_is_local_to_the_affected_symbol() -> None:
    repository = InMemoryProtectionCapabilityRepository()
    for symbol in ("BTCUSDT", "ETHUSDT"):
        repository.upsert(
            BitgetProtectionCapability(
                exchange="bitget",
                environment="LIVE",
                symbol=symbol,
                native_state=NativeProtectionState.UNSUPPORTED,
                fallback_allowed=True,
                stream_state=StreamState.HEALTHY,
                last_stream_at=_NOW,
            )
        )

    async def read_position(_: str) -> list[dict[str, str]]:
        return []

    watchdog = BitgetProtectionWatchdog(
        FakeSocket({"BTCUSDT": False, "ETHUSDT": True}),
        repository,
        environment="LIVE",
        symbols=["BTCUSDT", "ETHUSDT"],
        read_position=read_position,
        now=lambda: _NOW,
    )

    report = await watchdog.run_once()

    assert report.status is WatchdogStatus.STALE
    assert report.allow_new_entries == {"BTCUSDT": False, "ETHUSDT": True}
    assert report.reasons == ("protection-stream-stale",)


@pytest.mark.asyncio
async def test_fresh_stream_does_not_grant_unallowlisted_fallback():
    repository = InMemoryProtectionCapabilityRepository()
    repository.upsert(
        BitgetProtectionCapability(
            exchange="bitget",
            environment="LIVE",
            symbol="BTCUSDT",
            fallback_allowed=False,
            stream_state=StreamState.HEALTHY,
            last_stream_at=_NOW,
        )
    )

    async def read_position(_symbol):
        return []

    report = await BitgetProtectionWatchdog(
        FakeSocket(True),
        repository,
        environment="LIVE",
        symbols=["BTCUSDT"],
        read_position=read_position,
        now=lambda: _NOW,
    ).run_once()
    assert report.allow_new_entries == {"BTCUSDT": False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [{"symbol": "BTCUSDT"}],
        [{"symbol": "BTCUSDT", "total": "NaN"}],
        [{"symbol": "BTCUSDT", "total": "Infinity"}],
        [{"symbol": "OTHERUSDT", "total": "1"}],
        [{"symbol": "BTCUSDT", "total": "-1"}],
    ],
)
async def test_invalid_provider_quantity_blocks_even_fresh_stream(repository, rows):
    async def read_position(_symbol):
        return rows

    report = await BitgetProtectionWatchdog(
        FakeSocket(True),
        repository,
        environment="LIVE",
        symbols=["BTCUSDT"],
        read_position=read_position,
        now=lambda: _NOW,
    ).run_once()
    assert report.status is WatchdogStatus.FAILED
    assert report.allow_new_entries == {"BTCUSDT": False}
    assert report.reasons == ("provider-position-invalid",)
