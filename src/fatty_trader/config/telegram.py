"""Validated Telegram intake configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class TelegramSettings:
    api_id: int
    api_hash: str
    session_string: str
    channels: tuple[str, ...]

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> TelegramSettings:
        raw_id = values.get("TELEGRAM_API_ID", "").strip()
        if not raw_id.isdigit() or int(raw_id) <= 0:
            raise ValueError("TELEGRAM_API_ID must be a positive numeric value")
        api_hash = values.get("TELEGRAM_API_HASH", "").strip()
        if not api_hash:
            raise ValueError("TELEGRAM_API_HASH is required")
        session = values.get("TELEGRAM_SESSION", "").strip()
        if not session:
            raise ValueError("TELEGRAM_SESSION is required")
        channels = tuple(
            channel.strip()
            for channel in values.get("TELEGRAM_CHANNELS", "").split(",")
            if channel.strip()
        )
        if not channels:
            raise ValueError("TELEGRAM_CHANNELS must contain at least one channel")
        return cls(int(raw_id), api_hash, session, channels)
