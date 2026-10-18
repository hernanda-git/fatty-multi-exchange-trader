from datetime import UTC, datetime
from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.protection_capability import (
    BitgetProtectionCapability,
    NativeProtectionState,
)
from fatty_trader.exchanges.bitget.ws_models import BitgetWebSocketEvent
from fatty_trader.service import (
    build_bitget_protection_admission,
    build_bitget_protection_stream,
    service_config,
)
from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository

STREAM_ENV = {
    "BITGET_PROTECTION_STREAM_ENABLED": "1",
    "BITGET_PROTECTION_STREAM_SYMBOLS": "BTCUSDT",
    "BITGET_API_KEY": "key",
    "BITGET_API_SECRET": "secret",
    "BITGET_API_PASSPHRASE": "passphrase",
    "BITGET_MODE": "LIVE",
}
STREAM_EPOCH = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)
STREAM_EVENT_MS = int(STREAM_EPOCH.timestamp() * 1000)


class _RecordingClient:
    """Minimal execution client surface the close callback reads from."""

    environment = "LIVE"


def test_execution_uses_global_cap_without_single_symbol_restriction() -> None:
    environment = {
        "TRADER_MODE": "LIVE",
        "BITGET_MODE": "LIVE",
        "BITGET_EXECUTION_ENABLED": "1",
        "BITGET_CANARY_MAX_ORDERS": "5",
        "BITGET_APPROVAL_REFERENCE": "user-approved-20260908",
        "BITGET_MAX_CLOCK_SKEW_MS": "5000",
    }

    assert service_config("dispatcher-bitget", environment).execution_enabled is True


def test_protection_capability_gate_is_disabled_by_default() -> None:
    assert build_bitget_protection_admission({}) is None


def test_protection_capability_gate_rejects_invalid_flag() -> None:
    with pytest.raises(ValueError, match="BITGET_PROTECTION_CAPABILITY_GATE_ENABLED"):
        build_bitget_protection_admission({"BITGET_PROTECTION_CAPABILITY_GATE_ENABLED": "yes"})


def test_enabled_protection_capability_gate_uses_live_environment_record() -> None:
    now = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)
    repository = InMemoryProtectionCapabilityRepository()
    repository.upsert(
        BitgetProtectionCapability(
            exchange="bitget",
            environment="LIVE",
            symbol="BTCUSDT",
            native_state=NativeProtectionState.VERIFIED,
        )
    )

    admission = build_bitget_protection_admission(
        {
            "BITGET_PROTECTION_CAPABILITY_GATE_ENABLED": "1",
            "BITGET_MODE": "LIVE",
            "BITGET_PROTECTION_STALE_SECONDS": "5",
        },
        repository=repository,
        now=lambda: now,
    )

    assert admission is not None
    assert admission("BTCUSDT") == (True, "native-protected")
    assert admission("WLDUSDT") == (False, "protection-capability-unknown")


def test_protection_stream_is_disabled_by_default() -> None:
    assert build_bitget_protection_stream({}) is None


def test_enabled_protection_stream_can_start_empty_and_sync_active_symbols_dynamically() -> None:
    stream = build_bitget_protection_stream(
        {
            "BITGET_PROTECTION_STREAM_ENABLED": "1",
            "BITGET_API_KEY": "key",
            "BITGET_API_SECRET": "secret",
            "BITGET_API_PASSPHRASE": "passphrase",
            "BITGET_MODE": "LIVE",
        },
        active_symbol_source=lambda: ["BTCUSDT"],
    )

    assert stream is not None
    assert stream.symbols == ()


def test_protection_stream_mutations_default_to_observe_only_without_engine() -> None:
    stream = build_bitget_protection_stream(
        {**STREAM_ENV, "BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED": "0"},
        repository=InMemoryProtectionCapabilityRepository(),
    )

    assert stream is not None
    assert stream.engine is None


def test_protection_stream_mutations_flag_one_wires_engine_with_close_callback() -> None:
    stream = build_bitget_protection_stream(
        {**STREAM_ENV, "BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED": "1"},
        repository=InMemoryProtectionCapabilityRepository(),
        client=_RecordingClient(),
        entries=lambda: [],
        now=lambda: STREAM_EPOCH,
    )

    assert stream is not None
    assert stream.engine is not None


