"""Telethon-independent intake adapter for message updates."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from telethon import events

from fatty_trader.config.telegram import TelegramSettings
from fatty_trader.intake.persistence import (
    RawMessageRepository,
    RawTelegramMessage,
    revision_hash,
)

WIB = ZoneInfo("Asia/Jakarta")


class TelegramIntake:
    def __init__(self, repository: RawMessageRepository) -> None:
        self._repository = repository

    async def attach(self, client: Any, channels: tuple[str, ...]) -> None:
        """Register one push handler; history/backfill remains a separate concern."""

        async def handle(event: Any) -> None:
            self.ingest(channel_id=int(event.chat_id), message=event.message)

        client.add_event_handler(handle, events.NewMessage(chats=list(channels)))

    def ingest(self, *, channel_id: int, message: Any) -> RawTelegramMessage:
        item = self._build_message(channel_id=channel_id, message=message)
        return self._repository.save_if_new(item)

    def _build_message(self, *, channel_id: int, message: Any) -> RawTelegramMessage:
        raw_text = str(getattr(message, "message", "") or "")
        reply = getattr(message, "reply_to", None)
        reply_id = getattr(reply, "reply_to_msg_id", None)
        has_media = getattr(message, "media", None) is not None
        return RawTelegramMessage(
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


def format_forward_html(
    raw_text: str, *, channel_id: int | None = None, message_id: int | None = None
) -> str:
    """Wrap source text as safe, consistently branded Telegram HTML."""
    body = escape(raw_text.strip() or "(media attachment)")
    reference = ""
    if channel_id is not None and message_id is not None:
        reference = f"\nSource ID: <code>{channel_id}:{message_id}</code>"
    return f"<b>Fatty Signal Relay</b> · <i>Source channel update</i>{reference}\n\n{body}"


class TelegramForwarder:
    """Persist source updates and defer relay delivery to the durable bot outbox."""

    def __init__(
        self, client: Any, settings: TelegramSettings, repository: RawMessageRepository
    ) -> None:
        if settings.target_chat_id is None:
            raise ValueError("Telegram forwarding target is not configured")
        self._client = client
        self._settings = settings
        self._intake = TelegramIntake(repository)

    async def handle_message(self, channel_id: int, message: Any) -> None:
        item = self._intake._build_message(channel_id=channel_id, message=message)
        enqueued = self._intake._repository.save_and_enqueue_forward(item)
        if enqueued:
            print(
                f"service=intake event=source-forward-enqueued channel_id={item.channel_id} "
                f"message_id={item.message_id} revision={item.revision_hash[:12]}",
                flush=True,
            )

    async def attach(self) -> None:
        async def handle(event: Any) -> None:
            await self.handle_message(int(event.chat_id), event.message)

        self._client.add_event_handler(
            handle, events.NewMessage(chats=list(self._settings.channels))
        )


def _message_time(message: Any) -> datetime:
    value = getattr(message, "date", None)
    if not isinstance(value, datetime):
        return datetime.now(WIB)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
