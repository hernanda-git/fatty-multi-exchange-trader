"""Wall-clock floor for the Bitget protection-stream latch.

A cycle count is not an outage measure: the monitor ticks every few seconds, so
three consecutive bad cycles can be a 15 s reconnect. These tests pin the floor
that keeps a short blip from halting entries until someone notices
(2026-11-03: latched 6h23m, and the only page was the rejected dispatch).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fatty_trader.exchanges.bitget.websocket import WebSocketConnectionState
from fatty_trader.execution.bitget_protection_watchdog import (
    PROTECTION_STREAM_SCOPE,
    BitgetProtectionWatchdog,
)
from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository
from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository

_START = datetime(2026, 11, 3, 21, 0, tzinfo=UTC)


class Clock:
    """Deterministic clock; the watchdog reads now() for the outage floor."""

    def __init__(self) -> None:
        self.now = _START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class Socket:
    """Socket double whose state the test can flip to simulate a reconnect."""

    def __init__(self) -> None:
        self.state = WebSocketConnectionState.DISCONNECTED
        self.fresh = False

    def check_freshness(self, symbol: str | None = None) -> bool:
        return self.fresh

    def connect(self) -> None:
        self.state = WebSocketConnectionState.CONNECTED
        self.fresh = True

    def drop(self) -> None:
        self.state = WebSocketConnectionState.DISCONNECTED
        self.fresh = False


def _watchdog(clock, socket, kill_switch, *, min_outage_seconds, cycles=3):
    async def read_position(_symbol: str) -> list[dict[str, str]]:
        return []

    return BitgetProtectionWatchdog(
        socket,
        InMemoryProtectionCapabilityRepository(),
        environment="LIVE",
        symbols=["BTCUSDT"],
        read_position=read_position,
        now=clock,
        kill_switch=kill_switch,
        stale_cycles_before_latch=cycles,
        min_outage_seconds=min_outage_seconds,
    )


@pytest.mark.asyncio
async def test_short_outage_does_not_latch() -> None:
    clock, socket = Clock(), Socket()
    kill_switch = InMemoryReconciliationRepository()
    watchdog = _watchdog(clock, socket, kill_switch, min_outage_seconds=60.0)

    for _ in range(3):
        await watchdog.run_once()
        clock.advance(5)

    assert kill_switch.is_active(PROTECTION_STREAM_SCOPE) is False


@pytest.mark.asyncio
async def test_sustained_outage_latches() -> None:
    clock, socket = Clock(), Socket()
    kill_switch = InMemoryReconciliationRepository()
    watchdog = _watchdog(clock, socket, kill_switch, min_outage_seconds=60.0)

    await watchdog.run_once()
    clock.advance(30)
    await watchdog.run_once()
    clock.advance(35)
    await watchdog.run_once()

    assert kill_switch.is_active(PROTECTION_STREAM_SCOPE) is True


@pytest.mark.asyncio
async def test_healthy_cycle_resets_the_outage_clock() -> None:
    clock, socket = Clock(), Socket()
    kill_switch = InMemoryReconciliationRepository()
    watchdog = _watchdog(clock, socket, kill_switch, min_outage_seconds=60.0)

    await watchdog.run_once()
    clock.advance(5)
    await watchdog.run_once()

    socket.connect()
    clock.advance(5)
    await watchdog.run_once()

    socket.drop()
    for _ in range(3):
        clock.advance(5)
        await watchdog.run_once()

    assert kill_switch.is_active(PROTECTION_STREAM_SCOPE) is False


@pytest.mark.asyncio
async def test_zero_floor_keeps_the_cycle_only_behaviour() -> None:
    clock, socket = Clock(), Socket()
    kill_switch = InMemoryReconciliationRepository()
    watchdog = _watchdog(clock, socket, kill_switch, min_outage_seconds=0.0, cycles=1)

    await watchdog.run_once()

    assert kill_switch.is_active(PROTECTION_STREAM_SCOPE) is True
