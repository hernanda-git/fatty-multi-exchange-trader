"""Offline tests for the Bitget protection REST watchdog."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fatty_trader.exchanges.bitget.protection_capability import (
    BitgetProtectionCapability,
    NativeProtectionState,
    StreamState,
)
from fatty_trader.execution.bitget_protection_watchdog import (
    BitgetProtectionWatchdog,
    WatchdogStatus,
)
from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository

_NOW = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)


class FakeSocket:
    def __init__(self, fresh: bool | dict[str, bool]) -> None:
        self.fresh = fresh

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
