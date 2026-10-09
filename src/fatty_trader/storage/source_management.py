"""PostgreSQL store for durable source-management queue and POST intent fence."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from typing import Any
from uuid import UUID

from fatty_trader.analyzer.trade_management import ManagementAction
from fatty_trader.execution.source_management import SourceManagementUpdate
from fatty_trader.intake.freshness import SOURCE_ELIGIBLE_SQL


class PostgresSourceManagementStore:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def claim(self, worker_id: str) -> SourceManagementUpdate | None:
        if not worker_id:
            raise ValueError("worker_id is required")
        with closing(self._connection_factory()) as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE source_management_updates u SET state='failed',
                    updated_at=clock_timestamp()
                FROM telegram_messages tm
                WHERE tm.id=u.source_message_id
                  AND u.state IN ('queued','claimed','reconciliation-pending')
                  AND NOT COALESCE(("""
                + SOURCE_ELIGIBLE_SQL.format(alias="tm")
                + """),false)
                  AND NOT EXISTS (SELECT 1 FROM source_management_provider_intents p
                      WHERE p.management_update_id=u.id)
                  AND NOT EXISTS (SELECT 1 FROM live_order_intents i
                      WHERE i.cancel_requested_by=u.id)"""
            )
            cursor.execute(
                """
                WITH candidate AS (
                  SELECT id FROM source_management_updates
                  WHERE state IN ('queued', 'claimed', 'reconciliation-pending')
                  ORDER BY updated_at, id FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE source_management_updates u SET state = 'claimed', claimed_by = %s,
                  claimed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                FROM candidate WHERE u.id = candidate.id
                RETURNING u.id, u.source_message_id, u.revision, u.symbol, u.action, u.state
                """,
                (worker_id,),
            )
            row = cursor.fetchone()
            connection.commit()
        return self._row(row) if row is not None else None

    def get(self, update_id: UUID) -> SourceManagementUpdate:
        with closing(self._connection_factory()) as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT id, source_message_id, revision, symbol, action, state
                   FROM source_management_updates WHERE id = %s""",
                (update_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise LookupError(f"unknown management update: {update_id}")
        return self._row(row)

    def update_state(self, update_id: UUID, state: str) -> None:
        with closing(self._connection_factory()) as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE source_management_updates SET state = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (state, update_id),
            )
            connection.commit()

    def persist_provider_intent(self, update_id: UUID, client_oid: str) -> bool:
        with closing(self._connection_factory()) as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO source_management_provider_intents
                   (management_update_id, client_order_id)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING client_order_id""",
                (update_id, client_oid),
            )
            inserted = cursor.fetchone() is not None
            connection.commit()
        return inserted

    def has_provider_intent(self, update_id: UUID, client_oid: str) -> bool:
        with closing(self._connection_factory()) as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT 1 FROM source_management_provider_intents
                   WHERE management_update_id = %s AND client_order_id = %s""",
                (update_id, client_oid),
            )
            return cursor.fetchone() is not None

    def management_is_eligible(self, update_id: UUID) -> bool:
        """Recheck source time under the update lock before a NEW mutation."""
        with closing(self._connection_factory()) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(("
                + SOURCE_ELIGIBLE_SQL.format(alias="tm")
                + "),false) AND tm.revision_hash=u.revision "
                "FROM source_management_updates u "
                "JOIN telegram_messages tm ON tm.id=u.source_message_id "
                "WHERE u.id=%s FOR UPDATE OF u",
                (update_id,),
            )
            row = cursor.fetchone()
            connection.commit()
            return row is not None and bool(row[0])

    def has_claimed_cancellation(self, update_id: UUID) -> bool:
        with closing(self._connection_factory()) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM live_order_intents "
                "WHERE exchange='bitget' AND role='ENTRY' AND cancel_requested_by=%s)",
                (update_id,),
            )
            return bool(cursor.fetchone()[0])

    @staticmethod
    def _row(row: Any) -> SourceManagementUpdate:
        values = list(row.values()) if isinstance(row, dict) else list(row)
        return SourceManagementUpdate(
            id=UUID(str(values[0])),
            source_message_id=UUID(str(values[1])),
            revision=str(values[2]),
            symbol=str(values[3]),
            action=ManagementAction(str(values[4])),
            state=str(values[5]),
        )
