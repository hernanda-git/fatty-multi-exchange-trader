from datetime import UTC, datetime

import pytest

from fatty_trader.exchanges.bitget.protection_capability import (
    BitgetProtectionCapability,
    NativeProtectionState,
)
from fatty_trader.service import (
    build_bitget_protection_admission,
    build_bitget_protection_stream,
    service_config,
)
from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository


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


def test_stream_mutation_flag_cannot_enable_unimplemented_close_path() -> None:
    with pytest.raises(ValueError, match="observe-only"):
        build_bitget_protection_stream(
            {
                "BITGET_PROTECTION_STREAM_ENABLED": "1",
                "BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED": "1",
                "BITGET_PROTECTION_STREAM_SYMBOLS": "BTCUSDT",
                "BITGET_API_KEY": "key",
                "BITGET_API_SECRET": "secret",
                "BITGET_API_PASSPHRASE": "passphrase",
                "BITGET_MODE": "LIVE",
            }
        )


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


