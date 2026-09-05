"""Fail-closed configuration for Bitget PAPER and LIVE venues."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class BitgetVenueState(StrEnum):
    """Whether the PAPER adapter may be selected by the application."""

    DISABLED = "disabled"
    PAPER_READY = "paper_ready"


class BitgetVenueConfig(BaseModel):
    """Credentials and mode gate for a future Bitget PAPER integration.

    Credentials are deliberately optional: incomplete or blank credentials leave the venue
    disabled instead of attempting a partial integration.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["PAPER"] = "PAPER"
    api_key: SecretStr | None = None
    api_secret: SecretStr | None = None
    passphrase: SecretStr | None = None

    @field_validator("api_key", "api_secret", "passphrase", mode="before")
    @classmethod
    def blank_credentials_are_missing(cls, value: object) -> object:
        """Normalize whitespace-only credential fields to the disabled state."""
        if isinstance(value, str):
            return value.strip() or None
        return value

    @property
    def state(self) -> BitgetVenueState:
        """Return PAPER_READY only when every required credential is present."""
        if all((self.api_key, self.api_secret, self.passphrase)):
            return BitgetVenueState.PAPER_READY
        return BitgetVenueState.DISABLED


class BitgetLiveConfig(BaseModel):
    """Frozen, fail-closed contract for the Bitget LIVE venue.

    Credentials are required: missing or blank values raise a ValidationError
    instead of silently disabling the venue. Leverage is bounded to 20-50 and
    only isolated margin is permitted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["LIVE"] = "LIVE"
    api_key: SecretStr
    api_secret: SecretStr
    passphrase: SecretStr
    product_type: Literal["USDT-FUTURES"] = "USDT-FUTURES"
    margin_coin: Literal["USDT"] = "USDT"
    margin_mode: Literal["isolated"] = "isolated"
    min_leverage: int = Field(default=20, ge=20, le=50)
    max_leverage: int = Field(default=50, ge=20, le=50)
    allocation_pct: Decimal = Field(default=Decimal("0.20"), gt=0, le=1)
    max_normal_positions: int = Field(default=5, gt=0)
    liquidation_buffer: Decimal = Field(default=Decimal("0.10"), gt=0, le=1)

    @field_validator("api_key", "api_secret", "passphrase", mode="before")
    @classmethod
    def reject_missing_or_blank_credentials(cls, value: object) -> object:
        """Fail closed when a required credential is missing or blank."""
        if value is None:
            raise ValueError("credential is required for LIVE mode")
        if isinstance(value, str) and not value.strip():
            raise ValueError("credential must not be blank for LIVE mode")
        return value

    @model_validator(mode="after")
    def validate_leverage_range(self) -> BitgetLiveConfig:
        """Enforce 20 <= min_leverage <= max_leverage <= 50."""
        if self.min_leverage > self.max_leverage:
            raise ValueError("min_leverage must not exceed max_leverage")
        return self


def live_canary_allowed(
    config: BitgetLiveConfig,
    *,
    authenticated_read_passed: bool,
    implementation_enabled: bool,
    safety_checks_passed: bool,
    approval_token: str | None,
) -> bool:
    """Return true only when the explicit bounded-canary gate is fully satisfied."""
    return bool(
        config.mode == "LIVE"
        and authenticated_read_passed
        and implementation_enabled
        and safety_checks_passed
        and approval_token
        and approval_token.strip()
    )
