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


class InMemoryRawMessageRepository:
    def __init__(self) -> None:
        self._messages: dict[tuple[int, int, str], RawTelegramMessage] = {}

    @property
    def count(self) -> int:
        return len(self._messages)

    def save_if_new(self, message: RawTelegramMessage) -> RawTelegramMessage:
        key = (message.channel_id, message.message_id, message.revision_hash)
        existing = self._messages.setdefault(key, message)
        return existing


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


def revision_hash(*, raw_text: str, reply_to_message_id: int | None, has_media: bool) -> str:
    payload = f"{raw_text}\x00{reply_to_message_id or ''}\x00{int(has_media)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
