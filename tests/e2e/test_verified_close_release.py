"""Verified close release proofs using a disposable real PostgreSQL schema."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from test_durable_entry_admission import repo
from test_durable_entry_admission import request as admission_request
from test_postgres_migration_and_margin_admission import _connect, _migrate
from test_postgres_migration_and_margin_admission import postgres_schema as postgres_schema


@pytest.mark.parametrize(
    "change",
    [
        {"authenticated": False},
        {"flat_quantity": Decimal("1")},
        {"flat_quantity": Decimal("NaN")},
        {"fills": ()},
        {"environment": "demo"},
        {"environment": "LIVE"},
        {"symbol": "ETHUSDT"},
        {"dispatch_id": uuid4()},
        {"entry_client_order_id": "wrong-entry"},
        {"close_client_order_id": "wrong-close"},
        {"symbol": " BTCUSDT"},
        {"observed_at": datetime.now(UTC) - timedelta(minutes=2)},
        {"observed_at": datetime.now(UTC) + timedelta(minutes=2)},
        {"observed_at": datetime.now()},
        {"max_snapshot_age": timedelta(0)},
    ],
)
def test_unverified_close_cannot_release(postgres_schema, change):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    repository, admission, evidence = owned_close(dsn, schema)
    evidence.update(change)
    result = repository.release_verified_close(**evidence)
    assert not result.accepted
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("consumed",)


def request(dsn, schema, **overrides):
    """Provide genuine eligible canonical intake for the admission seam."""
    args = admission_request(dsn, schema, **overrides)
    message, signal = uuid4(), uuid4()
    with _connect(dsn, schema) as c:
        c.execute(
            (
                "INSERT INTO telegram_messages\n"
                "            (id, channel_id, message_id, revision_hash, raw_text, "
                "intake_state,\n"
                "             ingestion_origin, entry_expires_at)\n"
                "            VALUES (%s, 1, %s, %s, 'test-only', 'ANALYZED', "
                "'realtime', clock_timestamp()+interval '5 minutes')"
            ),
            (message, message.int % (2**62), "a" * 64),
        )
        c.execute(
            """INSERT INTO canonical_signals
            (id,message_id,revision,pair_token,direction,entry_price,stop_loss)
            VALUES (%s,%s,%s,'BTC','LONG',100,90)""",
            (signal, message, "a" * 64),
        )
        c.execute(
            "UPDATE dispatches SET source_id=%s,source_type='canonical_signal' WHERE id=%s",
            (signal, args["dispatch_id"]),
        )
    return args


def owned_close(dsn, schema):
    args = request(dsn, schema)
    repository = repo(dsn, schema)
    admission = repository.reserve(**args)
    assert admission.accepted, admission.reason
    repository.resolve(admission.reservation_id, "FILLED")
    close_oid = str(uuid4())
    with _connect(dsn, schema) as c:
        c.execute(
            """INSERT INTO live_order_intents
            (id,exchange,client_order_id,symbol,side,role,state,requested_qty,filled_qty,
             provider_order_id,margin_reservation_id,balance_snapshot_id,planned_margin_usdt,
             leverage,margin_mode)
            VALUES (%s,'bitget',%s,'BTCUSDT','BUY','ENTRY','filled',1,1,%s,%s,%s,1,
                    20,'ISOLATED')""",
            (
                uuid4(),
                args["client_order_id"],
                str(uuid4()),
                admission.reservation_id,
                admission.snapshot_id,
            ),
        )
        c.execute(
            (
                "INSERT INTO live_order_intents\n"
                "            (id,exchange,client_order_id,symbol,side,role,state,reques"
                "ted_qty,filled_qty,\n"
                "             provider_order_id) VALUES "
                "(%s,'bitget',%s,'BTCUSDT','SELL','CLOSE','requested',1,0,'close-order')"
            ),
            (uuid4(), close_oid),
        )
    from types import SimpleNamespace

    from fatty_trader.storage.live_intents import PostgresLiveIntentStore

    store = PostgresLiveIntentStore(lambda: _connect(dsn, schema))
    assert store.verified_close_lifecycle.bind_requested_close(
        SimpleNamespace(environment="DEMO"), close_oid
    )
    with _connect(dsn, schema) as c:
        c.execute(
            "UPDATE live_order_intents SET state='filled',filled_qty=1,"
            "provider_fill_ids='[\"real-close-fill\"]'::jsonb WHERE client_order_id=%s",
            (close_oid,),
        )
        c.execute(
            """INSERT INTO fills
            (id,exchange,client_order_id,provider_fill_id,symbol,price,quantity,fee,realized_pnl)
            VALUES (%s,'bitget',%s,'real-close-fill','BTCUSDT',100,1,.1,2)""",
            (uuid4(), close_oid),
        )
    evidence: dict[str, Any] = dict(
        environment="DEMO",
        symbol="BTCUSDT",
        dispatch_id=args["dispatch_id"],
        entry_client_order_id=args["client_order_id"],
        close_client_order_id=close_oid,
        authenticated=True,
        observed_at=datetime.now(UTC),
        flat_quantity=Decimal(0),
        fills=(
            dict(
                provider_fill_id="real-close-fill",
                provider_order_id="close-order",
                symbol="BTCUSDT",
                side="SELL",
                trade_side="close",
                quantity=Decimal(1),
                price=Decimal(100),
                fee=Decimal(".1"),
                realized_pnl=Decimal(2),
            ),
        ),
    )
    return repository, admission, evidence


def test_verified_close_releases_consumed_owner_without_changing_ledger(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    repository, admission, evidence = owned_close(dsn, schema)
    with _connect(dsn, schema) as c:
        before = c.execute("SELECT * FROM fills ORDER BY id").fetchall()
    result = repository.release_verified_close(**evidence)
    assert result.accepted, result.reason
    assert result.reservation_id == admission.reservation_id
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("released",)
        assert c.execute("SELECT * FROM fills ORDER BY id").fetchall() == before
    assert repository.reserve(**request(dsn, schema)).accepted


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE live_order_intents SET side='BUY' WHERE role='CLOSE'",
        "UPDATE live_order_intents SET role='ENTRY' WHERE role='CLOSE'",
        "UPDATE live_order_intents SET state='unknown' WHERE role='CLOSE'",
        "UPDATE live_order_intents SET filled_qty=.5 WHERE role='CLOSE'",
        "UPDATE live_order_intents SET margin_reservation_id=NULL WHERE role='ENTRY'",
        "UPDATE live_order_intents SET created_at=created_at - interval '1 day' WHERE role='CLOSE'",
        "DELETE FROM fills",
        "UPDATE fills SET provider_fill_id='status-derived:fake'",
        "UPDATE fills SET symbol='ETHUSDT'",
        "UPDATE fills SET quantity=.5",
        "UPDATE fills SET fee=.2",
        "UPDATE fills SET realized_pnl=99",
        "UPDATE fills SET filled_at=filled_at - interval '1 day'",
        "UPDATE bitget_margin_reservations SET state='unknown'",
    ],
)
def test_unbound_or_contradictory_ledger_cannot_release(postgres_schema, mutation):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    repository, admission, evidence = owned_close(dsn, schema)
    with _connect(dsn, schema) as c:
        c.execute(mutation)
        ledger = c.execute("SELECT * FROM fills ORDER BY id").fetchall()
    assert not repository.release_verified_close(**evidence).accepted
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone()[0] != "released"
        assert c.execute("SELECT * FROM fills ORDER BY id").fetchall() == ledger


@pytest.mark.parametrize(
    "change",
    [
        {"side": "BUY"},
        {"trade_side": "open"},
        {"provider_order_id": "wrong"},
        {"provider_fill_id": "wrong"},
        {"quantity": Decimal(".5")},
        {"price": Decimal("NaN")},
        {"symbol": "ETHUSDT"},
        {"fee": Decimal(".2")},
        {"realized_pnl": Decimal(3)},
    ],
)
def test_authenticated_fill_must_match_exact_durable_close(postgres_schema, change):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    repository, admission, evidence = owned_close(dsn, schema)
    evidence["fills"][0].update(change)
    assert not repository.release_verified_close(**evidence).accepted


def test_close_release_serializes_with_new_admission(postgres_schema):
    import time
    from concurrent.futures import ThreadPoolExecutor

    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    repository, admission, evidence = owned_close(dsn, schema)
    next_request = request(dsn, schema)
    assert repository.reserve(**next_request).reason == "symbol-already-owned"
    blocker = _connect(dsn, schema)
    blocker.execute(
        "SELECT id FROM bitget_margin_reservations WHERE id=%s FOR UPDATE",
        (admission.reservation_id,),
    )

    def wait_for_lock(kind):
        deadline = time.monotonic() + 5
        with _connect(dsn, schema) as c:
            while not c.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype=%s AND NOT granted", (kind,)
            ).fetchone()[0]:
                assert time.monotonic() < deadline
                time.sleep(0.01)

    try:
        with ThreadPoolExecutor(2) as workers:
            releasing = workers.submit(repository.release_verified_close, **evidence)
            wait_for_lock("transactionid")
            admitting = workers.submit(repo(dsn, schema).reserve, **next_request)
            wait_for_lock("advisory")
            assert not releasing.done() and not admitting.done()
            with _connect(dsn, schema) as c:
                before = c.execute("SELECT * FROM fills ORDER BY id").fetchall()
                assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == (
                    "consumed",
                )
            blocker.commit()
            assert releasing.result(timeout=5).accepted
            assert admitting.result(timeout=5).accepted
        with _connect(dsn, schema) as c:
            assert c.execute(
                "SELECT state,count(*) FROM bitget_margin_reservations GROUP BY state "
                "ORDER BY state"
            ).fetchall() == [("released", 1), ("reserved", 1)]
            assert c.execute("SELECT * FROM fills ORDER BY id").fetchall() == before
    finally:
        blocker.rollback()
        blocker.close()


@pytest.mark.parametrize("blocked_lock", ["advisory", "row"])
def test_close_wait_rechecks_freshness_and_retains_ownership(postgres_schema, blocked_lock):
    import time
    from concurrent.futures import ThreadPoolExecutor

    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    repository, admission, evidence = owned_close(dsn, schema)
    evidence["max_snapshot_age"] = timedelta(seconds=1)
    blocker = _connect(dsn, schema)
    if blocked_lock == "advisory":
        blocker.execute("SELECT pg_advisory_xact_lock(hashtext('bitget'))")
    else:
        blocker.execute(
            "SELECT id FROM bitget_margin_reservations WHERE id=%s FOR UPDATE",
            (admission.reservation_id,),
        )
    try:
        with ThreadPoolExecutor(1) as workers:
            evidence["observed_at"] = datetime.now(UTC)
            future = workers.submit(repository.release_verified_close, **evidence)
            deadline = time.monotonic() + 5
            with _connect(dsn, schema) as c:
                while not c.execute(
                    "SELECT count(*) FROM pg_locks WHERE locktype=%s AND NOT granted",
                    ("advisory" if blocked_lock == "advisory" else "transactionid",),
                ).fetchone()[0]:
                    assert not future.done(), future.result()
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
            time.sleep(1.1)
            blocker.commit()
            assert future.result(timeout=5).reason == "stale-or-future-close-evidence"
        assert repository.reserve(**request(dsn, schema)).reason == "symbol-already-owned"
    finally:
        blocker.rollback()
        blocker.close()


def test_repeated_verified_close_preserves_receipt_and_economics(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    repository, admission, evidence = owned_close(dsn, schema)
    assert repository.release_verified_close(**evidence).accepted
    with _connect(dsn, schema) as c:
        receipt = c.execute("SELECT * FROM bitget_margin_reservations").fetchall()
        ledger = c.execute("SELECT * FROM fills").fetchall()
    assert repository.release_verified_close(**evidence).accepted
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT * FROM bitget_margin_reservations").fetchall() == receipt
        assert c.execute("SELECT * FROM fills").fetchall() == ledger


@pytest.mark.parametrize(
    "change",
    [
        {"environment": "LIVE"},
        {"symbol": "ETHUSDT"},
        {"dispatch_id": uuid4()},
        {"entry_client_order_id": str(uuid4())},
        {"close_client_order_id": str(uuid4())},
    ],
)
def test_wrong_canonical_owner_never_releases(postgres_schema, change):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    repository, admission, evidence = owned_close(dsn, schema)
    evidence.update(change)
    assert not repository.release_verified_close(**evidence).accepted


def test_ambiguous_symbol_owner_cannot_release(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    repository, admission, evidence = owned_close(dsn, schema)
    second = repository.reserve(**request(dsn, schema, symbol="ETHUSDT"))
    repository.resolve(second.reservation_id, "FILLED")
    with _connect(dsn, schema) as c:
        # Legacy/corrupt ownership can predate migration20; production keeps index.
        c.execute("DROP INDEX bitget_margin_reservations_symbol_owner")
        c.execute(
            "UPDATE bitget_margin_reservations SET symbol='BTCUSDT' WHERE id=%s",
            (second.reservation_id,),
        )
    assert repository.release_verified_close(**evidence).reason == "ambiguous-close-ownership"
    with _connect(dsn, schema) as c:
        assert c.execute(
            "SELECT count(*) FROM bitget_margin_reservations WHERE state='consumed'"
        ).fetchone() == (2,)


def test_failed_release_transaction_retains_ownership_and_fill_ledger(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    repository, admission, evidence = owned_close(dsn, schema)
    with _connect(dsn, schema) as c:
        before = c.execute("SELECT * FROM fills ORDER BY id").fetchall()
        c.execute("""CREATE FUNCTION fail_close_release() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected-release-transaction-failure'; END; $$""")
        c.execute(
            "CREATE TRIGGER injected_release_failure BEFORE UPDATE ON "
            "bitget_margin_reservations\n"
            "            FOR EACH ROW WHEN (NEW.state = 'released') EXECUTE "
            "FUNCTION fail_close_release()"
        )
    with pytest.raises(Exception, match="injected-release-transaction-failure"):
        repository.release_verified_close(**evidence)
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("consumed",)
        assert c.execute("SELECT * FROM fills ORDER BY id").fetchall() == before
        c.execute("DROP TRIGGER injected_release_failure ON bitget_margin_reservations")
    assert repository.reserve(**request(dsn, schema)).reason == "symbol-already-owned"
    assert repository.release_verified_close(**evidence).accepted


