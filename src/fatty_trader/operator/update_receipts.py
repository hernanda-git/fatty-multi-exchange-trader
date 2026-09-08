"""Durable Telegram Bot API update receipts for at-most-once command handling."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class Cursor(Protocol):
    rowcount: int

    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> object: ...
    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Any: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class PostgresTelegramUpdateReceiptStore:
    """Claims Bot API update IDs before command execution, never re-claiming a duplicate."""

    def __init__(self, connection_factory: Callable[[], Connection]) -> None:
        self._connection_factory = connection_factory

    def claim(self, update_id: int) -> bool:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """INSERT INTO operator_telegram_updates (update_id)
                   VALUES (%s) ON CONFLICT (update_id) DO NOTHING""",
                (update_id,),
            )
            claimed = bool(cursor.rowcount == 1)
            connection.commit()
            return claimed
        except Exception:
            connection.rollback()
            raise

    def next_offset(self) -> int | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        cursor.execute("SELECT MAX(update_id) + 1 AS next_offset FROM operator_telegram_updates")
        row = cursor.fetchone()
        if row is None:
            return None
        value = row["next_offset"] if isinstance(row, dict) else row[0]
        return int(value) if value is not None else None
