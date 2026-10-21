"""PostgreSQL store for durable source-management queue and POST intent fence."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from typing import Any
from uuid import UUID, uuid4

from fatty_trader.analyzer.trade_management import ManagementAction
from fatty_trader.execution.source_management import SourceManagementUpdate
from fatty_trader.intake.freshness import SOURCE_ELIGIBLE_SQL


def _dead_letter_payload(update_id: UUID, attempts: int, error: str) -> str:
    """One alert when a parked update spends its reconciliation budget."""
    return json.dumps(
        {
            "kind": "reconciliation-dead-letter",
            "update_id": str(update_id),
            "attempts": attempts,
            "last_error": error[:200],
            "summary": (
                f"source-management update {update_id} spent its reconciliation budget "
                f"after {attempts} attempts and was marked failed: {error[:120]}"
            ),
        },
        sort_keys=True,
    )


def _outcome_payload(update: SourceManagementUpdate, state: str, error: str | None = None) -> str:
    """One operator-visible outcome for a manual management instruction.

    A manual instruction is only trustworthy when its result is visible: the
    operator must be able to see that the venue closed the position, moved the
    stop, or refused the request, without reading the database.
    """
    action = getattr(update.action, "value", update.action)
    message = f"{update.symbol} {action} → {state}"
    if error:
        message = f"{message} ({error[:160]})"
    return json.dumps(
        {
            "kind": "system-event",
            "update_id": str(update.id),
            "symbol": update.symbol,
            "action": str(action),
            "state": state,
            "error": (error or "")[:200],
            "message": message,
        },
        sort_keys=True,
    )


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
                UPDATE source_management_updates u SET state='failed',
                    last_error='management-execution-budget-expired',
                    updated_at=clock_timestamp()
                WHERE u.state IN ('queued', 'claimed')
                  AND u.deadline_at IS NOT NULL AND u.deadline_at <= now()
                  AND NOT EXISTS (SELECT 1 FROM source_management_provider_intents p
                      WHERE p.management_update_id=u.id)
                  AND NOT EXISTS (SELECT 1 FROM live_order_intents i
                      WHERE i.cancel_requested_by=u.id)
                """
            )
            cursor.execute(
                """
                WITH candidate AS (
                  SELECT id FROM source_management_updates
                  WHERE state IN ('queued', 'claimed', 'reconciliation-pending')
                    AND (state <> 'reconciliation-pending'
                         OR deadline_at IS NULL OR deadline_at <= now())
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

    def park(self, update_id: UUID, *, error: str, retry_seconds: int, max_attempts: int) -> str:
        """Count one attempt against the budget and dead-letter when it is spent.

        Returns the resulting state: ``reconciliation-pending`` while the budget
        remains (with a deadline), ``failed`` once it is exhausted — with one alert.
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        with closing(self._connection_factory()) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE source_management_updates u SET
                    attempts = u.attempts + 1,
                    last_error = %s,
                    updated_at = clock_timestamp(),
                    deadline_at = CASE WHEN u.attempts + 1 >= %s THEN NULL
                        ELSE clock_timestamp() + (%s * interval '1 second') END,
                    state = CASE WHEN u.attempts + 1 >= %s THEN 'failed'
                        ELSE 'reconciliation-pending' END
                WHERE u.id = %s
                RETURNING u.state, u.attempts
                """,
                (error[:400], max_attempts, max(0, retry_seconds), max_attempts, update_id),
            )
            row = cursor.fetchone()
            if row is None:
                connection.commit()
                raise LookupError(f"unknown management update: {update_id}")
            values = list(row.values()) if isinstance(row, dict) else list(row)
            state, attempts = str(values[0]), int(values[1])
            if state == "failed":
                cursor.execute(
                    """
                    INSERT INTO notifications_outbox (id, dedup_key, payload)
                    VALUES (%s, %s, %s::jsonb)
                    ON CONFLICT (dedup_key) DO NOTHING
                    """,
                    (
                        uuid4(),
                        f"source-update-dead-letter:{update_id}",
                        _dead_letter_payload(update_id, attempts, error),
                    ),
                )
            connection.commit()
        return state

    def update_state(self, update_id: UUID, state: str) -> None:
        with closing(self._connection_factory()) as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE source_management_updates SET state = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (state, update_id),
            )
            connection.commit()

    def alert_outcome(
        self, update: SourceManagementUpdate, state: str, error: str | None = None
    ) -> None:
        """Tell the operator how a manual management instruction ended."""
        with closing(self._connection_factory()) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO notifications_outbox (id, dedup_key, payload)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (dedup_key) DO NOTHING
                """,
                (
                    uuid4(),
                    f"source-update-outcome:{update.id}",
                    _outcome_payload(update, state, error),
                ),
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
