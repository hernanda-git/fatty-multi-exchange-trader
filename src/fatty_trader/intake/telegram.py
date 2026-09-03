"""Telethon-independent intake adapter for message updates."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from telethon import events  # type: ignore[import-untyped]

from fatty_trader.intake.persistence import (
    RawMessageRepository,
    RawTelegramMessage,
    revision_hash,
)


class TelegramIntake:
    def __init__(self, repository: RawMessageRepository) -> None:
        self._repository = repository

    async def attach(self, client: Any, channels: tuple[str, ...]) -> None:
        """Register one push handler; history/backfill remains a separate concern."""

        async def handle(event: Any) -> None:
            self.ingest(channel_id=int(event.chat_id), message=event.message)

        client.add_event_handler(handle, events.NewMessage(chats=list(channels)))

    def ingest(self, *, channel_id: int, message: Any) -> RawTelegramMessage:
        raw_text = str(getattr(message, "message", "") or "")
        reply = getattr(message, "reply_to", None)
        reply_id = getattr(reply, "reply_to_msg_id", None)
        has_media = getattr(message, "media", None) is not None
        item = RawTelegramMessage(
            channel_id=channel_id,
            message_id=int(message.id),
            revision_hash=revision_hash(
                raw_text=raw_text, reply_to_message_id=reply_id, has_media=has_media
            ),
            raw_text=raw_text,
            received_at=_message_time(message),
            reply_to_message_id=reply_id,
            has_media=has_media,
        )
        return self._repository.save_if_new(item)


def _message_time(message: Any) -> datetime:
    value = getattr(message, "date", None)
    if not isinstance(value, datetime):
        return datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
