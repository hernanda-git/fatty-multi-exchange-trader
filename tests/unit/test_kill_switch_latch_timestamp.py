"""Exercise production latch SQL in memory; no runtime PostgreSQL access.

SQLite supports the CASE/COALESCE/ON CONFLICT subset used by this statement.
Only parameter syntax and the test clock are translated. Outbox SQL is captured
rather than executed (PostgreSQL JSONB); timestamp semantics run in a real engine.
"""
from __future__ import annotations

import sqlite3

import pytest

from fatty_trader.storage.reconciliation import PostgresReconciliationRepository


class MemorySQLConnection:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute(
            "CREATE TABLE venue_kill_switches "
            "(scope TEXT PRIMARY KEY, active BOOLEAN, reason TEXT, "
            "latched_at TEXT, updated_at TEXT)"
        )
        self.now = "2026-10-05 10:00:00"
        self.outbox = []

    def cursor(self):
        return self

    def execute(self, statement, params=()):
        if "INSERT INTO notifications_outbox" in statement:
            self.outbox.append(params)
            return
        sql = statement.replace("%s", "?").replace("CURRENT_TIMESTAMP", f"'{self.now}'")
        self.result = self.db.execute(sql, params)

    def fetchone(self):
        return self.result.fetchone()

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()


@pytest.mark.parametrize("active,original", [(True, None), (True, "2026-10-04 09:00:00"),
                                            (False, "2026-10-04 09:00:00")])
def test_latch_sets_missing_timestamp_preserves_active_epoch_and_resets_released_epoch(
    active, original,
):
    connection = MemorySQLConnection()
    connection.db.execute(
        "INSERT INTO venue_kill_switches VALUES (?, ?, ?, ?, ?)",
        ("bitget", active, "old", original, "2026-10-04 09:00:00"),
    )
    repository = PostgresReconciliationRepository(lambda: connection)
    try:
        repository.latch_kill_switch("bitget", "provider-fills-invalid")
        expected = original if active and original else connection.now
        first = connection.db.execute(
            "SELECT active, reason, latched_at FROM venue_kill_switches"
        ).fetchone()
        assert first == (True, "provider-fills-invalid", expected)
        connection.now = "2026-10-05 10:05:00"
        repository.latch_kill_switch("bitget", "provider-fills-read-failed")
        second = connection.db.execute(
            "SELECT latched_at, updated_at FROM venue_kill_switches"
        ).fetchone()
        assert second == (expected, connection.now)
        assert repository.kill_switch_reason("bitget") == "provider-fills-read-failed"
    finally:
        connection.db.close()
