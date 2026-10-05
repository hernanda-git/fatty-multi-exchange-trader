"""Offline tests for the Bitget monitor lifecycle wiring."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from fatty_trader.execution.bitget_protection_watchdog import WatchdogReport, WatchdogStatus
from fatty_trader.service import (
    build_bitget_monitor_protection,
    check_monitor_heartbeat,
    monitor_heartbeat_age,
    monitor_heartbeat_is_fresh,
    run_bitget_monitor_loop,
    start_monitor_stall_supervisor,
    write_monitor_heartbeat,
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


class _StopAfterFirstMonitor:
    """Monitor stub that walks one full cycle and then asks the loop to stop."""

    def __init__(self, stop_event: asyncio.Event) -> None:
        self.calls = 0
        self._stop_event = stop_event

    async def run_once(self) -> object:
        self.calls += 1
        self._stop_event.set()
        return object()


def _record_exit(record: list[int]) -> Any:
    """Stand-in for ``os._exit`` that records the code instead of killing the process."""

    def fake_exit(code: int = 0) -> None:
        record.append(code)

    return fake_exit


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
async def test_monitor_loop_logs_persisted_latch_not_as_current_failure(capsys) -> None:
    from fatty_trader.execution.bitget_monitor import MonitorReport

    stop = asyncio.Event()

    class LatchedMonitor:
        async def run_once(self):
            stop.set()
            return MonitorReport("kill-switch-latched", latched_reason="provider-fills-invalid")

    await run_bitget_monitor_loop(LatchedMonitor(), interval=1, stop_event=stop)
    output = capsys.readouterr().out
    assert "reasons=none" in output
    assert "latched_reason=provider-fills-invalid" in output


@pytest.mark.asyncio
async def test_monitor_loop_rejects_non_positive_intervals() -> None:
    with pytest.raises(ValueError, match="interval"):
        await run_bitget_monitor_loop(FakeMonitor(), interval=0)


@pytest.mark.asyncio
async def test_monitor_loop_rejects_non_positive_liveness_budgets() -> None:
    with pytest.raises(ValueError, match="cycle timeout"):
        await run_bitget_monitor_loop(FakeMonitor(), interval=30, cycle_timeout=0)
    with pytest.raises(ValueError, match="stall timeout"):
        await run_bitget_monitor_loop(FakeMonitor(), interval=30, stall_timeout=-1)


@pytest.mark.asyncio
async def test_monitor_loop_writes_heartbeat_every_cycle(tmp_path: Path) -> None:
    heartbeat = tmp_path / "monitor-heartbeat"
    stop_event = asyncio.Event()
    monitor = _StopAfterFirstMonitor(stop_event)

    await run_bitget_monitor_loop(
        monitor,
        interval=0.01,
        stop_event=stop_event,
        heartbeat_path=str(heartbeat),
    )

    assert monitor.calls == 1
    age = monitor_heartbeat_age(str(heartbeat))
    assert age is not None and age < 30


@pytest.mark.asyncio
async def test_monitor_cycle_timeout_restarts_instead_of_hanging() -> None:
    """A cycle that never returns must fail loudly; Compose restarts the process."""

    class HangingMonitor:
        async def run_once(self) -> object:
            await asyncio.sleep(3600)
            return object()

    with pytest.raises(RuntimeError, match="cycle exceeded"):
        await run_bitget_monitor_loop(
            HangingMonitor(),
            interval=1,
            cycle_timeout=0.05,
        )


def test_heartbeat_round_trips_and_reports_staleness(tmp_path: Path) -> None:
    path = tmp_path / "hb"
    assert monitor_heartbeat_age(str(path)) is None
    assert monitor_heartbeat_is_fresh(str(path), 30) is False

    write_monitor_heartbeat(str(path), now=1_000.0)
    assert monitor_heartbeat_age(str(path), now=1_010.0) == pytest.approx(10.0)
    assert monitor_heartbeat_is_fresh(str(path), 30, now=1_010.0) is True
    assert monitor_heartbeat_is_fresh(str(path), 5, now=1_010.0) is False


def test_heartbeat_from_a_previous_boot_counts_as_stale(tmp_path: Path) -> None:
    # time.monotonic() restarts with the process, so a stamp in the "future" proves the
    # file predates this boot: it must never be read as fresh.
    path = tmp_path / "hb"
    write_monitor_heartbeat(str(path), now=9_999.0)
    assert monitor_heartbeat_is_fresh(str(path), 30, now=5.0) is False


def test_unreadable_heartbeat_never_reports_healthy(tmp_path: Path) -> None:
    path = tmp_path / "hb"
    path.write_text("not-a-number\n", encoding="utf-8")
    assert monitor_heartbeat_age(str(path)) is None
    assert check_monitor_heartbeat(str(path), 90) == 1
    assert check_monitor_heartbeat(str(path), None) == 2
    assert check_monitor_heartbeat(str(path), 0) == 2


def test_healthcheck_passes_only_with_a_fresh_heartbeat(tmp_path: Path) -> None:
    path = tmp_path / "hb"
    assert check_monitor_heartbeat(str(path), 90) == 1
    write_monitor_heartbeat(str(path))
    assert check_monitor_heartbeat(str(path), 90) == 0


def test_stall_supervisor_exits_the_process_for_restart(tmp_path: Path) -> None:
    """The 2026-09-27 stall blocked the event loop, so only a thread can recover it."""
    path = tmp_path / "hb"  # never written: immediately stale
    exits: list[int] = []

    thread = start_monitor_stall_supervisor(
        str(path), max_age=0.05, check_interval=0.01, exit_callable=_record_exit(exits)
    )
    assert thread.daemon is True
    for _ in range(200):
        if exits:
            break
        time.sleep(0.01)

    assert exits == [1]


def test_stall_supervisor_leaves_a_live_monitor_alone(tmp_path: Path) -> None:
    path = tmp_path / "hb"
    exits: list[int] = []
    write_monitor_heartbeat(str(path))

    start_monitor_stall_supervisor(
        str(path), max_age=60, check_interval=0.01, exit_callable=_record_exit(exits)
    )
    time.sleep(0.05)

    assert exits == []


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
