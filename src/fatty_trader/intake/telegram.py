"""Telethon-independent intake adapter for message updates."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from telethon import events

from fatty_trader.config.telegram import TelegramSettings, channel_ref
from fatty_trader.intake.media import (
    MAX_MEDIA_BYTES,
    BoundedMediaBuffer,
    MediaTooLarge,
    persist_media_bytes,
)
from fatty_trader.intake.persistence import (
    RawMessageRepository,
    RawTelegramMessage,
    revision_hash,
)

WIB = ZoneInfo("Asia/Jakarta")
MAX_SOURCE_MEDIA_BYTES = MAX_MEDIA_BYTES
SOURCE_MEDIA_DOWNLOAD_TIMEOUT_SECONDS = 30.0
SUPPORTED_SOURCE_MEDIA = {"image/jpeg", "image/png", "image/webp"}


class TelegramIntake:
    def __init__(self, repository: RawMessageRepository) -> None:
        self._repository = repository

    async def attach(self, client: Any, channels: tuple[str, ...]) -> None:
        """Retain new messages and audit-only edits; history is separate."""

        async def handle(event: Any) -> None:
            self.ingest(channel_id=int(event.chat_id), message=event.message)

        client.add_event_handler(
            handle, events.NewMessage(chats=[channel_ref(channel) for channel in channels])
        )
        client.add_event_handler(
            handle, events.MessageEdited(chats=[channel_ref(channel) for channel in channels])
        )

    def ingest(
        self, *, channel_id: int, message: Any, origin: str = "realtime"
    ) -> RawTelegramMessage:
        item = self._build_message(channel_id=channel_id, message=message, origin=origin)
        return self._repository.save_if_new(item)

    async def ingest_async(
        self,
        *,
        channel_id: int,
        message: Any,
        media_root: str,
        persist: bool = True,
        origin: str = "realtime",
    ) -> RawTelegramMessage:
        item = self._build_message(channel_id=channel_id, message=message, origin=origin)
        if not item.has_media or item.entry_rejection_reason:
            return self._repository.save_if_new(item) if persist else item
        try:
            with BoundedMediaBuffer() as stream:
                await asyncio.wait_for(
                    message.download_media(file=stream),
                    timeout=SOURCE_MEDIA_DOWNLOAD_TIMEOUT_SECONDS,
                )
                raw = stream.getvalue()
                mime_type = str(getattr(getattr(message, "file", None), "mime_type", "image/jpeg"))
                artifact = persist_media_bytes(
                    media_root,
                    channel_id=channel_id,
                    message_id=item.message_id,
                    revision_hash=item.revision_hash,
                    mime_type=mime_type,
                    data=raw,
                )
                item = item.__class__(
                    **{
                        **item.__dict__,
                        "media_path": str(artifact.path),
                        "media_sha256": artifact.sha256,
                        "media_mime_type": artifact.mime_type,
                        "media_size_bytes": artifact.size_bytes,
                    }
                )
        except MediaTooLarge:
            item = replace(item, entry_rejection_reason="oversized-source-media")
        except TimeoutError:
            item = replace(item, entry_rejection_reason="source-media-download-timeout")
        except Exception:
            # Preserve audit history on provider/file failures without attaching partial bytes.
            # asyncio cancellation is a BaseException and deliberately propagates.
            item = replace(item, entry_rejection_reason="source-media-download-failed")
        return self._repository.save_if_new(item) if persist else item

    def _build_message(
        self, *, channel_id: int, message: Any, origin: str = "realtime"
    ) -> RawTelegramMessage:
        source_time = _message_time(message)
        now = datetime.now(UTC)
        expires_at = source_time + timedelta(minutes=5)
        rejection = None
        if not isinstance(getattr(message, "date", None), datetime):
            rejection = "missing-source-time"
        elif source_time > now + timedelta(seconds=30):
            rejection = "future-source-time"
        elif expires_at <= now:
            rejection = "stale-source-message"
        elif getattr(message, "edit_date", None) is not None:
            # An edit is audit-only: it must never become a second ENTER revision.
            rejection = "edited-source-message"
        if rejection is None and getattr(message, "media", None) is not None:
            file = getattr(message, "file", None)
            mime = getattr(file, "mime_type", "image/jpeg")
            size = getattr(file, "size", None)
            if mime not in SUPPORTED_SOURCE_MEDIA:
                rejection = "unsupported-source-media"
            elif isinstance(size, int) and size > MAX_SOURCE_MEDIA_BYTES:
                rejection = "oversized-source-media"
        raw_text = str(getattr(message, "message", "") or "")
        reply = getattr(message, "reply_to", None)
        reply_id = getattr(reply, "reply_to_msg_id", None)
        has_media = getattr(message, "media", None) is not None
        return RawTelegramMessage(
            channel_id=channel_id,
            message_id=int(message.id),
            revision_hash=revision_hash(
                raw_text=raw_text,
                reply_to_message_id=reply_id,
                has_media=has_media,
                media_identity=_media_identity(message),
                edited_at=_edit_identity(message),
            ),
            raw_text=raw_text,
            received_at=source_time,
            ingestion_origin=origin,
            entry_expires_at=expires_at,
            entry_rejection_reason=rejection,
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

    async def handle_historical_message(self, channel_id: int, message: Any) -> None:
        await self.handle_message(channel_id, message, origin="catchup")

    async def handle_message(
        self, channel_id: int, message: Any, *, origin: str = "realtime"
    ) -> None:
        item = await self._intake.ingest_async(
            channel_id=channel_id,
            message=message,
            media_root=self._settings.media_root,
            persist=False,
            origin=origin,
        )
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
            handle, events.NewMessage(chats=list(self._settings.channel_refs))
        )
        self._client.add_event_handler(
            handle, events.MessageEdited(chats=list(self._settings.channel_refs))
        )


def _media_identity(message: Any) -> str:
    media = getattr(message, "media", None)
    for kind in ("photo", "document"):
        identity = getattr(getattr(media, kind, None), "id", None)
        if identity is not None:
            return f"{kind}:{identity}"
    return ""


def _edit_identity(message: Any) -> str:
    value = getattr(message, "edit_date", None)
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    return "edited" if value is not None else ""


def _message_time(message: Any) -> datetime:
    value = getattr(message, "date", None)
    if not isinstance(value, datetime):
        return datetime.now(WIB)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
