"""Raw Telegram message contracts and a deterministic test repository."""

from __future__ import annotations

import hashlib
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4


@dataclass(frozen=True)
class RawTelegramMessage:
    channel_id: int
    message_id: int
    revision_hash: str
    raw_text: str
    received_at: datetime
    reply_to_message_id: int | None = None
    has_media: bool = False
    media_path: str | None = None
    media_sha256: str | None = None
    media_mime_type: str | None = None
    media_size_bytes: int | None = None
    ingestion_origin: str = "realtime"
    entry_expires_at: datetime | None = None
    entry_rejection_reason: str | None = None


class RawMessageRepository(Protocol):
    def save_if_new(self, message: RawTelegramMessage) -> RawTelegramMessage: ...

    def save_and_enqueue_forward(self, message: RawTelegramMessage) -> bool: ...


class InMemoryRawMessageRepository:
    def __init__(self) -> None:
        self._messages: dict[tuple[int, int, str], RawTelegramMessage] = {}
        self.forwards: list[dict[str, object]] = []

    @property
    def count(self) -> int:
        return len(self._messages)

    def save_if_new(self, message: RawTelegramMessage) -> RawTelegramMessage:
        key = (message.channel_id, message.message_id, message.revision_hash)
        existing = self._messages.setdefault(key, message)
        if (
            existing.media_path is None
            and message.media_path is not None
            and message.entry_rejection_reason is None
            and existing.entry_rejection_reason
            in (None, "source-media-download-failed", "source-media-download-timeout")
            and existing.entry_expires_at is not None
            and existing.entry_expires_at > datetime.now(UTC)
        ):
            existing = replace(
                existing,
                media_path=message.media_path,
                media_sha256=message.media_sha256,
                media_mime_type=message.media_mime_type,
                media_size_bytes=message.media_size_bytes,
                entry_rejection_reason=None,
            )
            self._messages[key] = existing
        return existing

    @property
    def forward_count(self) -> int:
        return len(self.forwards)

    def save_and_enqueue_forward(self, message: RawTelegramMessage) -> bool:
        """Persist once; analysis emits the single operator-facing notification."""
        key = (message.channel_id, message.message_id, message.revision_hash)
        if key in self._messages:
            self.save_if_new(message)
            return False
        self._messages[key] = message
        return True


