"""Adversarial probes written by an independent reviewer. Read-only review aid."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from decimal import Decimal
from uuid import uuid4

import pytest

from fatty_trader.exchanges.bitget.async_execution import BitgetEntryVeto
from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveIntentRecord
from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
from fatty_trader.execution.bitget_dispatch_repository import (
    BitgetDispatch,
    PostgresBitgetDispatchRepository,
)
from fatty_trader.storage.reconciliation import PostgresReconciliationRepository

psycopg = pytest.importorskip("psycopg")


def _schema(ddl_needed: bool = True):
    dsn = os.environ["FATTY_TEST_POSTGRES_DSN"]
    schema = "adv_" + uuid4().hex
    conns = []

    def connect():
        c = psycopg.connect(dsn, options=f"-c search_path={schema}")
        conns.append(c)
        return c

    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    if ddl_needed:
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
            c.execute("ALTER TABLE venue_kill_switches ADD COLUMN last_latched_at timestamptz")
            c.execute(
                "CREATE TABLE notifications_outbox (id uuid, dedup_key text UNIQUE, payload jsonb)"
            )
    return schema, connect, admin, conns


def _seed(connect, dispatch_id, source_id, message_id, state="SUBMITTING"):
    with connect() as c:
        c.execute(
            "INSERT INTO telegram_messages VALUES (%s,1,1,'realtime',clock_timestamp(),"
            "clock_timestamp()+interval '5 minutes',NULL)",
            (message_id,),
        )
        c.execute("INSERT INTO canonical_signals VALUES (%s,%s)", (source_id, message_id))
        c.execute("INSERT INTO dispatches VALUES (%s,%s,%s)", (dispatch_id, source_id, state))


def _wrap(connect):
    class Cursor:
        def __init__(self, c):
            self.c = c

        def execute(self, sql, params=()):
            return self.c.execute(sql, params)

        def fetchone(self):
            return self.c.fetchone()

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

    return Connection


def _drop(schema, admin, conns):
    for c in conns:
        try:
            c.close()
        except Exception:
            pass
    admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
    admin.close()


# ---------------------------------------------------------------- A: kill fence


@pytest.mark.parametrize("scope", ["global", "bitget"])
def test_a1_existing_active_kill_blocks_permission(scope):
    schema, connect, admin, conns = _schema()
    try:
        d, s, m = uuid4(), uuid4(), uuid4()
        _seed(connect, d, s, m)
        kill = PostgresReconciliationRepository(connect)
        kill.latch_kill_switch(scope, "already-on")
        repo = PostgresBitgetDispatchRepository(_wrap(connect))
        with repo.entry_permission(d) as veto:
            assert veto == "REJECTED", veto
    finally:
        _drop(schema, admin, conns)


def test_a2_kill_latched_from_other_connection_during_fence_is_serialized():
    """Fence held -> latch from a DIFFERENT connection must block until exit."""
    schema, connect, admin, conns = _schema()
    try:
        d, s, m = uuid4(), uuid4(), uuid4()
        _seed(connect, d, s, m)
        repo = PostgresBitgetDispatchRepository(_wrap(connect))
        with repo.entry_permission(d) as veto:
            assert veto is None
            done = threading.Event()

            def latch():
                with psycopg.connect(
                    os.environ["FATTY_TEST_POSTGRES_DSN"],
                    autocommit=True,
                    options=f"-c search_path={schema}",
                ) as c:
                    c.execute(
                        "INSERT INTO venue_kill_switches VALUES ('bitget',TRUE,'x',now(),now()) "
                        "ON CONFLICT (scope) DO UPDATE SET active=TRUE"
                    )
                done.set()

            t = threading.Thread(target=latch, daemon=True)
            t.start()
            # The POST stands in for however long the fence is held.
            time.sleep(1.0)
            assert not done.is_set(), "latch was NOT blocked by the held fence"
        assert done.wait(10)
        t.join()
        kill = PostgresReconciliationRepository(connect)
        assert kill.is_active("bitget")
    finally:
        _drop(schema, admin, conns)


def test_a3_unknown_scope_row_also_fenced():
    """A scope row that does not exist yet must still be INSERT-blocked by SHARE."""
    schema, connect, admin, conns = _schema()
    try:
        d, s, m = uuid4(), uuid4(), uuid4()
        _seed(connect, d, s, m)
        repo = PostgresBitgetDispatchRepository(_wrap(connect))
        with repo.entry_permission(d) as veto:
            assert veto is None
            with pytest.raises(psycopg.errors.QueryCanceled):
                with psycopg.connect(
                    os.environ["FATTY_TEST_POSTGRES_DSN"],
                    autocommit=True,
                    options=f"-c search_path={schema}",
                ) as c:
                    c.execute("SET statement_timeout='600ms'")
                    c.execute(
                        "INSERT INTO venue_kill_switches VALUES ('global',TRUE,'x',now(),now())"
                    )
    finally:
        _drop(schema, admin, conns)


def test_a4_fence_defers_kill_until_after_post_which_is_the_residual_race():
    """Documents the residual window: kill latched AFTER the fence is granted.

    The fence serializes, it does not preview. A latch committing after the fence
    is granted but before the POST still allows the POST. Measure it.
    """
    schema, connect, admin, conns = _schema()
    try:
        d, s, m = uuid4(), uuid4(), uuid4()
        _seed(connect, d, s, m)
        repo = PostgresBitgetDispatchRepository(_wrap(connect))
        kill = PostgresReconciliationRepository(connect)
        posted = threading.Event()
        with repo.entry_permission(d) as veto:
            assert veto is None
            # Simulate the latch committing in the instant after the fence read.
            with psycopg.connect(
                os.environ["FATTY_TEST_POSTGRES_DSN"],
                autocommit=True,
                options=f"-c search_path={schema}",
            ) as c:
                c.execute("SET lock_timeout='1s'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    c.execute(
                        "INSERT INTO venue_kill_switches VALUES ('bitget',TRUE,'raced',now(),now())"
                    )
            posted.set()
        assert posted.is_set()
    finally:
        _drop(schema, admin, conns)


@pytest.mark.parametrize("scope", ["global", "bitget"])
def test_a5_latch_commits_while_fence_blocks_on_source_row_lock(scope):
    """Fence blocked on the source FOR UPDATE; latch commits; fence then proceeds.

    The latch must be observed: veto must be REJECTED and no POST may occur.
    """
    schema, connect, admin, conns = _schema()
    try:
        d, s, m = uuid4(), uuid4(), uuid4()
        _seed(connect, d, s, m, state="QUEUED")
        waiting = threading.Event()
        blocker = connect()
        blocker.execute("SELECT id FROM dispatches WHERE id=%s FOR UPDATE", (d,))

        class Repository(PostgresBitgetDispatchRepository):
            def unresolved_protection_issues(self):
                return []

        class Cursor:
            def __init__(self, c):
                self.c = c

            def execute(self, sql, params=()):
                if "FOR UPDATE" in sql:
                    waiting.set()
                return self.c.execute(sql, params)

            def fetchone(self):
                return self.c.fetchone()

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

        class Provider:
            posts = 0

            async def submit_entry_guarded(self, intent, check):
                with check() or nullcontext():
                    self.posts += 1
                raise AssertionError("POST under kill")

        kill = PostgresReconciliationRepository(connect)
        provider = Provider()
        adapter = BitgetDispatchExecution(
            provider,
            InMemoryLiveIntentStore(),
            dispatch_repository=Repository(Connection),
            kill_switch=kill,
        )
        intent = LiveIntentRecord("bitget", "adv-a5", "BTCUSDT", "BUY", requested_qty=Decimal("1"))
        adapter._store.save(intent)
        dispatch = BitgetDispatch(
            d, "QUEUED", "t", 1, "BTCUSDT", "LONG", Decimal("1"), Decimal("0.9"), (Decimal("1.1"),)
        )

        result = {}

        def run():
            try:
                result["out"] = asyncio.run(adapter._submit_guarded(dispatch, intent))
            except BaseException as exc:  # noqa: BLE001
                result["exc"] = exc

        th = threading.Thread(target=run)
        th.start()
        assert waiting.wait(10), "fence never reached the source row lock"
        kill.latch_kill_switch(scope, "raced-latch")
        assert kill.is_active(scope)
        blocker.rollback()
        th.join(15)
        assert not th.is_alive()
        assert result.get("exc") is None or isinstance(result["exc"], BitgetEntryVeto), result
        if "out" in result:
            assert result["out"] == "REJECTED", result
        else:
            assert result["exc"].outcome == "REJECTED", result
        assert provider.posts == 0
    finally:
        _drop(schema, admin, conns)


def test_a6_no_await_escapes_the_fence_during_the_post():
    """The POST await must run inside the with-block; verify by lock observability."""
    schema, connect, admin, conns = _schema()
    try:
        d, s, m = uuid4(), uuid4(), uuid4()
        _seed(connect, d, s, m)
        repo = PostgresBitgetDispatchRepository(_wrap(connect))

        observed = {}

        class Provider:
            async def submit_entry_guarded(self, intent, check):
                ctx = check() or nullcontext()
                with ctx:
                    # while inside, another connection must be lock-blocked
                    import psycopg as pg

                    with pg.connect(
                        os.environ["FATTY_TEST_POSTGRES_DSN"],
                        autocommit=True,
                        options=f"-c search_path={schema}",
                    ) as c:
                        c.execute("SET lock_timeout='400ms'")
                        try:
                            c.execute(
                                "INSERT INTO venue_kill_switches VALUES "
                                "('global',TRUE,'nope',now(),now())"
                            )
                            observed["blocked"] = False
                        except psycopg.errors.LockNotAvailable:
                            observed["blocked"] = True
                return "done"

        store = InMemoryLiveIntentStore()
        intent = LiveIntentRecord("bitget", "x", "BTCUSDT", "BUY", requested_qty=Decimal("1"))
        store.save(intent)
        dispatch = BitgetDispatch(
            d,
            "SUBMITTING",
            "t",
            1,
            "BTCUSDT",
            "LONG",
            Decimal("1"),
            Decimal("0.9"),
            (Decimal("1.1"),),
        )
        adapter = BitgetDispatchExecution(
            Provider(),
            store,
            dispatch_repository=repo,
            kill_switch=PostgresReconciliationRepository(connect),
        )

        class Repo2(PostgresBitgetDispatchRepository):
            def unresolved_protection_issues(self):
                return []

        adapter._dispatch_repository = Repo2(_wrap(connect))
        asyncio.run(adapter._submit_guarded(dispatch, intent))
        assert observed.get("blocked") is True, observed
    finally:
        _drop(schema, admin, conns)


def test_a7_release_kill_switch_path():
    """release_kill_switch uses UPDATE -> ROW EXCLUSIVE; must also be fenced."""
    schema, connect, admin, conns = _schema()
    try:
        d, s, m = uuid4(), uuid4(), uuid4()
        _seed(connect, d, s, m)
        repo = PostgresBitgetDispatchRepository(_wrap(connect))
        kill = PostgresReconciliationRepository(connect)
        kill.latch_kill_switch("bitget", "on")
        with repo.entry_permission(d) as veto:
            assert veto == "REJECTED"
    finally:
        _drop(schema, admin, conns)


def test_a8_kill_veto_does_not_leak_a_committed_post():
    """If the caller raises inside the fence, rollback must occur."""
    schema, connect, admin, conns = _schema()
    try:
        d, s, m = uuid4(), uuid4(), uuid4()
        _seed(connect, d, s, m)
        repo = PostgresBitgetDispatchRepository(_wrap(connect))
        with pytest.raises(RuntimeError):
            with repo.entry_permission(d):
                raise RuntimeError("provider blew up")
        # repository still usable
        with repo.entry_permission(d) as veto:
            assert veto is None
    finally:
        _drop(schema, admin, conns)


def test_a9_missing_dispatch_row_fails_closed():
    schema, connect, admin, conns = _schema()
    try:
        repo = PostgresBitgetDispatchRepository(_wrap(connect))
        with repo.entry_permission(uuid4()) as veto:
            assert veto == "EXPIRED", veto
    finally:
        _drop(schema, admin, conns)
