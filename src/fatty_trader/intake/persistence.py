"""Raw Telegram message contracts and a deterministic test repository."""

from __future__ import annotations

import hashlib
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
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
        return existing

    @property
    def forward_count(self) -> int:
        return len(self.forwards)

    def save_and_enqueue_forward(self, message: RawTelegramMessage) -> bool:
        """Persist once; analysis emits the single operator-facing notification."""
        key = (message.channel_id, message.message_id, message.revision_hash)
        if key in self._messages:
            return False
        self._messages[key] = message
        return True


class PostgresRawMessageRepository:
    """Durably retain source messages for relay idempotency and operator telemetry."""

    def __init__(self, connection_factory: Any) -> None:
        self._connection_factory = connection_factory

    def save_if_new(self, message: RawTelegramMessage) -> RawTelegramMessage:
        statement = """
            INSERT INTO telegram_messages (
                id, channel_id, message_id, revision_hash, received_at, raw_text, intake_state
            ) VALUES (%s, %s, %s, %s, %s, %s, 'RECEIVED')
            ON CONFLICT (channel_id, message_id, revision_hash) DO NOTHING
        """
        with closing(self._connection_factory()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    statement,
                    (
                        uuid4(),
                        message.channel_id,
                        message.message_id,
                        message.revision_hash,
                        message.received_at,
                        message.raw_text,
                    ),
                )
            connection.commit()
        return message

    def save_and_enqueue_forward(self, message: RawTelegramMessage) -> bool:
        """Atomically retain a source update; analysis owns the operator notification."""
        statement = """
            INSERT INTO telegram_messages (
                id, channel_id, message_id, revision_hash, received_at, raw_text, intake_state
            ) VALUES (%s, %s, %s, %s, %s, %s, 'RECEIVED')
            ON CONFLICT (channel_id, message_id, revision_hash) DO NOTHING
            RETURNING id
        """
        with closing(self._connection_factory()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    statement,
                    (
                        uuid4(),
                        message.channel_id,
                        message.message_id,
                        message.revision_hash,
                        message.received_at,
                        message.raw_text,
                    ),
                )
                row = cursor.fetchone()
            connection.commit()
        return row is not None


def revision_hash(*, raw_text: str, reply_to_message_id: int | None, has_media: bool) -> str:
    payload = f"{raw_text}\x00{reply_to_message_id or ''}\x00{int(has_media)}"
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
