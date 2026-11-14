"""Raw Telegram message contracts and a deterministic test repository."""

from __future__ import annotations

import hashlib
import re
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
    source_edited_at: datetime | None = None


def _row_values(row: Any, *names: str) -> tuple[Any, ...]:
    """Read a cursor row by name (dict rows) or position (tuple rows)."""
    if isinstance(row, dict):
        return tuple(row[name] for name in names)
    return tuple(row[: len(names)])


def _is_same_signal_or_cosmetic(text1: str, text2: str) -> bool:
    if not text1 or not text2:
        return text1 == text2
    if text1.strip() == text2.strip():
        return True
    clean1 = re.sub(r"\s+", " ", text1).strip()
    clean2 = re.sub(r"\s+", " ", text2).strip()
    if clean1 == clean2:
        return True
    norm1 = re.sub(r"(?:^|\s)[#$]([A-Z0-9]+)\s+[$#]\1\b", r" \1", clean1, flags=re.I)
    norm2 = re.sub(r"(?:^|\s)[#$]([A-Z0-9]+)\s+[$#]\1\b", r" \1", clean2, flags=re.I)
    return (
        re.sub(r"[#$]", "", norm1).strip().upper() == re.sub(r"[#$]", "", norm2).strip().upper()
    )

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
        siblings = [
            stored for stored_key, stored in self._messages.items() if stored_key[:2] == key[:2]
        ]
        existing = self._messages.get(key)
        if existing is None:
            reason = message.entry_rejection_reason
            if reason is None and siblings:
                current = next(
                    (s for s in siblings if s.entry_rejection_reason is None), None
                )
                if current is not None:
                    reason = (
                        "superseded-source-revision"
                        if _is_same_signal_or_cosmetic(current.raw_text, message.raw_text)
                        else None
                    )
                # No eligible revision (legacy edited or all superseded): the new
                # publication is the signal, even when its text matches an old row.
            # The latest text-changing revision is the signal; every older
            # revision is audit-only. Same-text re-publications never supersede
            # the retained eligible revision.
            if reason is None:
                for sibling_key, sibling in list(self._messages.items()):
                    if sibling_key[:2] == key[:2]:
                        self._messages[sibling_key] = replace(
                            sibling, entry_rejection_reason="superseded-source-revision"
                        )
            existing = replace(message, entry_rejection_reason=reason)
            self._messages[key] = existing
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
        first = key not in self._messages
        self.save_if_new(message)
        return first


class PostgresRawMessageRepository:
    """Durably retain source messages for relay idempotency and operator telemetry."""

    def __init__(self, connection_factory: Any) -> None:
        self._connection_factory = connection_factory

    @staticmethod
    def _supersede_earlier_revisions(cursor: Any, message: RawTelegramMessage) -> None:
        """Make the latest text-changing revision the only eligible revision.

        The publisher edits every post (chart attachment/formatting), so an
        `edit_date` alone is provenance. A revision whose text differs from the
        current eligible revision becomes the new signal and every older
        revision becomes audit-only. Same-text re-publications are classified
        as `superseded-source-revision` by `_classify_revision` and never call
        this method, so they cannot create a duplicate management action.
        """
        cursor.execute(
            """UPDATE telegram_messages
            SET entry_rejection_reason='superseded-source-revision'
            WHERE channel_id=%s AND message_id=%s AND revision_hash<>%s""",
            (message.channel_id, message.message_id, message.revision_hash),
        )

    @staticmethod
    def _classify_revision(cursor: Any, message: RawTelegramMessage) -> str | None:
        """Reason to store for this revision, by comparison with retained revisions.

        ``None`` for the first revision ever observed (the channel's published
        text is the signal, even if it was edited before we first saw it),
        ``superseded-source-revision`` when the text matches an audited revision,
        and ``edited-source-message`` when the text differs from a retained one.
        """
        if message.entry_rejection_reason is not None:
            return message.entry_rejection_reason
        cursor.execute(
            """SELECT raw_text, entry_rejection_reason
               FROM telegram_messages WHERE channel_id=%s AND message_id=%s
               ORDER BY (entry_rejection_reason IS NULL) DESC,
                        received_at DESC, id DESC""",
            (message.channel_id, message.message_id),
        )
        rows = cursor.fetchall()
        if not rows:
            return None
        reference = rows[0]
        reference_rejection = (
            reference["entry_rejection_reason"]
            if isinstance(reference, dict)
            else reference[1]
        )
        if reference_rejection is not None:
            # No eligible revision: this publication becomes the signal.
            return None
        reference_text = (
            reference["raw_text"] if isinstance(reference, dict) else reference[0]
        )
        if _is_same_signal_or_cosmetic(reference_text, message.raw_text):
            return "superseded-source-revision"
        return None

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
                ingestion_origin, entry_expires_at, entry_rejection_reason, edit_veto_recorded_at,
                source_edited_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      CASE WHEN %s='edited-source-message' THEN clock_timestamp() END, %s)
            ON CONFLICT (channel_id, message_id, revision_hash) DO NOTHING
        """
        with closing(self._connection_factory()) as connection:
            with connection.cursor() as cursor:
                self._anchor_coverage(cursor, message)
                reason = self._classify_revision(cursor, message)
                cursor.execute(
                    statement,
                    (
                        uuid4(),
                        message.channel_id,
                        message.message_id,
                        message.revision_hash,
                        message.received_at,
                        message.raw_text,
                        "EXPIRED" if reason else "RECEIVED",
                        message.has_media,
                        message.media_path,
                        message.media_sha256,
                        message.media_mime_type,
                        message.media_size_bytes,
                        message.ingestion_origin,
                        message.entry_expires_at,
                        reason,
                        reason,
                        message.source_edited_at,
                    ),
                )
                self._heal_media(cursor, message)
                if reason is None:
                    self._supersede_earlier_revisions(cursor, message)
            connection.commit()
        return replace(message, entry_rejection_reason=reason)

    def save_and_enqueue_forward(self, message: RawTelegramMessage) -> bool:
        """Atomically retain a source update; analysis owns the operator notification."""
        statement = """
            INSERT INTO telegram_messages (
                id, channel_id, message_id, revision_hash, received_at, raw_text, intake_state,
                has_media, media_path, media_sha256, media_mime_type, media_size_bytes,
                ingestion_origin, entry_expires_at, entry_rejection_reason, edit_veto_recorded_at,
                source_edited_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      CASE WHEN %s='edited-source-message' THEN clock_timestamp() END, %s)
            ON CONFLICT (channel_id, message_id, revision_hash) DO NOTHING
            RETURNING id
        """
        with closing(self._connection_factory()) as connection:
            with connection.cursor() as cursor:
                self._anchor_coverage(cursor, message)
                reason = self._classify_revision(cursor, message)
                cursor.execute(
                    statement,
                    (
                        uuid4(),
                        message.channel_id,
                        message.message_id,
                        message.revision_hash,
                        message.received_at,
                        message.raw_text,
                        "EXPIRED" if reason else "RECEIVED",
                        message.has_media,
                        message.media_path,
                        message.media_sha256,
                        message.media_mime_type,
                        message.media_size_bytes,
                        message.ingestion_origin,
                        message.entry_expires_at,
                        reason,
                        reason,
                        message.source_edited_at,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    self._heal_media(cursor, message)
                if reason is None:
                    self._supersede_earlier_revisions(cursor, message)
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
