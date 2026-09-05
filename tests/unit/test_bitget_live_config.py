"""Task 1 (RED): frozen Bitget LIVE configuration contract."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from fatty_trader.config.bitget import BitgetLiveConfig, live_canary_allowed


def _creds(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "api_key": "live-key",
        "api_secret": "live-secret",
        "passphrase": "live-passphrase",
    }
    base.update(overrides)
    return base


def test_valid_live_config_uses_frozen_defaults() -> None:
    config = BitgetLiveConfig(**_creds())

    assert config.mode == "LIVE"
    assert config.product_type == "USDT-FUTURES"
    assert config.margin_coin == "USDT"
    assert config.margin_mode == "isolated"
    assert config.min_leverage == 20
    assert config.max_leverage == 50
    assert config.allocation_pct == Decimal("0.20")
    assert config.max_normal_positions == 5
    assert config.liquidation_buffer > 0


def test_non_isolated_margin_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        BitgetLiveConfig(**_creds(margin_mode="crossed"))


def test_leverage_below_minimum_is_rejected() -> None:
    with pytest.raises(ValidationError):
        BitgetLiveConfig(**_creds(min_leverage=19))


def test_leverage_above_maximum_is_rejected() -> None:
    with pytest.raises(ValidationError):
        BitgetLiveConfig(**_creds(max_leverage=51))


def test_inverted_leverage_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        BitgetLiveConfig(**_creds(min_leverage=40, max_leverage=30))


def test_missing_credentials_fail_closed() -> None:
    with pytest.raises(ValidationError):
        BitgetLiveConfig()  # type: ignore[call-arg]


def test_blank_credentials_fail_closed() -> None:
    with pytest.raises(ValidationError):
        BitgetLiveConfig(**_creds(api_key="   "))
    with pytest.raises(ValidationError):
        BitgetLiveConfig(**_creds(api_secret=""))
    with pytest.raises(ValidationError):
        BitgetLiveConfig(**_creds(passphrase="  "))


def test_secrets_are_masked_in_repr() -> None:
    config = BitgetLiveConfig(**_creds())
    rendered = f"{config!r}\n{config}"

    assert "live-secret" not in rendered
    assert "live-passphrase" not in rendered


def test_live_canary_gate_requires_every_runtime_precondition() -> None:
    config = BitgetLiveConfig(**_creds())
    assert not live_canary_allowed(
        config,
        authenticated_read_passed=True,
        implementation_enabled=False,
        safety_checks_passed=True,
        approval_token="operator-approved",
    )
    assert live_canary_allowed(
        config,
        authenticated_read_passed=True,
        implementation_enabled=True,
        safety_checks_passed=True,
        approval_token="operator-approved",
    )
    assert not live_canary_allowed(
        config,
        authenticated_read_passed=True,
        implementation_enabled=True,
        safety_checks_passed=True,
        approval_token=" ",
    )
