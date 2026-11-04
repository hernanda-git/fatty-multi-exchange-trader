"""Bounded, audited self-heal for the protection-stream latch.

The watchdog latches by design and never releases. This healer is the single
sanctioned release path: it requires a run of consecutive HEALTHY watchdog
reports, respects a cooldown, trips a circuit breaker on flapping, and writes an
audit reference through the same release API an operator uses.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from fatty_trader.execution.bitget_protection_watchdog import PROTECTION_STREAM_SCOPE

PROVIDER_REASONS = ("provider-position-read-failed", "provider-position-invalid")


def _flag(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = str(environ.get(name, "1" if default else "0")).strip().lower()
    if raw not in {"0", "1"}:
        raise ValueError(name + " must be 0 or 1")
    return raw == "1"


def _positive_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = str(environ.get(name, str(default))).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(name + " must be an integer") from exc
    if value < 1:
        raise ValueError(name + " must be at least 1")
    return value


@dataclass(frozen=True)
class HealerPolicy:
    enabled: bool = True
    socket_cycles: int = 6
    provider_cycles: int = 12
    cooldown_seconds: float = 600.0
    max_per_hour: int = 4
    scope: str = PROTECTION_STREAM_SCOPE


def healer_policy_from_environ(environ: Mapping[str, str]) -> HealerPolicy:
    return HealerPolicy(
        enabled=_flag(environ, "BITGET_LATCH_AUTOHEAL", True),
        socket_cycles=_positive_int(environ, "BITGET_LATCH_HEAL_CYCLES", 6),
        provider_cycles=_positive_int(environ, "BITGET_LATCH_HEAL_PROVIDER_CYCLES", 12),
        cooldown_seconds=float(environ.get("BITGET_LATCH_HEAL_COOLDOWN_SECONDS", "600")),
        max_per_hour=_positive_int(environ, "BITGET_LATCH_HEAL_MAX_PER_HOUR", 4),
    )


class LatchHealer:
    """Release the protection-stream latch only after sustained health."""

    def __init__(
        self,
        policy: HealerPolicy,
        *,
        release: Callable[[str, str], None],
        latch_state: Callable[[str], tuple[bool, str | None]],
        alert: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        stamp: Callable[[], datetime] | None = None,
    ) -> None:
        self._policy = policy
        self._release = release
        self._latch_state = latch_state
        self._alert = alert
        self._clock = clock
        self._stamp = stamp or (lambda: datetime.now(UTC))
        self._healthy = 0
        self._last_release = float("-inf")
        self._recent: deque[float] = deque()

    @property
    def healthy_cycles(self) -> int:
        return self._healthy

    def _required_cycles(self, reason: str | None) -> int:
        text = str(reason or "")
        if any(text.startswith(item) or item in text for item in PROVIDER_REASONS):
            return self._policy.provider_cycles
        return self._policy.socket_cycles

    def _breaker_open(self, now: float) -> bool:
        while self._recent and now - self._recent[0] > 3600:
            self._recent.popleft()
        return len(self._recent) >= self._policy.max_per_hour

    def _status_is_healthy(self, report: object) -> bool:
        status = getattr(report, "status", None)
        value = str(getattr(status, "value", status) or "").upper()
        return value == "HEALTHY"

    def observe(self, report: object) -> str:
        """Return a short action string for logging; never raises."""
        if not self._policy.enabled:
            return "disabled"
        try:
            if not self._status_is_healthy(report):
                self._healthy = 0
                return "disarmed"
            self._healthy += 1
            active, reason = self._latch_state(self._policy.scope)
            if not active:
                return "idle"
            required = self._required_cycles(reason)
            if self._healthy < required:
                return "armed:{}/{}".format(self._healthy, required)
            now = self._clock()
            if now - self._last_release < self._policy.cooldown_seconds:
                return "cooldown"
            if self._breaker_open(now):
                if self._alert is not None:
                    self._alert("latch-flapping:{}".format(self._policy.scope))
                return "breaker"
            reference = "auto:watchdog-healthy:{}:{}cycles".format(
                self._stamp().strftime("%Y%m%dT%H%M%SZ"), self._healthy
            )
            self._release(self._policy.scope, reference)
            self._recent.append(now)
            self._last_release = now
            healed = self._healthy
            self._healthy = 0
            return "healed:{}cycles".format(healed)
        except Exception:
            return "error"


async def observe_async(healer: LatchHealer | None, report: object) -> str:
    """Awaitable wrapper so the loop can call the sync policy without blocking on it."""
    if healer is None:
        return "idle"
    return healer.observe(report)


def latch_state_reader(repository: object):
    """Adapt the reconciliation repository to (active, reason) for the healer scope."""

    def read(scope: str) -> tuple[bool, str | None]:
        is_active = getattr(repository, "kill_switch_active", None) or getattr(
            repository, "is_active", None
        )
        reason_lookup = getattr(repository, "kill_switch_reason", None) or getattr(
            repository, "latch_reason", None
        )
        active = bool(is_active(scope)) if callable(is_active) else False
        reason = reason_lookup(scope) if (active and callable(reason_lookup)) else None
        return active, reason

    return read
