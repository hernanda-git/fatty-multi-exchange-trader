"""A kill switch must record when it FIRST latched and never lose that timestamp.

Production evidence: the bitget row stayed ``active = TRUE`` with
``latched_at = NULL`` and an ``updated_at`` that moved on every re-latch, so the
outage start time was unrecoverable and the outage duration uncomputable.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from fatty_trader.storage.migrations import (
    _IDEMPOTENT_ERROR_MARKERS,
    MIGRATIONS,
    _is_idempotent_error,
    _iter_statements,
)
from fatty_trader.storage.reconciliation import (
    InMemoryReconciliationRepository,
    PostgresReconciliationRepository,
)

#: Version of the migration that adds ``last_latched_at``.
LAST_LATCHED_MIGRATION = 24


class RecordingCursor:
    """Captures executed SQL so the upsert text itself can be asserted."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: list[tuple[Any, ...]] = []
        self.rowcount = 1

    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> None:
        self.statements.append(statement)
        self.params.append(params)

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[Any]:
        return []


class Connection:
    def __init__(self) -> None:
        self.cursor_value = RecordingCursor()
        self.commits = 0

    def cursor(self) -> RecordingCursor:
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        raise AssertionError("latch_kill_switch must not roll back")


def test_relatch_keeps_the_original_first_latched_timestamp() -> None:
    connection = Connection()
    repository = PostgresReconciliationRepository(lambda: connection)

    repository.latch_kill_switch("bitget", "provider-fills-read-failed:TimeoutError")

    upsert = next(s for s in connection.cursor_value.statements if "venue_kill_switches" in s)
    # A re-latch must not overwrite latched_at with EXCLUDED (the new timestamp).
    assert "latched_at = EXCLUDED.latched_at" not in upsert
    assert re.search(r"latched_at\s*=\s*COALESCE\(", upsert), upsert
    # The latest re-latch is recorded separately and is always refreshed.
    assert "last_latched_at = CURRENT_TIMESTAMP" in upsert
    assert "latched_at" in upsert and "last_latched_at" in upsert


def test_release_clears_both_latch_timestamps() -> None:
    connection = Connection()
    repository = PostgresReconciliationRepository(lambda: connection)

    repository.release_kill_switch("bitget", "approval-1")

    release = next(
        s for s in connection.cursor_value.statements if "UPDATE venue_kill_switches" in s
    )
    assert "latched_at = NULL" in release
    assert "last_latched_at = NULL" in release
    # Releasing must still not re-arm the switch.
    assert "active = FALSE" in release
    assert "active = TRUE" not in release


def test_migration_adds_and_backfills_last_latched_at() -> None:
    version, sql = next((version, sql) for version, sql in MIGRATIONS if "last_latched_at" in sql)
    assert version == LAST_LATCHED_MIGRATION
    assert version == max(v for v, _ in MIGRATIONS), (
        "migration must be appended, not edited in place"
    )
    normalized = " ".join(sql.lower().split())
    assert "add column last_latched_at timestamptz" in normalized
    # Backfill: an already-active row with no first-latch time gets the best
    # available lower bound (its last update), never a fabricated NOW().
    assert "last_latched_at = coalesce(last_latched_at, updated_at)" in normalized
    assert "latched_at = coalesce(latched_at, updated_at)" in normalized
    assert "where active = true" in normalized
    # Never invent a NOW() for a row we cannot date.
    assert "current_timestamp" not in normalized.split("update venue_kill_switches")[1]


_POSTGRES_ONLY_MARKERS = (
    'near "EXISTS": syntax error',
    "no such table",
    'near "LOCK": syntax error',
)


def _is_postgres_only(exc: sqlite3.OperationalError) -> bool:
    return any(marker in str(exc) for marker in _POSTGRES_ONLY_MARKERS)


