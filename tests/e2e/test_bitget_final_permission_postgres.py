"""Disposable PostgreSQL concurrency; all provider mutations are offline fakes."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from decimal import Decimal
from threading import Event
from uuid import uuid4

import pytest

from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveIntentRecord
from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
from fatty_trader.execution.bitget_dispatch_repository import (
    BitgetDispatch,
    PostgresBitgetDispatchRepository,
)
from fatty_trader.storage.reconciliation import PostgresReconciliationRepository

psycopg = pytest.importorskip("psycopg")


@pytest.mark.parametrize("scope", ["global", "bitget"])
def test_kill_latched_while_final_source_read_waits_prevents_post(scope):
    dsn = os.environ.get("FATTY_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("requires disposable FATTY_TEST_POSTGRES_DSN")
    schema = "final_permission_" + uuid4().hex
    dispatch_id, source_id, message_id = uuid4(), uuid4(), uuid4()
    waiting = Event()
    connections = []

    def connect():
        c = psycopg.connect(dsn, options=f"-c search_path={schema}")
        connections.append(c)
        return c

    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        with connect() as c:
            c.execute(
                "CREATE TABLE telegram_messages (id uuid PRIMARY KEY, channel_id bigint, "
                "message_id bigint, ingestion_origin text, received_at timestamptz, "
                "entry_expires_at timestamptz, entry_rejection_reason text)"
            )
            c.execute("CREATE TABLE canonical_signals (id uuid PRIMARY KEY, message_id uuid)")
            c.execute("CREATE TABLE dispatches (id uuid PRIMARY KEY, source_id uuid, state text)")
            c.execute(
                "CREATE TABLE venue_kill_switches (scope text PRIMARY KEY, active bool, "
                "reason text, latched_at timestamptz, updated_at timestamptz)"
            )
            c.execute(
                "CREATE TABLE notifications_outbox (id uuid, dedup_key text UNIQUE, payload jsonb)"
            )
            c.execute(
                "INSERT INTO telegram_messages VALUES (%s,1,1,'realtime',clock_timestamp(),"
                "clock_timestamp()+interval '5 minutes',NULL)",
                (message_id,),
            )
            c.execute("INSERT INTO canonical_signals VALUES (%s,%s)", (source_id, message_id))
            c.execute(
                "INSERT INTO dispatches VALUES (%s,%s,'SUBMITTING')", (dispatch_id, source_id)
            )
        blocker = connect()
        blocker.execute("SELECT id FROM dispatches WHERE id=%s FOR UPDATE", (dispatch_id,))

        class Cursor:
            def __init__(self, cursor):
                self.cursor = cursor

            def execute(self, sql, params=()):
                if "FOR UPDATE" in sql:
                    waiting.set()
                return self.cursor.execute(sql, params)

            def fetchone(self):
                return self.cursor.fetchone()

        class Connection:
            def __init__(self):
                self.connection = connect()

            def cursor(self):
                return Cursor(self.connection.cursor())

            def commit(self):
                self.connection.commit()

            def rollback(self):
                self.connection.rollback()

            def close(self):
                self.connection.close()

        class Repository(PostgresBitgetDispatchRepository):
            reads = 0

            def entry_source_eligible(self, ident):
                self.reads += 1
                # Reproduce the last source read AFTER the two clear kill reads.
                if self.reads == 1:
                    return True
                return super().entry_source_eligible(ident)

            def unresolved_protection_issues(self):
                return []

        class Provider:
            posts = 0

            async def submit_entry_guarded(self, intent, check):
                with check() or nullcontext():
                    self.posts += 1
                raise AssertionError("ENTRY POST crossed a latched kill")

        repository = Repository(Connection)
        kill = PostgresReconciliationRepository(connect)
        provider = Provider()
        store = InMemoryLiveIntentStore()
        intent = LiveIntentRecord(
            "bitget", "offline-entry", "BTCUSDT", "BUY", requested_qty=Decimal("1")
        )
        store.save(intent)
        dispatch = BitgetDispatch(
            dispatch_id,
            "SUBMITTING",
            "test",
            1,
            "BTCUSDT",
            "LONG",
            Decimal("1"),
            Decimal("0.9"),
            (Decimal("1.1"),),
        )
        adapter = BitgetDispatchExecution(
            provider, store, dispatch_repository=repository, kill_switch=kill
        )
        from fatty_trader.exchanges.bitget.async_execution import BitgetEntryVeto

        def submit():
            try:
                asyncio.run(adapter._submit_guarded(dispatch, intent))
            except BitgetEntryVeto as exc:
                return exc.outcome

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(submit)
            try:
                assert waiting.wait(5), "final source query never reached lock"
                kill.latch_kill_switch(scope, "late-source-lock")
                assert kill.is_active(scope)
            finally:
                blocker.rollback()
            assert future.result(timeout=5) == "REJECTED"
        assert provider.posts == 0
        stored = store.get(intent.client_oid)
        assert stored is not None and stored.state == "rejected"

        # Conversely, a permission already granted must serialize a later latch
        # until the mutation boundary exits, including initially absent scopes.
        with connect() as c:
            c.execute("DELETE FROM venue_kill_switches")
        latch_started = Event()

        def later_latch():
            with connect() as c:
                c.execute("SET application_name = 'finalguard_later_latch'")
                latch_started.set()
                c.execute(
                    "INSERT INTO venue_kill_switches VALUES (%s,TRUE,'later',now(),now())",
                    (scope,),
                )

        with ThreadPoolExecutor(max_workers=1) as pool:
            with repository.entry_permission(dispatch_id) as veto:
                assert veto is None
                future = pool.submit(later_latch)
                assert latch_started.wait(5)
                from time import monotonic

                deadline = monotonic() + 5
                blocked = False
                with psycopg.connect(dsn, autocommit=True) as observer:
                    while monotonic() < deadline:
                        blocked = observer.execute(
                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                            "WHERE application_name='finalguard_later_latch' "
                            "AND wait_event_type='Lock')"
                        ).fetchone()[0]
                        if blocked:
                            break
                    assert blocked, "latch did not wait on the held ENTRY fence"
                assert not future.done()
            future.result(timeout=5)
        assert kill.is_active(scope)
    finally:
        for connection in connections:
            connection.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
