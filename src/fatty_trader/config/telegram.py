"""Validated Telegram intake configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TelegramSettings:
    api_id: int
    api_hash: str = field(repr=False)
    session_string: str = field(repr=False)
    channels: tuple[str, ...]
    target_chat_id: int | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> TelegramSettings:
        raw_id = values.get("TG_API_ID", values.get("TELEGRAM_API_ID", "")).strip()
        if not raw_id.isdigit() or int(raw_id) <= 0:
            raise ValueError("TELEGRAM_API_ID must be a positive numeric value")
        api_hash = values.get("TG_API_HASH", values.get("TELEGRAM_API_HASH", "")).strip()
        if not api_hash:
            raise ValueError("TELEGRAM_API_HASH is required")
        session = values.get("TELEGRAM_SESSION", "").strip()
        if not session:
            raise ValueError("TELEGRAM_SESSION is required")
        channels = tuple(
            channel.strip()
            for channel in values.get(
                "TELEGRAM_SOURCE_CHANNELS", values.get("TELEGRAM_CHANNELS", "")
            ).split(",")
            if channel.strip()
        )
        if not channels:
            raise ValueError("TELEGRAM_CHANNELS must contain at least one channel")
        raw_target = values.get("TELEGRAM_TARGET_CHAT_ID", "").strip()
        if not raw_target or not raw_target.lstrip("-").isdigit() or int(raw_target) == 0:
            raise ValueError("TELEGRAM_TARGET_CHAT_ID must be a non-zero numeric value")
        return cls(int(raw_id), api_hash, session, channels, int(raw_target))