@pytest.mark.parametrize("outcome", ["present", "missing", "invalid-record", "query-error"])
def test_intent_read_releases_transaction_on_every_outcome(postgres_schema, outcome):
    import psycopg

    from fatty_trader.storage.live_intents import PostgresLiveIntentStore

    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    oid = str(uuid4())
    if outcome in {"present", "invalid-record"}:
        with _connect(dsn, schema) as connection:
            connection.execute(
                """INSERT INTO live_order_intents
                (id,exchange,client_order_id,symbol,side,role,state,requested_qty,leverage)
                VALUES (%s,'bitget',%s,'BTCUSDT','BUY','ENTRY','staged',1,%s)""",
                (uuid4(), oid, 20 if outcome == "invalid-record" else None),
            )
    opened = []

    def connect():
        connection = _connect(dsn, schema)
        opened.append(connection)
        if outcome == "query-error":
            connection.execute("SET search_path TO nonexistent_read_schema")
        return connection

    try:
        store = PostgresLiveIntentStore(connect)
        if outcome == "invalid-record":
            with pytest.raises(ValueError, match="admission evidence"):
                store.get(oid)
        elif outcome == "query-error":
            with pytest.raises(psycopg.errors.UndefinedTable):
                store.get(oid)
        else:
            record = store.get(oid)
            if outcome == "present":
                assert record.client_oid == oid
            else:
                assert record is None
        released = all(connection.closed for connection in opened)
    finally:
        # A failing regression must not itself strand DROP SCHEMA behind locks.
        for connection in opened:
            connection.close()
    assert released


