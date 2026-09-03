"""Construct an authenticated Telethon client from a session string."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from telethon import TelegramClient  # type: ignore[import-untyped]
from telethon.sessions import StringSession  # type: ignore[import-untyped]

from fatty_trader.config.telegram import TelegramSettings


def build_telethon_client(
    settings: TelegramSettings,
    *,
    client_factory: Callable[..., Any] = TelegramClient,
    session_factory: Callable[[str], Any] = StringSession,
) -> Any:
    """Build a client without file sessions or interactive phone authentication."""
    return client_factory(
        session_factory(settings.session_string), settings.api_id, settings.api_hash
    )
