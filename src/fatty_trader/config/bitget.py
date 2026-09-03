"""Fail-closed configuration for the Bitget PAPER venue."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator


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
