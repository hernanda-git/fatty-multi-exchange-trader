"""Disposable PostgreSQL admission regressions; no venue/network access."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from test_postgres_migration_and_margin_admission import (
    _connect,
    _migrate,
)
from test_postgres_migration_and_margin_admission import (
    postgres_schema as _postgres_schema,
)

from fatty_trader.storage.balance_reservations import PostgresBitgetMarginReservationRepository

postgres_schema = _postgres_schema


def request(dsn, schema, symbol="BTCUSDT", **overrides) -> dict[str, Any]:
    dispatch = uuid4()
    with _connect(dsn, schema) as connection:
        connection.execute(
            """INSERT INTO dispatches (id,source_type,source_id,revision,exchange,state)
            VALUES (%s,'admission',%s,%s,'bitget','QUEUED')""",
            (dispatch, uuid4(), "a" * 64),
        )
        from source_freshness_fixtures import eligible_dispatch_source

        eligible_dispatch_source(connection, dispatch)
    args = dict(
        exchange="bitget",
        dispatch_id=dispatch,
        client_order_id=str(dispatch),
        total_balance=Decimal(100),
        available_balance=Decimal(100),
        equity=Decimal(100),
        margin_coin="USDT",
        observed_at=datetime.now(UTC),
        planned_margin_usdt=Decimal(1),
        headroom=Decimal("0.5"),
        ttl=timedelta(minutes=5),
        max_margin_per_trade_usdt=Decimal(1),
        symbol=symbol,
        environment="DEMO",
        max_positions=3,
        provider_active_symbols=(),
        max_snapshot_age=timedelta(seconds=30),
        max_future_skew=timedelta(seconds=1),
    )
    args.update(overrides)
    return args


def repo(dsn, schema):
    return PostgresBitgetMarginReservationRepository(lambda: _connect(dsn, schema))


@pytest.mark.parametrize(
    "symbols,active,cap,reason",
    [
        (("BTCUSDT", "BTCUSDT"), (), 3, "symbol-already-owned"),
        (("BTCUSDT", "ETHUSDT"), ("SOLUSDT",), 2, "position-cap-exceeded"),
    ],
)
def test_atomic_different_dispatch_admission(postgres_schema, symbols, active, cap, reason):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    args = [
        request(dsn, schema, s, provider_active_symbols=active, max_positions=cap) for s in symbols
    ]
    barrier = Barrier(2)
    with ThreadPoolExecutor(2) as workers:
        futures = [
            workers.submit(lambda a=a: (barrier.wait(), repo(dsn, schema).reserve(**a))[1])
            for a in args
        ]
        results = [f.result(timeout=10) for f in futures]
    assert sum(r.accepted for r in results) == 1
    assert [r.reason for r in results if not r.accepted] == [reason]
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT count(*) FROM bitget_margin_reservations").fetchone() == (1,)


def test_lock_wait_expires_snapshot(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    args = request(dsn, schema, max_snapshot_age=timedelta(milliseconds=150))
    blocker = _connect(dsn, schema)
    blocker.execute("SELECT pg_advisory_xact_lock(hashtext('bitget'))")
    with ThreadPoolExecutor(1) as workers:
        future = workers.submit(repo(dsn, schema).reserve, **args)
        # Verify a real waiter, not a timing-only concurrency claim.
        deadline = time.monotonic() + 5
        with _connect(dsn, schema) as c:
            while not c.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND NOT granted"
            ).fetchone()[0]:
                assert time.monotonic() < deadline
                time.sleep(0.01)
        time.sleep(0.2)
        blocker.commit()
        result = future.result(timeout=5)
    blocker.close()
    assert result.reason == "stale-balance-snapshot"
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT count(*) FROM bitget_margin_reservations").fetchone() == (0,)


def test_future_snapshot_rejected(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    result = repo(dsn, schema).reserve(
        **request(dsn, schema, observed_at=datetime.now(UTC) + timedelta(minutes=1))
    )
    assert result.reason == "future-balance-snapshot"


def test_partial_keeps_remaining_margin_and_slot(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    first = repo(dsn, schema).reserve(**request(dsn, schema))
    assert first.accepted and first.reservation_id is not None
    repo(dsn, schema).resolve(first.reservation_id, "PARTIAL")
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("unknown",)
    second = repo(dsn, schema).reserve(
        **request(dsn, schema, "ETHUSDT", available_balance=Decimal(2), headroom=Decimal(".75"))
    )
    assert second.reason == "insufficient-reserved-headroom"


def test_consumed_stale_balance_does_not_free_margin(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    args = request(dsn, schema, "ETHUSDT", available_balance=Decimal(2), headroom=Decimal(".75"))
    first = repo(dsn, schema).reserve(**request(dsn, schema))
    assert first.accepted and first.reservation_id is not None
    repo(dsn, schema).resolve(first.reservation_id, "FILLED")
    second = repo(dsn, schema).reserve(**args)
    assert second.reason == "insufficient-reserved-headroom"


def test_provider_plus_inflight_slots_not_double_counted(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    first = repo(dsn, schema).reserve(**request(dsn, schema))
    assert first.accepted and first.reservation_id is not None
    repo(dsn, schema).resolve(first.reservation_id, "FILLED")
    second = repo(dsn, schema).reserve(
        **request(dsn, schema, "ETHUSDT", provider_active_symbols=("BTCUSDT",), max_positions=2)
    )
    assert second.accepted
    third = repo(dsn, schema).reserve(
        **request(dsn, schema, "SOLUSDT", provider_active_symbols=("BTCUSDT",), max_positions=2)
    )
    assert third.reason == "position-cap-exceeded"


def test_partial_then_cancel_retains_exposure(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    first = repo(dsn, schema).reserve(**request(dsn, schema))
    assert first.accepted and first.reservation_id is not None
    repo(dsn, schema).resolve(first.reservation_id, "PARTIAL")
    repo(dsn, schema).resolve(first.reservation_id, "CANCELLED")
    second = repo(dsn, schema).reserve(**request(dsn, schema))
    assert second.reason == "symbol-already-owned"


@pytest.mark.parametrize("late_outcome", ["PARTIAL", "FILLED"])
def test_late_fill_after_cancel_restores_commitment(postgres_schema, late_outcome):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    first = repo(dsn, schema).reserve(**request(dsn, schema))
    assert first.accepted and first.reservation_id is not None
    repo(dsn, schema).resolve(first.reservation_id, "CANCELLED")
    repo(dsn, schema).resolve(first.reservation_id, late_outcome)
    result = repo(dsn, schema).reserve(**request(dsn, schema))
    assert result.reason == "symbol-already-owned"
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT has_exposure FROM bitget_margin_reservations").fetchone() == (
            True,
        )


def insert_intent(c, amount, **extra):
    fields = dict(
        id=uuid4(),
        exchange="bitget",
        client_order_id=str(uuid4()),
        symbol="BTCUSDT",
        side="BUY",
        role="ENTRY",
        state="requested",
        requested_qty=1,
        planned_margin_usdt=amount,
        planned_notional_usdt=amount,
    )
    fields.update(extra)
    c.execute(
        f"INSERT INTO live_order_intents ({','.join(fields)}) "
        f"VALUES ({','.join(['%s'] * len(fields))})",
        tuple(fields.values()),
    )


def upgrade_to_18(dsn, schema, monkeypatch):
    from fatty_trader.storage import migrations
    from fatty_trader.storage.schema import apply_initial_schema

    old = [(v, sql) for v, sql in migrations.MIGRATIONS if v <= 18]
    # Actual migration-14 path: earlier live schema has no planned/admission columns.
    sql = old[0][1]
    sql = sql.replace(
        "    planned_margin_usdt NUMERIC CHECK "
        "(planned_margin_usdt IS NULL OR planned_margin_usdt > 0),\n",
        "",
    )
    sql = sql.replace(
        "    planned_notional_usdt NUMERIC CHECK (\n"
        "        planned_notional_usdt IS NULL OR planned_notional_usdt > 0\n    ),\n",
        "",
    )
    sql = sql.replace("    balance_snapshot_id UUID,\n    margin_reservation_id UUID,\n", "")
    old[0] = (1, sql)
    with monkeypatch.context() as m:
        m.setattr(migrations, "MIGRATIONS", old)
        with _connect(dsn, schema) as c:
            apply_initial_schema(c.cursor())
            migrations.apply_migrations(c.cursor())


@pytest.mark.parametrize("upgrade", [False, True])
@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity", "-Infinity"])
def test_fresh_upgrade_reject_invalid_planned_amounts(
    postgres_schema, monkeypatch, upgrade, amount
):
    from fatty_trader.storage.migrations import apply_migrations

    dsn, schema = postgres_schema
    if upgrade:
        upgrade_to_18(dsn, schema, monkeypatch)
        with _connect(dsn, schema) as c:
            apply_migrations(c.cursor())
    else:
        _migrate(dsn, schema)
    import psycopg

    with _connect(dsn, schema) as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_intent(c, Decimal(amount))
        c.rollback()


def test_upgrade_invalid_existing_rows_fail_without_rewrite(postgres_schema, monkeypatch):
    from fatty_trader.storage.migrations import apply_migrations

    dsn, schema = postgres_schema
    upgrade_to_18(dsn, schema, monkeypatch)
    with _connect(dsn, schema) as c:
        insert_intent(c, Decimal(-1))
    with _connect(dsn, schema) as c:
        with pytest.raises(ValueError, match="preflight.*invalid planned-amount"):
            apply_migrations(c.cursor())
        c.rollback()
        assert c.execute("SELECT planned_margin_usdt FROM live_order_intents").fetchone() == (
            Decimal(-1),
        )
        assert c.execute("SELECT max(version) FROM schema_migrations").fetchone() == (18,)


@pytest.mark.parametrize("mismatch", ["missing", "client", "snapshot", "symbol", "margin"])
def test_intent_reservation_binding(postgres_schema, mismatch):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    args = request(dsn, schema)
    first = repo(dsn, schema).reserve(**args)
    assert first.accepted and first.reservation_id is not None and first.snapshot_id is not None
    fields = dict(
        client_order_id=args["client_order_id"],
        margin_reservation_id=first.reservation_id,
        balance_snapshot_id=first.snapshot_id,
    )
    amount = Decimal(1)
    if mismatch == "missing":
        fields["margin_reservation_id"] = uuid4()
    if mismatch == "client":
        fields["client_order_id"] = str(uuid4())
    if mismatch == "snapshot":
        fields["balance_snapshot_id"] = uuid4()
    if mismatch == "symbol":
        fields["symbol"] = "ETHUSDT"
    if mismatch == "margin":
        amount = Decimal(2)
    import psycopg

    with _connect(dsn, schema) as c:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            insert_intent(c, amount, **fields)
        c.rollback()