class PostgresRawMessageRepository:
    """Durably retain source messages for relay idempotency and operator telemetry."""

    def __init__(self, connection_factory: Any) -> None:
        self._connection_factory = connection_factory

    @staticmethod
    def _revoke_edited_source(cursor: Any, message: RawTelegramMessage) -> None:
        """Persist the message-level veto before old rows reach analyzer I/O.

        Do not alter dispatch/provider truth here. Admission gates retire only
        known-unsent entries; the shared predicate also protects old revisions.
        """
        cursor.execute(
            """UPDATE telegram_messages tm
            SET entry_rejection_reason='edited-source-message'
            WHERE tm.channel_id=%s AND tm.message_id=%s
              AND (tm.entry_rejection_reason IS NULL
                   OR (%s AND tm.revision_hash=%s))
              AND (%s OR EXISTS (
                  SELECT 1 FROM telegram_messages edited
                  WHERE edited.channel_id=tm.channel_id
                    AND edited.message_id=tm.message_id
                    AND edited.entry_rejection_reason='edited-source-message'
              ))""",
            (
                message.channel_id,
                message.message_id,
                message.entry_rejection_reason == "edited-source-message",
                message.revision_hash,
                message.entry_rejection_reason == "edited-source-message",
            ),
        )

    @staticmethod
    def _heal_media(cursor: Any, message: RawTelegramMessage) -> None:
        """Heal missing attachments only on fresh, unprocessed source rows."""
        if message.media_path is None or message.entry_rejection_reason is not None:
            return
        cursor.execute(
            """UPDATE telegram_messages
            SET media_path=%s, media_sha256=%s, media_mime_type=%s, media_size_bytes=%s,
                entry_rejection_reason=NULL, intake_state='RECEIVED'
            WHERE channel_id=%s AND message_id=%s AND revision_hash=%s
                AND media_path IS NULL AND entry_expires_at > CURRENT_TIMESTAMP
                AND intake_state IN ('RECEIVED', 'EXPIRED')
                AND (entry_rejection_reason IS NULL OR entry_rejection_reason IN
                    ('source-media-download-failed', 'source-media-download-timeout'))""",
            (
                message.media_path,
                message.media_sha256,
                message.media_mime_type,
                message.media_size_bytes,
                message.channel_id,
                message.message_id,
                message.revision_hash,
            ),
        )

    @staticmethod
    def _anchor_coverage(cursor: Any, message: RawTelegramMessage) -> None:
        # Establish the first sighting only, in the same transaction as the raw row.
        # Later pushes/duplicates cannot move the independent history watermark.
        cursor.execute(
            """INSERT INTO telegram_catchup_coverage (channel_id, covered_message_id)
            VALUES (%s, %s) ON CONFLICT (channel_id) DO NOTHING""",
            (message.channel_id, message.message_id),
        )

    def save_if_new(self, message: RawTelegramMessage) -> RawTelegramMessage:
        statement = """
            INSERT INTO telegram_messages (
                id, channel_id, message_id, revision_hash, received_at, raw_text, intake_state,
                has_media, media_path, media_sha256, media_mime_type, media_size_bytes,
                ingestion_origin, entry_expires_at, entry_rejection_reason
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (channel_id, message_id, revision_hash) DO NOTHING
        """
        with closing(self._connection_factory()) as connection:
            with connection.cursor() as cursor:
                self._anchor_coverage(cursor, message)
                cursor.execute(
                    statement,
                    (
                        uuid4(),
                        message.channel_id,
                        message.message_id,
                        message.revision_hash,
                        message.received_at,
                        message.raw_text,
                        "EXPIRED" if message.entry_rejection_reason else "RECEIVED",
                        message.has_media,
                        message.media_path,
                        message.media_sha256,
                        message.media_mime_type,
                        message.media_size_bytes,
                        message.ingestion_origin,
                        message.entry_expires_at,
                        message.entry_rejection_reason,
                    ),
                )
                self._heal_media(cursor, message)
                self._revoke_edited_source(cursor, message)
            connection.commit()
        return message

    def save_and_enqueue_forward(self, message: RawTelegramMessage) -> bool:
        """Atomically retain a source update; analysis owns the operator notification."""
        statement = """
            INSERT INTO telegram_messages (
                id, channel_id, message_id, revision_hash, received_at, raw_text, intake_state,
                has_media, media_path, media_sha256, media_mime_type, media_size_bytes,
                ingestion_origin, entry_expires_at, entry_rejection_reason
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (channel_id, message_id, revision_hash) DO NOTHING
            RETURNING id
        """
        with closing(self._connection_factory()) as connection:
            with connection.cursor() as cursor:
                self._anchor_coverage(cursor, message)
                cursor.execute(
                    statement,
                    (
                        uuid4(),
                        message.channel_id,
                        message.message_id,
                        message.revision_hash,
                        message.received_at,
                        message.raw_text,
                        "EXPIRED" if message.entry_rejection_reason else "RECEIVED",
                        message.has_media,
                        message.media_path,
                        message.media_sha256,
                        message.media_mime_type,
                        message.media_size_bytes,
                        message.ingestion_origin,
                        message.entry_expires_at,
                        message.entry_rejection_reason,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    self._heal_media(cursor, message)
                self._revoke_edited_source(cursor, message)
            connection.commit()
        return row is not None


def revision_hash(
    *,
    raw_text: str,
    reply_to_message_id: int | None,
    has_media: bool,
    media_identity: str = "",
    edited_at: str = "",
) -> str:
    payload = f"{raw_text}\x00{reply_to_message_id or ''}\x00{int(has_media)}"
    if media_identity or edited_at:
        payload += f"\x00{media_identity}\x00{edited_at}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_forward_dedup_key(message: RawTelegramMessage) -> str:
    return f"source-forward:{message.channel_id}:{message.message_id}:{message.revision_hash}"


def _source_forward_payload(message: RawTelegramMessage) -> dict[str, object]:
    return {
        "kind": "source-forward",
        "source_channel_id": message.channel_id,
        "source_message_id": message.message_id,
        "source_revision": message.revision_hash,
        "raw_text": message.raw_text,
        "has_media": message.has_media,
    }