@pytest.mark.asyncio
async def test_enabled_protection_stream_forwards_only_fresh_marks_to_engine() -> None:
    stream = build_bitget_protection_stream(
        {**STREAM_ENV, "BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED": "1"},
        repository=InMemoryProtectionCapabilityRepository(),
        client=_RecordingClient(),
        entries=lambda: [],
        now=lambda: STREAM_EPOCH,
    )
    assert stream is not None
    engine = stream.engine
    assert engine is not None
    # A connected socket is required for the runtime to accept the mark; the engine
    # then re-checks the same freshness before it touches any close path.
    stream.socket.check_freshness = lambda symbol=None: True

    await stream._on_event(
        BitgetWebSocketEvent(
            kind="mark_price",
            symbol="BTCUSDT",
            mark_price=Decimal("100"),
            event_time_ms=STREAM_EVENT_MS,
        )
    )
    assert engine.last_event_ms("BTCUSDT") == STREAM_EVENT_MS

    await stream._on_event(
        BitgetWebSocketEvent(
            kind="position",
            symbol="ETHUSDT",
            quantity=Decimal("1"),
            event_time_ms=STREAM_EVENT_MS,
        )
    )
    assert engine.last_event_ms("ETHUSDT") is None


@pytest.mark.parametrize("value", ["yes", "true", "2", ""])
def test_protection_stream_mutations_flag_rejects_non_boolean(value: str) -> None:
    with pytest.raises(ValueError, match="BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED"):
        build_bitget_protection_stream(
            {**STREAM_ENV, "BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED": value},
            repository=InMemoryProtectionCapabilityRepository(),
        )


def test_protection_stream_mutations_require_an_execution_client() -> None:
    with pytest.raises(ValueError, match="require an execution client"):
        build_bitget_protection_stream(
            {**STREAM_ENV, "BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED": "1"},
            repository=InMemoryProtectionCapabilityRepository(),
        )


@pytest.mark.parametrize("value", ["mutate", "live", ""])
def test_protection_stream_mode_still_rejects_anything_but_observe(value: str) -> None:
    with pytest.raises(ValueError, match="BITGET_PROTECTION_STREAM_MODE"):
        build_bitget_protection_stream(
            {**STREAM_ENV, "BITGET_PROTECTION_STREAM_MODE": value},
            repository=InMemoryProtectionCapabilityRepository(),
        )


def test_protection_stream_stale_after_reaches_both_socket_and_runtime() -> None:
    stream = build_bitget_protection_stream(
        {**STREAM_ENV, "BITGET_PROTECTION_STREAM_STALE_SECONDS": "7.5"},
        repository=InMemoryProtectionCapabilityRepository(),
    )

    assert stream is not None
    assert stream._stale_after == 7.5
    assert stream.socket._stale_after == 7.5


def test_protection_stream_engine_shares_the_configured_stale_after() -> None:
    stream = build_bitget_protection_stream(
        {
            **STREAM_ENV,
            "BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED": "1",
            "BITGET_PROTECTION_STREAM_STALE_SECONDS": "7.5",
        },
        repository=InMemoryProtectionCapabilityRepository(),
        client=_RecordingClient(),
        entries=lambda: [],
    )

    assert stream is not None
    assert stream.engine is not None
    assert stream.engine._stale_after == 7.5


def test_dispatcher_check_rejects_live_execution_without_all_explicit_gates() -> None:
    enabled = {
        "TRADER_MODE": "DEMO",
        "BITGET_MODE": "DEMO",
        "BITGET_EXECUTION_ENABLED": "1",
        "BITGET_API_KEY": "key",
        "BITGET_API_SECRET": "secret",
        "BITGET_API_PASSPHRASE": "passphrase",
    }
    with pytest.raises(ValueError, match="canary"):
        service_config("dispatcher-bitget", enabled)

    enabled.update(
        {
            "BITGET_CANARY_MAX_ORDERS": "1",
            "BITGET_CANARY_SYMBOL": "BTCUSDT",
            "BITGET_APPROVAL_REFERENCE": "operator-ticket-123",
            "BITGET_MAX_CLOCK_SKEW_MS": "5000",
        }
    )
    assert service_config("dispatcher-bitget", enabled).execution_enabled is True


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("BITGET_CANARY_MAX_ORDERS", "0", "canary"),
        ("BITGET_APPROVAL_REFERENCE", "", "approval"),
        ("BITGET_MAX_CLOCK_SKEW_MS", "0", "clock skew"),
    ],
)
def test_dispatcher_check_rejects_invalid_cutover_values(
    name: str, value: str, message: str
) -> None:
    environment = {
        "TRADER_MODE": "DEMO",
        "BITGET_MODE": "DEMO",
        "BITGET_EXECUTION_ENABLED": "1",
        "BITGET_API_KEY": "key",
        "BITGET_API_SECRET": "secret",
        "BITGET_API_PASSPHRASE": "passphrase",
        "BITGET_CANARY_MAX_ORDERS": "1",
        "BITGET_CANARY_SYMBOL": "BTCUSDT",
        "BITGET_APPROVAL_REFERENCE": "operator-ticket-123",
        "BITGET_MAX_CLOCK_SKEW_MS": "5000",
    }
    environment[name] = value
    with pytest.raises(ValueError, match=message):
        service_config("dispatcher-bitget", environment)