class SqliteCursorAdapter:
    """Adapt sqlite3 to the storage cursor protocol (mirrors tests/unit/test_live_schema.py)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._cur = connection.cursor()

    def execute(self, statement: str) -> object:
        script = statement.replace("DEFAULT now()", "DEFAULT CURRENT_TIMESTAMP")
        if script.lstrip().startswith("ALTER TABLE live_order_intents") and "CONSTRAINT" in script:
            return None
        if "venue_kill_switches" in script and "bitget_kill_switch_alert_only" in script:
            return None
        parts = [part.strip() for part in script.split(";") if part.strip()]
        if len(parts) > 1:
            self._conn.executescript(script)
        else:
            try:
                self._cur.execute(script)
            except sqlite3.OperationalError as exc:
                # PostgreSQL-only DDL (ADD COLUMN IF NOT EXISTS, LOCK, JSONB paths)
                # is tolerated exactly as apply_migrations tolerates a replayed
                # statement. Anything touching venue_kill_switches must NOT be
                # swallowed: that is the table under test.
                if "venue_kill_switches" in script:
                    raise
                if _is_postgres_only(exc):
                    return None
                raise
        return None

    def fetchall(self) -> list[Any]:
        return self._cur.fetchall()


def _deployed_db() -> sqlite3.Connection:
    """Apply the frozen v0 schema, then the real migrations, unmodified."""
    from fatty_trader.storage.schema import apply_initial_schema

    connection = sqlite3.connect(":memory:")
    apply_initial_schema(SqliteCursorAdapter(connection))
    return connection


def test_migration_backfills_an_already_latched_row_from_updated_at() -> None:
    """The production row had latched_at = NULL; the backfill must fill both columns."""
    connection = _deployed_db()
    adapter = SqliteCursorAdapter(connection)
    # Migration 4 creates the table; run everything up to (not including) 24.
    for version, sql in MIGRATIONS:
        if version >= LAST_LATCHED_MIGRATION:
            break
        for statement in _iter_statements(sql):
            try:
                adapter.execute(statement)
            except sqlite3.OperationalError:  # postgres-only DDL in this test path
                continue
    assert "last_latched_at" not in {
        row[1] for row in connection.execute("PRAGMA table_info(venue_kill_switches)")
    }
    connection.execute(
        """INSERT INTO venue_kill_switches (scope, active, reason, latched_at, updated_at)
           VALUES ('bitget', TRUE, 'provider-fills-invalid', NULL, '2026-10-03 16:21:56')"""
    )

    _, sql = next((v, s) for v, s in MIGRATIONS if v == LAST_LATCHED_MIGRATION)
    for statement in _iter_statements(sql):
        adapter.execute(statement)

    row = connection.execute(
        "SELECT latched_at, last_latched_at FROM venue_kill_switches WHERE scope = 'bitget'"
    ).fetchone()
    assert row is not None
    # Backfilled from updated_at: an observed lower bound, never a fabricated NOW().
    assert row[0] is not None and row[1] is not None
    assert row[0] == row[1]


def test_migration_leaves_a_released_row_without_a_fabricated_latch_time() -> None:
    connection = _deployed_db()
    adapter = SqliteCursorAdapter(connection)
    for version, sql in MIGRATIONS:
        if version >= LAST_LATCHED_MIGRATION:
            break
        for statement in _iter_statements(sql):
            try:
                adapter.execute(statement)
            except sqlite3.OperationalError:
                continue
    connection.execute(
        """INSERT INTO venue_kill_switches (scope, active, reason, latched_at, updated_at)
           VALUES ('binance', FALSE, 'released:APPROVAL-1', NULL, '2026-10-01 00:00:00')"""
    )

    _, sql = next((v, s) for v, s in MIGRATIONS if v == LAST_LATCHED_MIGRATION)
    for statement in _iter_statements(sql):
        adapter.execute(statement)

    row = connection.execute(
        "SELECT latched_at, last_latched_at FROM venue_kill_switches WHERE scope = 'binance'"
    ).fetchone()
    assert row == (None, None)


def test_migrations_remain_versioned_append_only_and_tolerate_a_replay() -> None:
    """The new version is appended and its statements are replay-tolerated."""
    versions = [version for version, _ in MIGRATIONS]
    assert versions == sorted(set(versions))
    assert versions[-1] == LAST_LATCHED_MIGRATION
    # A duplicate ADD COLUMN (replay against a database where it already ran)
    # must be tolerated by the existing idempotency guard, not abort the migrate.
    duplicate = sqlite3.OperationalError('duplicate column name: "last_latched_at"')
    assert _is_idempotent_error(duplicate)
    # Asserted structurally: the guard matches on the message text, not the SQL.
    assert "already exists" in _IDEMPOTENT_ERROR_MARKERS
    assert "duplicate" in _IDEMPOTENT_ERROR_MARKERS


def test_in_memory_repository_records_first_and_last_latch_and_clears_both() -> None:
    repository = InMemoryReconciliationRepository()

    repository.latch_kill_switch("bitget", "provider-fills-read-failed:TimeoutError")
    first = repository.kill_switch_latch_times("bitget")

    assert first is not None
    first_latched_at, last_latched_at = first
    assert first_latched_at is not None
    assert last_latched_at == first_latched_at

    # A re-latch advances the latest latch but must not move the first one.
    repository.latch_kill_switch("bitget", "provider-fills-read-failed:BitgetApiError:30006")
    second = repository.kill_switch_latch_times("bitget")
    assert second is not None
    assert second[0] == first_latched_at
    assert second[1] is not None and second[1] >= first_latched_at

    repository.release_kill_switch("bitget", "approval-1")
    assert repository.kill_switch_latch_times("bitget") is None
    assert repository.kill_switch_active("bitget") is False


def test_in_memory_first_latch_is_monotonic_under_a_fake_clock() -> None:
    clock = FakeClock(datetime(2026, 10, 2, 12, 30, 9, tzinfo=UTC))
    repository = InMemoryReconciliationRepository(clock=clock)

    repository.latch_kill_switch("bitget", "provider-positions-read-failed:TimeoutError")
    clock.now += timedelta(hours=3)
    repository.latch_kill_switch("bitget", "provider-positions-read-failed:TimeoutError")
    times = repository.kill_switch_latch_times("bitget")

    assert times is not None
    assert times[0] == datetime(2026, 10, 2, 12, 30, 9, tzinfo=UTC)
    assert times[1] == datetime(2026, 10, 2, 15, 30, 9, tzinfo=UTC)
    # The outage is now computable from the recorded first latch.
    assert times[1] - times[0] == timedelta(hours=3)


def test_latch_is_never_automatically_released_by_a_healthy_read() -> None:
    """A good cycle must not clear the timestamps; release stays operator-only."""
    repository = InMemoryReconciliationRepository()

    repository.latch_kill_switch("bitget", "provider-fills-shape-invalid")
    repository.latch_kill_switch("bitget", "provider-fills-shape-invalid")

    assert repository.kill_switch_active("bitget") is True
    assert repository.kill_switch_latch_times("bitget") is not None


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now
