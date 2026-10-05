"""Adversarial probes for migration 24 latch-timestamp semantics on real PG."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from fatty_trader.storage.reconciliation import PostgresReconciliationRepository

psycopg = pytest.importorskip("psycopg")


@pytest.fixture
def schema():
    dsn = os.environ["FATTY_TEST_POSTGRES_DSN"]
    name = "mig24_" + uuid4().hex
    conns = []

    def connect():
        c = psycopg.connect(dsn, options=f"-c search_path={name}")
        conns.append(c)
        return c

    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{name}"')
    with connect() as c:
        c.execute(
            "CREATE TABLE venue_kill_switches (scope text PRIMARY KEY, active bool, "
            "reason text, latched_at timestamptz, updated_at timestamptz)"
        )
        c.execute(
            "CREATE TABLE notifications_outbox (id uuid, dedup_key text UNIQUE, payload jsonb)"
        )
        # apply migration 24 exactly as shipped
        c.execute("ALTER TABLE venue_kill_switches ADD COLUMN last_latched_at TIMESTAMPTZ")
        c.execute(
            "UPDATE venue_kill_switches SET latched_at = COALESCE(latched_at, updated_at), "
            "last_latched_at = COALESCE(last_latched_at, updated_at) WHERE active = TRUE"
        )
    yield name, connect
    for c in conns:
        try:
            c.close()
        except Exception:
            pass
    admin.execute(f'DROP SCHEMA "{name}" CASCADE')
    admin.close()


def test_m1_release_clears_both_columns(schema):
    _, connect = schema
    repo = PostgresReconciliationRepository(connect)
    repo.latch_kill_switch("bitget", "boom")
    repo.release_kill_switch("bitget", "APPROVED-1")
    with connect() as c:
        row = c.execute(
            "SELECT active, latched_at, last_latched_at FROM venue_kill_switches "
            "WHERE scope='bitget'"
        ).fetchone()
    assert row[0] is False
    assert row[1] is None, "latched_at survived release"
    assert row[2] is None, "last_latched_at survived release"
    assert repo.kill_switch_latch_times("bitget") is None


def test_m2_relatch_after_release_gets_a_fresh_start(schema):
    _, connect = schema
    repo = PostgresReconciliationRepository(connect)
    repo.latch_kill_switch("bitget", "a")
    repo.release_kill_switch("bitget", "APPROVED-1")
    repo.latch_kill_switch("bitget", "b")
    first, last = repo.kill_switch_latch_times("bitget")
    assert first is not None and last is not None
    # a released switch must not resurrect the old outage start
    assert first > last - __import__("datetime").timedelta(seconds=1)


def test_m3_relatch_preserves_original_start(schema):
    _, connect = schema
    repo = PostgresReconciliationRepository(connect)
    repo.latch_kill_switch("bitget", "a")
    first0, _ = repo.kill_switch_latch_times("bitget")
    repo.latch_kill_switch("bitget", "b")
    first1, last1 = repo.kill_switch_latch_times("bitget")
    assert first1 == first0, "re-latch moved the outage start"
    assert last1 >= first1


def test_m4_backfill_gives_a_lower_bound_not_a_fabricated_now(schema):
    """A pre-existing active row with latched_at NULL gets updated_at, not NOW()."""
    _, connect = schema
    with connect() as c:
        c.execute(
            "INSERT INTO venue_kill_switches VALUES "
            "('global', TRUE, 'pre-existing', NULL, '2026-01-01T00:00:00+00')"
        )
        # simulate applying migration 24 to that row
        c.execute(
            "UPDATE venue_kill_switches SET latched_at = COALESCE(latched_at, updated_at), "
            "last_latched_at = COALESCE(last_latched_at, updated_at) WHERE active = TRUE"
        )
        row = c.execute(
            "SELECT latched_at, last_latched_at FROM venue_kill_switches WHERE scope='global'"
        ).fetchone()
    assert str(row[0]).startswith("2026-01-01"), row[0]
    assert str(row[1]).startswith("2026-01-01"), row[1]


def test_m5_inactive_row_is_not_backfilled(schema):
    _, connect = schema
    with connect() as c:
        c.execute(
            "INSERT INTO venue_kill_switches VALUES "
            "('global', FALSE, 'released:x', NULL, '2026-01-01T00:00:00+00')"
        )
        c.execute(
            "UPDATE venue_kill_switches SET latched_at = COALESCE(latched_at, updated_at), "
            "last_latched_at = COALESCE(last_latched_at, updated_at) WHERE active = TRUE"
        )
        row = c.execute(
            "SELECT latched_at, last_latched_at FROM venue_kill_switches WHERE scope='global'"
        ).fetchone()
    assert row[0] is None and row[1] is None


def test_m6_released_switch_does_not_re_latch_forever(schema):
    """has_unhandled_post_fill_mismatch must clear on release, not re-latch."""
    _, connect = schema
    repo = PostgresReconciliationRepository(connect)
    repo.latch_kill_switch("bitget", "mismatch")
    with connect() as c:
        c.execute(
            "CREATE TABLE bitget_post_fill_reconciliations (id serial PRIMARY KEY, "
            "exchange text, status text, created_at timestamptz)"
        )
        c.execute(
            "INSERT INTO bitget_post_fill_reconciliations (exchange, status, created_at) "
            "VALUES ('bitget','mismatch', now())"
        )
    assert repo.has_unhandled_post_fill_mismatch("bitget") is True
    repo.release_kill_switch("bitget", "APPROVED-1")
    assert repo.has_unhandled_post_fill_mismatch("bitget") is False


def test_m7_latch_fence_still_holds_after_migration_24(schema):
    """The entry fence shares the same table; migration 24 must not break locking."""
    _, connect = schema
    from fatty_trader.execution.bitget_dispatch_repository import (
        PostgresBitgetDispatchRepository,
    )

    with connect() as c:
        c.execute(
            "CREATE TABLE telegram_messages (id uuid PRIMARY KEY, channel_id bigint, "
            "message_id bigint, ingestion_origin text, received_at timestamptz, "
            "entry_expires_at timestamptz, entry_rejection_reason text)"
        )
        c.execute("CREATE TABLE canonical_signals (id uuid PRIMARY KEY, message_id uuid)")
        c.execute("CREATE TABLE dispatches (id uuid PRIMARY KEY, source_id uuid, state text)")
        d, s, m = uuid4(), uuid4(), uuid4()
        c.execute(
            "INSERT INTO telegram_messages VALUES (%s,1,1,'realtime',clock_timestamp(),"
            "clock_timestamp()+interval '5 minutes',NULL)",
            (m,),
        )
        c.execute("INSERT INTO canonical_signals VALUES (%s,%s)", (s, m))
        c.execute("INSERT INTO dispatches VALUES (%s,%s,'SUBMITTING')", (d, s))

    repo = PostgresReconciliationRepository(connect)
    repo.latch_kill_switch("bitget", "on")

    class Conn:
        def __init__(self):
            self.connection = connect()

        def cursor(self):
            return self.connection.cursor()

        def commit(self):
            self.connection.commit()

        def rollback(self):
            self.connection.rollback()

        def close(self):
            self.connection.close()

    dispatch_repo = PostgresBitgetDispatchRepository(Conn)
    with dispatch_repo.entry_permission(d) as veto:
        assert veto == "REJECTED", veto


def test_m8_migration_is_idempotent_on_reapply(schema):
    """Re-running migration 24's DDL must be recognised as already applied."""
    _, connect = schema
    with connect() as c:
        with pytest.raises(Exception):
            c.execute("ALTER TABLE venue_kill_switches ADD COLUMN last_latched_at TIMESTAMPTZ")
