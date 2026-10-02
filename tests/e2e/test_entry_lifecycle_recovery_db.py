"""Disposable PostgreSQL only; provider is fake, no venue I/O."""
# ruff: noqa: F401, F811

from decimal import Decimal
from uuid import uuid4

import pytest
from test_postgres_migration_and_margin_admission import _connect, _migrate, postgres_schema

from fatty_trader.domain.enums import DispatchState
from fatty_trader.exchanges.bitget.async_execution import AsyncExecutionResult
from fatty_trader.exchanges.bitget.live import LiveIntentRecord, LiveOrderStatus
from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
from fatty_trader.execution.bitget_dispatch_repository import (
    BitgetDispatch,
    PostgresBitgetDispatchRepository,
)
from fatty_trader.storage.live_intents import PostgresLiveIntentStore


def test_late_provider_fills_replace_provisional_ledger(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    store = PostgresLiveIntentStore(lambda: _connect(dsn, schema))
    intent = LiveIntentRecord(
        "bitget",
        "late-fill",
        "BTCUSDT",
        "BUY",
        requested_qty=Decimal("2"),
        filled_qty=Decimal("2"),
        avg_price=Decimal("100"),
        fee=Decimal("0.2"),
        state="filled",
    )
    store.save(intent)
    store.update(intent)
    actual = ({"fillId": "real-1", "size": "2", "price": "100", "fee": "0.2"},)
    store.record_fills(intent, actual)
    store.record_fills(intent, actual)
    with _connect(dsn, schema) as conn:
        row = conn.execute("SELECT count(*),sum(quantity),sum(fee) FROM fills").fetchone()
    assert row == (1, Decimal("2"), Decimal("0.2"))


def test_canary_counts_one_dispatch_not_intent_plus_reservation(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    ids = [uuid4(), uuid4()]
    with _connect(dsn, schema) as conn:
        for id in ids:
            conn.execute(
                """
                INSERT INTO dispatches(id,source_type,source_id,revision,exchange,state)
                VALUES (%s,'e2e',%s,%s,'bitget','SUBMITTING')
                """,
                (id, uuid4(), "a" * 64),
            )
    repo = PostgresBitgetDispatchRepository(lambda: _connect(dsn, schema))
    assert repo.reserve_canary_entry(ids[0], "bitget", 2)
    # The reservation dispatch ID is durably available through margin admission.
    with _connect(dsn, schema) as conn:
        snapshot = uuid4()
        conn.execute(
            (
                "\n"
                "            INSERT INTO\n"
                "            balance_snapshots(id,exchange,total_balance,available_bala"
                "nce,equity,margin_coin,captured_at)\n"
                "            VALUES (%s,'bitget',100,100,100,'USDT',now())\n"
                "            "
            ),
            (snapshot,),
        )
        reservation = uuid4()
        conn.execute(
            (
                "\n"
                "            INSERT INTO\n"
                "            bitget_margin_reservations(id,exchange,dispatch_id,client_"
                "order_id,balance_snapshot_id,planned_margin_usdt,state,expires_at,symb"
                "ol,environment)\n"
                "            VALUES (%s,'bitget',%s,'canary-first',%s,1,'reserved',\n"
                "                    now()+interval '1 hour','BTCUSDT','LIVE')\n"
                "            "
            ),
            (reservation, ids[0], snapshot),
        )
    store = PostgresLiveIntentStore(lambda: _connect(dsn, schema))
    store.save(
        LiveIntentRecord(
            "bitget",
            "canary-first",
            "BTCUSDT",
            "BUY",
            state="acknowledged",
            requested_qty=Decimal("1"),
            margin_reservation_id=reservation,
            balance_snapshot_id=snapshot,
            planned_leverage=1,
            planned_margin_usdt=Decimal("1"),
            planned_notional_usdt=Decimal("100"),
            margin_mode="ISOLATED",
        )
    )
    assert repo.reserve_canary_entry(ids[1], "bitget", 2)


def _owned_entry(dsn, schema, crash_state, filled_qty=Decimal("2")):
    dispatch_id, signal_id, message_id, snapshot_id, reservation_id = [uuid4() for _ in range(5)]
    dispatch = BitgetDispatch(
        dispatch_id,
        crash_state,
        "worker",
        1,
        "BTCUSDT",
        "LONG",
        Decimal("100"),
        Decimal("90"),
        (Decimal("110"),),
        7,
        9,
    )
    oid = BitgetDispatchExecution.client_oid(dispatch)
    with _connect(dsn, schema) as conn:
        conn.execute(
            """
            INSERT INTO
            telegram_messages(id,channel_id,message_id,revision_hash,raw_text,intake_state)
            VALUES (%s,7,9,%s,'entry','ANALYZED')
            """,
            (message_id, "a" * 64),
        )
        conn.execute(
            (
                "\n"
                "            INSERT INTO\n"
                "            canonical_signals(id,message_id,revision,pair_token,direct"
                "ion,entry_price,stop_loss,take_profits)\n"
                "            VALUES (%s,%s,%s,'BTCUSDT','LONG',100,90,'[110]'::jsonb)\n"
                "            "
            ),
            (signal_id, message_id, "a" * 64),
        )
        conn.execute(
            """
            INSERT INTO dispatches(id,source_type,source_id,revision,exchange,state)
            VALUES (%s,'telegram',%s,%s,'bitget',%s)
            """,
            (dispatch_id, signal_id, "a" * 64, crash_state),
        )
        conn.execute(
            """
            INSERT INTO
            balance_snapshots(id,exchange,total_balance,available_balance,equity,margin_coin)
            VALUES (%s,'bitget',100,100,100,'USDT')
            """,
            (snapshot_id,),
        )
        conn.execute(
            (
                "\n"
                "            INSERT INTO\n"
                "            bitget_margin_reservations(id,exchange,dispatch_id,client_"
                "order_id,balance_snapshot_id,planned_margin_usdt,state,expires_at,symb"
                "ol,environment)\n"
                "            VALUES (%s,'bitget',%s,%s,%s,1,'consumed',now()+interval "
                "'1 hour','BTCUSDT','LIVE')\n"
                "            "
            ),
            (reservation_id, dispatch_id, oid, snapshot_id),
        )
    store = PostgresLiveIntentStore(lambda: _connect(dsn, schema))
    intent = LiveIntentRecord(
        "bitget",
        oid,
        "BTCUSDT",
        "BUY",
        requested_qty=Decimal("2"),
        state="filled" if filled_qty == Decimal("2") else "partially_filled",
        filled_qty=filled_qty,
        avg_price=Decimal("100"),
        margin_reservation_id=reservation_id,
        balance_snapshot_id=snapshot_id,
        planned_margin_usdt=Decimal("1"),
        planned_leverage=1,
        margin_mode="ISOLATED",
    )
    store.save(intent)
    return dispatch, intent, store


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_state", ["SUBMITTING", "ACKNOWLEDGED", "FILLED"])
@pytest.mark.parametrize(
    "status,target",
    [(LiveOrderStatus.FILLED, "FILLED"), (LiveOrderStatus.PARTIAL, "PARTIALLY_FILLED")],
)
async def test_real_db_recovery_preserves_fill_and_escalates_without_any_post(
    postgres_schema, crash_state, status, target
):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    quantity = Decimal("2") if status is LiveOrderStatus.FILLED else Decimal("1")
    dispatch, intent, store = _owned_entry(dsn, schema, crash_state, quantity)

    class GetOnlyExecution:
        async def reconcile_intent(self, entry):
            return AsyncExecutionResult(
                entry.client_oid,
                status,
                quantity,
                Decimal("100"),
                Decimal("0.2"),
                "provider-1",
                ("fill-1",),
                ({"fillId": "fill-1", "size": str(quantity), "price": "100", "fee": "0.2"},),
            )

        async def submit_entry(self, *args):
            raise AssertionError("entry replay POST forbidden")

        async def protect_filled_position(self, *args):
            raise AssertionError("ambiguous protection POST forbidden")

    adapter = BitgetDispatchExecution(
        GetOnlyExecution(),
        store,
        dispatch_repository=PostgresBitgetDispatchRepository(lambda: _connect(dsn, schema)),
    )
    assert await adapter.recover_entry_lifecycles() == 1
    assert adapter.recovery_ready is False
    with _connect(dsn, schema) as conn:
        row = conn.execute(
            "SELECT state,terminal_reason FROM dispatches WHERE id=%s", (dispatch.id,)
        ).fetchone()
        fills = conn.execute("SELECT count(*),sum(quantity) FROM fills").fetchone()
        outbox = conn.execute("SELECT payload FROM notifications_outbox").fetchone()[0]
    assert row == (target, "recovery-missing-protection:recovery-reader-unwired")
    assert row[0] in set(DispatchState)
    assert fills == (1, quantity)
    assert outbox["reason"] == row[1]


def test_changed_protection_verdict_gets_distinct_durable_outbox_event(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    dispatch, _, _ = _owned_entry(dsn, schema, "FILLED")
    repo = PostgresBitgetDispatchRepository(lambda: _connect(dsn, schema))
    for reason in ["recovery-protection-verified", "recovery-missing-protection:unconfirmed"]:
        repo.transition(dispatch.id, expected_state="FILLED", target_state="FILLED", reason=reason)
    with _connect(dsn, schema) as conn:
        reasons = [
            row[0]["reason"]
            for row in conn.execute("SELECT payload FROM notifications_outbox").fetchall()
        ]
    assert set(reasons) == {
        "recovery-protection-verified",
        "recovery-missing-protection:unconfirmed",
    }
