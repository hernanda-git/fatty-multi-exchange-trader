"""Offline tests for symbol-local Bitget protection capability admission."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fatty_trader.exchanges.bitget.protection_capability import (
    BitgetProtectionCapability,
    NativeProtectionState,
    StreamState,
    can_admit_symbol,
)
from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository

_NOW = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)


def _capability(**overrides: object) -> BitgetProtectionCapability:
    values: dict[str, object] = {
        "exchange": "bitget",
        "environment": "LIVE",
        "symbol": "WLDUSDT",
        "native_state": NativeProtectionState.UNKNOWN,
        "fallback_allowed": False,
        "stream_state": StreamState.DISABLED,
        "last_stream_at": None,
    }
    values.update(overrides)
    return BitgetProtectionCapability(**values)  # type: ignore[arg-type]


def test_native_verified_symbol_is_admissible_without_fallback_stream() -> None:
    allowed, reason = can_admit_symbol(
        _capability(native_state=NativeProtectionState.VERIFIED), now=_NOW, stale_after=5
    )

    assert allowed is True
    assert reason == "native-protected"


def test_unknown_capability_is_blocked_fail_closed() -> None:
    allowed, reason = can_admit_symbol(_capability(), now=_NOW, stale_after=5)

    assert allowed is False
    assert reason == "protection-capability-unknown"


def test_fallback_is_admissible_only_when_stream_is_fresh_and_healthy() -> None:
    allowed, reason = can_admit_symbol(
        _capability(
            fallback_allowed=True,
            stream_state=StreamState.HEALTHY,
            last_stream_at=_NOW - timedelta(seconds=2),
        ),
        now=_NOW,
        stale_after=5,
    )

    assert allowed is True
    assert reason == "fresh-fallback-stream"


@pytest.mark.parametrize(
    "stream_state,last_stream_at,expected_reason",
    [
        (StreamState.STALE, _NOW - timedelta(seconds=1), "fallback-stream-not-healthy"),
        (StreamState.HEALTHY, _NOW - timedelta(seconds=6), "fallback-stream-stale"),
        (StreamState.HEALTHY, None, "fallback-stream-never-ready"),
    ],
)
def test_stale_or_unready_fallback_blocks_only_the_symbol(
    stream_state: StreamState, last_stream_at: datetime | None, expected_reason: str
) -> None:
    allowed, reason = can_admit_symbol(
        _capability(
            fallback_allowed=True,
            stream_state=stream_state,
            last_stream_at=last_stream_at,
        ),
        now=_NOW,
        stale_after=5,
    )

    assert allowed is False
    assert reason == expected_reason


def test_wrong_environment_capability_is_not_reused_for_live() -> None:
    capability = _capability(
        environment="DEMO",
        native_state=NativeProtectionState.VERIFIED,
    )

    allowed, reason = can_admit_symbol(
        capability,
        now=_NOW,
        stale_after=5,
        required_environment="LIVE",
    )

    assert allowed is False
    assert reason == "protection-capability-environment-mismatch"


def test_capability_repository_upsert_and_lookup_are_environment_specific() -> None:
    repository = InMemoryProtectionCapabilityRepository()
    demo = _capability(
        environment="DEMO",
        native_state=NativeProtectionState.VERIFIED,
    )
    live = _capability(
        environment="LIVE",
        native_state=NativeProtectionState.UNSUPPORTED,
        fallback_allowed=True,
    )

    repository.upsert(demo)
    repository.upsert(live)

    assert repository.get("bitget", "DEMO", "WLDUSDT") == demo
    assert repository.get("bitget", "LIVE", "WLDUSDT") == live
    assert repository.get("bitget", "LIVE", "BTCUSDT") is None
