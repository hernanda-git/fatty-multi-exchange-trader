"""Symbol-local Bitget protection capability and admission policy.

The policy is intentionally pure.  Persistence and stream workers provide observations;
this module decides whether a new position may be admitted from those observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class NativeProtectionState(StrEnum):
    UNKNOWN = "UNKNOWN"
    VERIFIED = "VERIFIED"
    UNSUPPORTED = "UNSUPPORTED"
    FAILED = "FAILED"


class StreamState(StrEnum):
    DISABLED = "DISABLED"
    CONNECTING = "CONNECTING"
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class BitgetProtectionCapability:
    """Durable symbol/environment capability observation."""

    exchange: str
    environment: str
    symbol: str
    position_mode: str = "one_way_mode"
    margin_mode: str = "isolated"
    native_state: NativeProtectionState = NativeProtectionState.UNKNOWN
    fallback_allowed: bool = False
    payload_profile: str = "classic-v2-position"
    last_verified_at: datetime | None = None
    last_error: str | None = None
    stream_state: StreamState = StreamState.DISABLED
    last_stream_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.exchange.strip():
            raise ValueError("protection capability exchange is required")
        if not self.environment.strip():
            raise ValueError("protection capability environment is required")
        if not self.symbol.strip():
            raise ValueError("protection capability symbol is required")
        if not self.position_mode.strip() or not self.margin_mode.strip():
            raise ValueError("protection capability account modes are required")
        if not self.payload_profile.strip():
            raise ValueError("protection capability payload profile is required")


def _native_state(value: NativeProtectionState | str) -> NativeProtectionState | None:
    try:
        return NativeProtectionState(str(value).upper())
    except ValueError:
        return None


def _stream_state(value: StreamState | str) -> StreamState | None:
    try:
        return StreamState(str(value).upper())
    except ValueError:
        return None


def _fresh(last_event: datetime | None, now: datetime, stale_after: float) -> bool:
    if last_event is None or stale_after < 0:
        return False
    if last_event.tzinfo is None or now.tzinfo is None:
        return False
    age = (now - last_event).total_seconds()
    return 0 <= age <= stale_after


def can_admit_symbol(
    capability: BitgetProtectionCapability,
    *,
    now: datetime,
    stale_after: float,
    required_environment: str | None = None,
) -> tuple[bool, str]:
    """Return whether this symbol has a currently provable protection lane.

    Native verification takes precedence over the fallback stream.  A fallback lane is
    valid only when explicitly allowlisted and its stream is healthy and fresh.  Every
    other outcome blocks the symbol; this function never changes global kill-switch state.
    """
    if required_environment is not None and (
        capability.environment.strip().upper() != required_environment.strip().upper()
    ):
        return False, "protection-capability-environment-mismatch"

    native_state = _native_state(capability.native_state)
    if native_state is NativeProtectionState.VERIFIED:
        return True, "native-protected"

    if native_state is NativeProtectionState.UNKNOWN and not capability.fallback_allowed:
        return False, "protection-capability-unknown"
    if not capability.fallback_allowed:
        return False, "fallback-not-allowed"

    stream_state = _stream_state(capability.stream_state)
    if stream_state is not StreamState.HEALTHY:
        return False, "fallback-stream-not-healthy"
    if not _fresh(capability.last_stream_at, now, stale_after):
        if capability.last_stream_at is None:
            return False, "fallback-stream-never-ready"
        return False, "fallback-stream-stale"
    return True, "fresh-fallback-stream"
