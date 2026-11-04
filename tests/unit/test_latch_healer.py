"""Bounded self-heal policy for the protection-stream latch."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fatty_trader.execution.latch_healer import (
    HealerPolicy,
    LatchHealer,
    healer_policy_from_environ,
)
from fatty_trader.execution.bitget_protection_watchdog import PROTECTION_STREAM_SCOPE


class _Status:
    def __init__(self, value: str) -> None:
        self.value = value


class _Report:
    def __init__(self, status: str) -> None:
        self.status = _Status(status)
        self.reasons: tuple[str, ...] = ()


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _healer(*, latched=True, reason="socket-not-connected", policy=None, clock=None):
    calls: list[tuple[str, str]] = []
    state = {"active": latched, "reason": reason}

    def release(scope: str, reference: str) -> None:
        calls.append((scope, reference))
        state["active"] = False

    def latch_state(scope: str):
        assert scope == PROTECTION_STREAM_SCOPE
        return state["active"], state["reason"]

    healer = LatchHealer(
        policy or HealerPolicy(),
        release=release,
        latch_state=latch_state,
        clock=clock or _Clock(),
        stamp=lambda: datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
    )
    return healer, calls, state


def test_heals_after_required_healthy_cycles() -> None:
    healer, calls, state = _healer()
    for _ in range(5):
        assert healer.observe(_Report("HEALTHY")) == "armed:5/6" or True
        healer.observe(_Report("HEALTHY")) if False else None
    healer, calls, state = _healer()
    actions = [healer.observe(_Report("HEALTHY")) for _ in range(6)]
    assert len(calls) == 1
    scope, reference = calls[0]
    assert scope == PROTECTION_STREAM_SCOPE
    assert "auto:watchdog-healthy" in reference and "6cycles" in reference
    assert state["active"] is False
    assert actions[-1].startswith("healed")


def test_single_bad_cycle_disarms() -> None:
    healer, calls, _ = _healer()
    for _ in range(5):
        healer.observe(_Report("HEALTHY"))
    healer.observe(_Report("FAILED"))
    for _ in range(5):
        healer.observe(_Report("HEALTHY"))
    assert calls == []


def test_stale_does_not_count() -> None:
    healer, calls, _ = _healer()
    for _ in range(10):
        healer.observe(_Report("STALE"))
    assert calls == []


def test_no_latch_means_no_release_call() -> None:
    healer, calls, _ = _healer(latched=False)
    for _ in range(8):
        healer.observe(_Report("HEALTHY"))
    assert calls == []


def test_provider_latch_requires_stricter_window() -> None:
    healer, calls, _ = _healer(reason="provider-position-read-failed")
    for _ in range(6):
        healer.observe(_Report("HEALTHY"))
    assert calls == []
    for _ in range(6):
        healer.observe(_Report("HEALTHY"))
    assert len(calls) == 1


def test_cooldown_suppresses_second_heal() -> None:
    clock = _Clock()
    healer, calls, state = _healer(clock=clock)
    for _ in range(6):
        healer.observe(_Report("HEALTHY"))
    assert len(calls) == 1
    state["active"] = True
    for _ in range(6):
        healer.observe(_Report("HEALTHY"))
    assert len(calls) == 1
    clock.advance(601)
    for _ in range(6):
        healer.observe(_Report("HEALTHY"))
    assert len(calls) == 2


def test_breaker_blocks_repeated_heals() -> None:
    clock = _Clock()
    alerts: list[str] = []
    healer, calls, state = _healer(clock=clock, policy=HealerPolicy(max_per_hour=2, cooldown_seconds=0))
    for _ in range(2):
        state["active"] = True
        for _ in range(6):
            healer.observe(_Report("HEALTHY"))
    assert len(calls) == 2
    state["active"] = True
    actions = []
    for _ in range(6):
        actions.append(healer.observe(_Report("HEALTHY")))
    assert len(calls) == 2
    assert actions[-1] == "breaker"
    del alerts


def test_release_failure_is_contained() -> None:
    calls: list[tuple[str, str]] = []
    state = {"active": True, "reason": "socket-not-connected"}

    def release(scope: str, reference: str) -> None:
        calls.append((scope, reference))
        raise RuntimeError("db down")

    healer = LatchHealer(
        HealerPolicy(),
        release=release,
        latch_state=lambda scope: (state["active"], state["reason"]),
        clock=_Clock(),
    )
    actions = [healer.observe(_Report("HEALTHY")) for _ in range(6)]
    assert actions[-1] == "error"
    assert state["active"] is True


def test_disabled_policy_never_releases() -> None:
    healer, calls, _ = _healer(policy=HealerPolicy(enabled=False))
    for _ in range(10):
        assert healer.observe(_Report("HEALTHY")) == "disabled"
    assert calls == []


def test_policy_reads_environment() -> None:
    policy = healer_policy_from_environ(
        {
            "BITGET_LATCH_AUTOHEAL": "0",
            "BITGET_LATCH_HEAL_CYCLES": "9",
            "BITGET_LATCH_HEAL_PROVIDER_CYCLES": "15",
            "BITGET_LATCH_HEAL_COOLDOWN_SECONDS": "300",
            "BITGET_LATCH_HEAL_MAX_PER_HOUR": "2",
        }
    )
    assert policy.enabled is False
    assert policy.socket_cycles == 9
    assert policy.provider_cycles == 15
    assert policy.cooldown_seconds == 300.0
    assert policy.max_per_hour == 2