@pytest.mark.parametrize("contradictory", [False, True])
def test_owned_residual_releases_owned_read_transaction(postgres_schema, contradictory):
    from fatty_trader.storage.live_intents import (
        OwnedQuantityEvidenceError,
        PostgresLiveIntentStore,
    )

    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    _, _, evidence = owned_close(dsn, schema)
    if contradictory:
        with _connect(dsn, schema) as connection:
            connection.execute("UPDATE live_order_intents SET side='BUY' WHERE role='CLOSE'")
    opened = []

    def connect():
        connection = _connect(dsn, schema)
        opened.append(connection)
        return connection

    try:
        store = PostgresLiveIntentStore(connect)
        if contradictory:
            with pytest.raises(OwnedQuantityEvidenceError):
                store.remaining_owned_quantity(evidence["entry_client_order_id"])
        else:
            assert store.remaining_owned_quantity(evidence["entry_client_order_id"]) == 0
        released = all(connection.closed for connection in opened)
    finally:
        for connection in opened:
            connection.close()
    assert released


def test_owned_residual_does_not_commit_or_close_borrowed_transaction(postgres_schema):
    from fatty_trader.storage.live_intents import PostgresLiveIntentStore

    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    _, admission, evidence = owned_close(dsn, schema)
    with _connect(dsn, schema) as connection:
        connection.execute(
            "UPDATE bitget_margin_reservations SET resolution_reason='uncommitted-fixture' "
            "WHERE id=%s",
            (admission.reservation_id,),
        )
        store = PostgresLiveIntentStore(lambda: _connect(dsn, schema))
        assert (
            store.remaining_owned_quantity(
                evidence["entry_client_order_id"], cursor=connection.cursor()
            )
            == 0
        )
        assert not connection.closed
        connection.rollback()
    with _connect(dsn, schema) as connection:
        assert (
            connection.execute(
                "SELECT resolution_reason FROM bitget_margin_reservations WHERE id=%s",
                (admission.reservation_id,),
            ).fetchone()[0]
            != "uncommitted-fixture"
        )


