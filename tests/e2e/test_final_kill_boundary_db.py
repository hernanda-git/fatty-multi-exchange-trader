"""Final ENTRY kill boundary: real service graph/PG, offline venue only."""

# ruff: noqa: F401, F811
from threading import Event, Thread
from time import monotonic, sleep
from uuid import uuid4

import pytest
from source_freshness_fixtures import eligible_dispatch_source
from test_bitget_service_admission_db import fixtures
from test_postgres_migration_and_margin_admission import _connect, _migrate, postgres_schema

from fatty_trader import service
from fatty_trader.execution.bitget_dispatcher import BitgetDispatcher, DispatchGate
from fatty_trader.storage.reconciliation import PostgresReconciliationRepository


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["global", "bitget"])
@pytest.mark.parametrize("activation", ["preflight", "leverage", "eligibility_lock"])
async def test_service_final_kill_blocks_known_unsent_entry(
    postgres_schema, monkeypatch, scope, activation
):
    import psycopg

    from fatty_trader.exchanges.bitget import async_venue

    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    connect = psycopg.connect

    def factory(*args, **kwargs):
        return connect(dsn, options=f"-c search_path={schema}", **kwargs)

    monkeypatch.setattr(psycopg, "connect", factory)
    kill = PostgresReconciliationRepository(factory)
    dispatch_id = uuid4()
    with factory() as c:
        c.execute(
            "INSERT INTO dispatches (id,source_type,source_id,revision,exchange,state) "
            "VALUES (%s,'admission',%s,%s,'bitget','QUEUED')",
            (dispatch_id, uuid4(), "a" * 64),
        )
        eligible_dispatch_source(c, dispatch_id)
        c.execute(
            "UPDATE canonical_signals SET pair_token='PENDLEUSDT', "
            "entry_price=2.32,stop_loss=2.25,take_profits='[2.60]'::jsonb"
        )

    lock_ready = Event()
    lock_wait_seen = Event()
    lock_errors = []
    lock_thread = None

    def activate_during_eligibility_lock():
        try:
            with factory() as locker, factory(autocommit=True) as observer:
                locker.execute("SELECT id FROM dispatches WHERE id=%s FOR UPDATE", (dispatch_id,))
                locker_pid = locker.info.backend_pid
                lock_ready.set()
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    # Match the query prefix: the added cutoff can push its
                    # trailing FOR UPDATE past track_activity_query_size. Blocking
                    # PID still proves the same real dispatch row-lock wait.
                    row = observer.execute(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE %s = ANY(pg_blocking_pids(pid)) "
                        "AND query LIKE 'SELECT d.state, COALESCE%%')",
                        (locker_pid,),
                    ).fetchone()
                    assert row is not None
                    if row[0]:
                        lock_wait_seen.set()
                        kill.latch_kill_switch(scope, "offline-eligibility-lock-race")
                        # Commit releases the real row lock only AFTER the kill is durable.
                        return
                    sleep(0.01)
                raise AssertionError("final eligibility query never waited on dispatch row lock")
        except BaseException as exc:
            lock_errors.append(exc)
            lock_ready.set()

    class Venue(fixtures["Venue"]):
        def __init__(self, client):
            super().__init__()
            self.leverage_calls = 0

        async def preflight(self, symbol):
            snapshot = await super().preflight(symbol)
            if activation == "preflight":
                kill.latch_kill_switch(scope, "offline-race")
            return snapshot

        async def ensure_leverage(self, symbol, leverage):
            nonlocal lock_thread
            self.leverage_calls += 1
            if activation == "leverage":
                kill.latch_kill_switch(scope, "offline-race")
            elif activation == "eligibility_lock":
                lock_thread = Thread(target=activate_during_eligibility_lock)
                lock_thread.start()
                assert lock_ready.wait(5), "row-lock holder did not start"
                assert not lock_errors

    class Provider:
        def __init__(self):
            self.posts = []

        async def get_all_positions(self):
            return []

        async def get_pending_orders(self):
            return []

        async def place_entry_order(self, **kwargs):
            self.posts.append(kwargs)
            raise RuntimeError("offline-entry-post-recorded")

    provider = Provider()
    monkeypatch.setattr(async_venue, "AsyncBitgetVenue", Venue)
    runtime = service.build_bitget_execution_runtime(
        dict(
            TRADER_MODE="DEMO",
            BITGET_MODE="DEMO",
            BITGET_EXECUTION_ENABLED="1",
            BITGET_API_KEY="fake",
            BITGET_API_SECRET="fake",
            BITGET_API_PASSPHRASE="fake",
            BITGET_CANARY_MAX_ORDERS="1",
            BITGET_CANARY_SYMBOL="PENDLEUSDT",
            BITGET_APPROVAL_REFERENCE="offline",
            BITGET_MAX_CLOCK_SKEW_MS="5000",
            BITGET_MAX_MARGIN_PER_TRADE_USDT="1",
        ),
        client_factory=lambda *args: provider,
    )
    assert runtime is not None
    assert await runtime.execution.recover_entry_lifecycles() == 0
    assert runtime.execution.recovery_ready
    dispatcher = BitgetDispatcher(
        runtime.execution._dispatch_repository,
        gate=DispatchGate(execution_enabled=True, canary_max_orders=1),
        execution=runtime.execution,
        preflight=runtime.preflight,
        kill_switch=kill,
    )
    try:
        outcome = await dispatcher.run_once("offline", 30)
    finally:
        if lock_thread is not None:
            lock_thread.join(timeout=15)
    if activation == "eligibility_lock":
        assert lock_thread is not None and not lock_thread.is_alive()
        assert not lock_errors
        assert lock_wait_seen.is_set(), "kill must activate during an observed real PG lock wait"
    assert kill.is_active(scope)
    assert provider.posts == []
    assert outcome == "rejected"
    with factory() as c:
        assert c.execute("SELECT state FROM dispatches WHERE id=%s", (dispatch_id,)).fetchone() == (
            "REJECTED",
        )
        assert c.execute("SELECT state,filled_qty FROM live_order_intents").fetchone() == (
            "rejected",
            0,
        )
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("released",)
        assert c.execute(
            "SELECT count(*) FROM dispatch_transitions WHERE to_state='UNKNOWN'"
        ).fetchone() == (0,)
        assert c.execute("SELECT count(*) FROM canary_entry_reservations").fetchone() == (0,)
    assert await dispatcher.run_once("offline", 30) == "idle"
    assert provider.posts == []
