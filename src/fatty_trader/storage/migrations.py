"""Versioned, additive, idempotent migrations for durable trading state.

Convention: ``MIGRATIONS`` is an append-only list of ``(version, sql)``
tuples with strictly increasing versions starting at 1. Version 0 is the
frozen ``INITIAL_SCHEMA_SQL`` that is already deployed. Each migration SQL
must be additive (``CREATE TABLE IF NOT EXISTS`` /
``CREATE UNIQUE INDEX IF NOT EXISTS``; future column additions via a
PostgreSQL ``DO`` block that checks ``information_schema`` first, or via the
per-statement tolerate-already-exists handling in :func:`apply_migrations`)
and must never drop tables, columns, or data.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final, Protocol

from fatty_trader.storage.schema import BITGET_DISPATCH_SCHEMA_SQL, LIVE_SCHEMA_SQL


class MigrationCursor(Protocol):
    def execute(self, statement: str) -> object: ...
    def fetchall(self) -> Sequence[object]: ...


SCHEMA_MIGRATIONS_SQL: Final = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

MIGRATIONS: Final = [
    (1, LIVE_SCHEMA_SQL),
    (
        2,
        """
        ALTER TABLE canonical_signals
        ADD COLUMN take_profits JSONB NOT NULL DEFAULT '[]';
        ALTER TABLE live_order_intents
        ADD COLUMN fee NUMERIC NOT NULL DEFAULT 0;
        ALTER TABLE live_order_intents
        ADD COLUMN provider_fill_ids JSONB NOT NULL DEFAULT '[]';
        """,
    ),
    (
        3,
        BITGET_DISPATCH_SCHEMA_SQL,
    ),
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS venue_kill_switches (
            scope TEXT PRIMARY KEY,
            active BOOLEAN NOT NULL DEFAULT FALSE,
            reason TEXT,
            latched_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
    ),
    (
        5,
        """
        ALTER TABLE notifications_outbox ADD COLUMN claimed_by TEXT;
        ALTER TABLE notifications_outbox ADD COLUMN lease_until TIMESTAMPTZ;
        ALTER TABLE notifications_outbox ADD COLUMN next_attempt_at TIMESTAMPTZ;
        ALTER TABLE notifications_outbox ADD COLUMN failed_at TIMESTAMPTZ;
        CREATE INDEX IF NOT EXISTS notifications_outbox_pending
        ON notifications_outbox (created_at, id)
        WHERE sent_at IS NULL AND failed_at IS NULL;
        """,
    ),
    (
        6,
        """
        ALTER TABLE live_order_intents
        DROP CONSTRAINT IF EXISTS live_order_intents_role_check;
        ALTER TABLE live_order_intents
        ADD CONSTRAINT live_order_intents_role_check CHECK (
            role IN ('ENTRY', 'SL', 'TP', 'CLOSE', 'EMERGENCY_CLOSE')
        );
        ALTER TABLE live_order_intents
        DROP CONSTRAINT IF EXISTS live_order_intents_state_check;
        ALTER TABLE live_order_intents
        ADD CONSTRAINT live_order_intents_state_check CHECK (
            state IN (
                'requested', 'acknowledged', 'submitted', 'partially_filled', 'filled',
                'cancelled', 'rejected', 'unknown', 'reconciled'
            )
        );
        """,
    ),
    (
        7,
        """
        CREATE TABLE IF NOT EXISTS canary_entry_reservations (
            dispatch_id UUID PRIMARY KEY REFERENCES dispatches(id),
            exchange TEXT NOT NULL CHECK (exchange IN ('binance', 'bitget')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS canary_entry_reservations_exchange
        ON canary_entry_reservations (exchange);
        """,
    ),
    (
        8,
        """
        CREATE TABLE IF NOT EXISTS operator_telegram_updates (
            update_id BIGINT PRIMARY KEY,
            claimed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
    ),
    (
        9,
        """
        CREATE TABLE IF NOT EXISTS intent_reconciliation_audit (
            id UUID PRIMARY KEY,
            exchange TEXT NOT NULL,
            client_order_id TEXT NOT NULL,
            prior_state TEXT NOT NULL,
            resolved_state TEXT NOT NULL,
            provider_snapshot JSONB NOT NULL,
            approval_reference TEXT NOT NULL,
            reconciled_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (exchange, client_order_id, resolved_state)
        );
        """,
    ),
    (
        10,
        """
        CREATE TABLE IF NOT EXISTS source_management_updates (
            id UUID PRIMARY KEY,
            source_message_id UUID NOT NULL REFERENCES telegram_messages(id),
            revision TEXT NOT NULL,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL CHECK (action IN ('TP1_BOOKED', 'SL_TO_ENTRY', 'CLOSE')),
            state TEXT NOT NULL CHECK (state IN (
                'queued', 'claimed', 'reconciliation-pending', 'failed', 'reconciled'
            )),
            claimed_by TEXT,
            claimed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source_message_id, revision, symbol, action)
        );
        CREATE INDEX IF NOT EXISTS source_management_updates_claim
        ON source_management_updates (created_at, id) WHERE state = 'queued';
        CREATE TABLE IF NOT EXISTS source_management_provider_intents (
            management_update_id UUID NOT NULL REFERENCES source_management_updates(id),
            client_order_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (management_update_id, client_order_id)
        );
        """,
    ),
]

# Error fragments that mean "this DDL was already applied" on PostgreSQL
# (psycopg raises them as UniqueViolation/DuplicateTable etc.) and SQLite.
# Anything else is re-raised so real failures stay loud.
_IDEMPOTENT_ERROR_MARKERS: Final = (
    "already exists",
    "duplicate",
)


def _iter_statements(sql: str) -> list[str]:
    return [part.strip() for part in sql.split(";") if part.strip()]


def _is_idempotent_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _IDEMPOTENT_ERROR_MARKERS)


def _applied_versions(cursor: MigrationCursor) -> set[int]:
    cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
    versions: set[int] = set()
    for row in cursor.fetchall():
        raw: Any
        if isinstance(row, dict):
            raw = row["version"]
        elif isinstance(row, (tuple, list)):
            raw = row[0]
        else:
            raw = row
        versions.add(int(raw))
    return versions


def apply_migrations(cursor: MigrationCursor) -> list[int]:
    """Apply pending migrations in version order; return versions applied.

    Creates ``schema_migrations`` on first use, skips versions already
    recorded, and records each newly applied version. Never drops data:
    every statement is additive, and already-applied DDL is tolerated.
    """
    cursor.execute(SCHEMA_MIGRATIONS_SQL)
    applied = _applied_versions(cursor)
    newly_applied: list[int] = []
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        for statement in _iter_statements(sql):
            try:
                cursor.execute(statement)
            except Exception as exc:
                if not _is_idempotent_error(exc):
                    raise
        cursor.execute(f"INSERT INTO schema_migrations (version) VALUES ({version})")
        newly_applied.append(version)
    return newly_applied