def test_other_contradictory_bound_exit_vetoes_release_and_new_close(postgres_schema):
    from types import SimpleNamespace

    from fatty_trader.storage.live_intents import PostgresLiveIntentStore

    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    repository, admission, evidence = owned_close(dsn, schema)
    bad_oid, new_oid = str(uuid4()), str(uuid4())
    with _connect(dsn, schema) as connection:
        connection.execute(
            """INSERT INTO live_order_intents
            (id,exchange,client_order_id,symbol,side,role,state,requested_qty,filled_qty,
             provider_order_id,provider_fill_ids)
            VALUES (%s,'bitget',%s,'BTCUSDT','BUY','CLOSE','filled',1,1,'bad-close',
                    '["unproved-close-fill"]'::jsonb),
                   (%s,'bitget',%s,'BTCUSDT','SELL','CLOSE','requested',1,0,NULL,'[]'::jsonb)""",
            (uuid4(), bad_oid, uuid4(), new_oid),
        )
        connection.execute(
            "INSERT INTO bitget_verified_close_bindings(close_client_order_id,reservation_id) "
            "VALUES (%s,%s)",
            (bad_oid, admission.reservation_id),
        )
        before = connection.execute("SELECT * FROM fills ORDER BY id").fetchall()
    assert repository.release_verified_close(**evidence).reason == "unverified-close-fills"
    store = PostgresLiveIntentStore(lambda: _connect(dsn, schema))
    assert not store.verified_close_lifecycle.bind_requested_close(
        SimpleNamespace(environment="DEMO"), new_oid
    )
    with _connect(dsn, schema) as connection:
        assert connection.execute(
            "SELECT state FROM bitget_margin_reservations WHERE id=%s",
            (admission.reservation_id,),
        ).fetchone() == ("consumed",)
        assert connection.execute(
            "SELECT count(*) FROM bitget_verified_close_bindings WHERE close_client_order_id=%s",
            (new_oid,),
        ).fetchone() == (0,)
        assert connection.execute("SELECT * FROM fills ORDER BY id").fetchall() == before


