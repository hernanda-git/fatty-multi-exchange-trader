"""Validated, secret-safe configuration for Telegram bot outbox delivery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TelegramNotificationSettings:
    """Credentials for a bot that delivers durable operator notifications."""

    bot_token: str = field(repr=False)
    target_chat_id: int

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> TelegramNotificationSettings:
        token = values.get("TG_BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("TG_BOT_TOKEN is required")
        raw_target = values.get("TELEGRAM_TARGET_CHAT_ID", "").strip()
        if not raw_target or not raw_target.lstrip("-").isdigit() or int(raw_target) == 0:
            raise ValueError("TELEGRAM_TARGET_CHAT_ID must be a non-zero numeric value")
        return cls(bot_token=token, target_chat_id=int(raw_target))
