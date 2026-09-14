"""Offline tests for the Bitget monitor lifecycle wiring."""

from __future__ import annotations

import asyncio

import pytest

from fatty_trader.execution.bitget_protection_watchdog import WatchdogReport, WatchdogStatus
from fatty_trader.service import (
    build_bitget_monitor_protection,
    run_bitget_monitor_loop,
)
from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository


class FakeMonitor:
    def __init__(self) -> None:
        self.calls = 0

    async def run_once(self) -> object:
        self.calls += 1
        return object()


class FakeStream:
    def __init__(self) -> None:
        self.started = False

    async def run(self, stop_event: asyncio.Event) -> None:
        self.started = True
        await stop_event.wait()


class FakeWatchdog:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.calls = 0
        self._stop_event = stop_event

    async def run_once(self) -> WatchdogReport:
        self.calls += 1
        self._stop_event.set()
        return WatchdogReport(WatchdogStatus.HEALTHY, {})


class FakeClient:
    async def get_single_position(self, _: str) -> list[dict[str, str]]:
        return []


@pytest.mark.asyncio
async def test_monitor_loop_starts_stream_and_watchdog_and_stops_cleanly() -> None:
    stop_event = asyncio.Event()
    monitor = FakeMonitor()
    stream = FakeStream()
    watchdog = FakeWatchdog(stop_event)

    await run_bitget_monitor_loop(
        monitor,
        interval=60,
        stream=stream,
        watchdog=watchdog,
        watchdog_interval=60,
        stop_event=stop_event,
    )

    assert stream.started is True
    assert watchdog.calls == 1
    assert monitor.calls >= 1


@pytest.mark.asyncio
async def test_monitor_loop_rejects_non_positive_intervals() -> None:
    with pytest.raises(ValueError, match="interval"):
        await run_bitget_monitor_loop(FakeMonitor(), interval=0)


def test_monitor_protection_components_are_disabled_without_explicit_stream_flag() -> None:
    stream, watchdog, watchdog_interval = build_bitget_monitor_protection(
        {},
        object(),
        repository=InMemoryProtectionCapabilityRepository(),
    )

    assert stream is None
    assert watchdog is None
    assert watchdog_interval is None


def test_monitor_protection_components_share_stream_repository_when_enabled() -> None:
    repository = InMemoryProtectionCapabilityRepository()
    stream, watchdog, watchdog_interval = build_bitget_monitor_protection(
        {
            "BITGET_PROTECTION_STREAM_ENABLED": "1",
            "BITGET_PROTECTION_STREAM_MODE": "observe",
            "BITGET_PROTECTION_STREAM_SYMBOLS": "BTCUSDT",
            "BITGET_PROTECTION_STREAM_STALE_SECONDS": "5",
            "BITGET_PROTECTION_STREAM_HEARTBEAT_SECONDS": "25",
            "BITGET_PROTECTION_REST_WATCHDOG_SECONDS": "4",
            "BITGET_API_KEY": "key",
            "BITGET_API_SECRET": "secret",
            "BITGET_API_PASSPHRASE": "passphrase",
            "BITGET_MODE": "LIVE",
        },
        FakeClient(),
        repository=repository,
        transport=object(),
    )

    assert stream is not None
    assert watchdog is not None
    assert watchdog_interval == 4
    assert stream.repository is repository
    assert stream.symbols == ("BTCUSDT",)