@pytest.mark.asyncio
async def test_venue_plan_close_without_a_client_oid_releases_the_reservation(postgres_schema):
    """A venue-executed TP/SL plan closes through the venue's own order.

    Its fill carries the executed order id instead of our client order id, and a
    one-way-mode venue reports the trade side as ``sell_single`` rather than
    ``close``.  The release path must recognise both, or the symbol stays owned
    after every trade and every later signal for it is refused.
    """
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    repository, admission, evidence = owned_close(dsn, schema)
    close_oid = evidence["close_client_order_id"]

    from fatty_trader.storage.live_intents import PostgresLiveIntentStore

    store = PostgresLiveIntentStore(lambda: _connect(dsn, schema))

    class PlanCloseVenue:
        environment = evidence["environment"]

        async def get_fills(self, symbol=None):
            return {
                "fillList": [
                    {
                        "tradeId": "real-close-fill",
                        "orderId": "close-order",
                        "symbol": "BTCUSDT",
                        "side": "sell",
                        "tradeSide": "sell_single",
                        "baseVolume": "1",
                        "price": "100",
                        "feeDetail": [{"feeCoin": "USDT", "totalFee": "-0.1"}],
                        "profit": "2",
                    }
                ],
                "endId": "real-close-fill",
            }

        async def get_all_positions(self):
            return [{"symbol": "BTCUSDT", "holdSide": "long", "total": "0"}]

    await store.verified_close_lifecycle.reconcile(PlanCloseVenue())

    with _connect(dsn, schema) as c:
        assert c.execute(
            "SELECT state,resolution_reason FROM bitget_margin_reservations WHERE id=%s",
            (admission.reservation_id,),
        ).fetchone() == ("released", f"verified-close:{close_oid}")
        assert c.execute(
            "SELECT state,filled_qty,provider_fill_ids FROM live_order_intents "
            "WHERE client_order_id=%s",
            (close_oid,),
        ).fetchone() == ("filled", Decimal(1), ["real-close-fill"])
