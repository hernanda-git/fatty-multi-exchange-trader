"""Credential-free actual service graph, migrated PostgreSQL, strict GET fake."""

# ruff: noqa: F401, F811
from decimal import Decimal

import pytest
from test_entry_lifecycle_recovery_db import _owned_entry
from test_postgres_migration_and_margin_admission import _connect, _migrate, postgres_schema

from fatty_trader import service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "protected",
        "flat",
        "wrong-level",
        "manual",
        "pending",
        "empty",
        "callback-disabled",
        "dispatcher-unprotected",
        "replacement-position",
        "old-plan",
        "missing-fill-epoch",
    ],
)
async def test_real_service_inventory_verdict(postgres_schema, monkeypatch, case):
    import psycopg

    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    dispatch, intent, store = _owned_entry(dsn, schema, "FILLED")
    with _connect(dsn, schema) as conn:
        conn.execute("UPDATE bitget_margin_reservations SET environment='DEMO'")
        if case == "empty":
            conn.execute("DELETE FROM live_order_intents")
            conn.execute("DELETE FROM bitget_margin_reservations")
    real_connect = psycopg.connect
    monkeypatch.setattr(
        psycopg,
        "connect",
        lambda *a, **kw: real_connect(dsn, **dict(kw, options=f"-c search_path={schema}")),
    )
    calls = []
    position = dict(
        symbol="BTCUSDT",
        total="2",
        holdSide="long",
        marginMode="isolated",
        posMode="one_way_mode",
        stopLossId="sl-id",
        takeProfitId="tp-id",
        leverage="1",
        marginSize="1",
        cTime="1700000000000",
    )
    plans = [
        dict(
            symbol="BTCUSDT",
            holdSide="long",
            planType=kind,
            orderId=pid,
            clientOid=intent.client_oid + "-" + leg,
            triggerPrice=level,
            triggerType="mark_price",
            executePrice="0",
            size="0",
            planStatus="live",
            cTime="1700000000100",
        )
        for kind, pid, leg, level in [
            ("pos_loss", "sl-id", "sl", "90"),
            ("pos_profit", "tp-id", "tp", "110"),
        ]
    ]
    if case == "wrong-level":
        plans[0]["triggerPrice"] = "89"
    if case == "replacement-position":
        position["cTime"] = "1700000000200"
    if case == "old-plan":
        plans[0]["cTime"] = "1699999999999"

    class Provider:
        async def get_order_detail(self, symbol, *, client_oid):
            calls.append("get_order_detail")
            assert client_oid == intent.client_oid
            return dict(orderId="entry-id", status="filled", baseVolume="2", priceAvg="100")

        async def get_fills(self, symbol):
            calls.append("get_fills")
            return [
                dict(
                    orderId="entry-id",
                    clientOid=intent.client_oid,
                    tradeId="fill-id",
                    size="2",
                    price="100",
                    fee="0.2",
                    symbol="BTCUSDT",
                    side="buy",
                    tradeSide="buy_single",
                    posMode="one_way_mode",
                    profit="0",
                    cTime=None if case == "missing-fill-epoch" else "1700000000000",
                )
            ]

        async def get_single_position(self, symbol):
            calls.append("get_single_position")
            return [] if case == "flat" else [position]

        async def get_pending_plan_orders(self, symbol):
            calls.append("get_pending_plan_orders")
            return plans

        async def get_all_positions(self):
            calls.append("get_all_positions")
            if case in {"flat", "empty"}:
                return []
            return [position] + ([dict(position, symbol="ETHUSDT")] if case == "manual" else [])

        async def get_pending_orders(self):
            calls.append("get_pending_orders")
            return [dict(symbol="ETHUSDT", clientOid="manual")] if case == "pending" else []

        def __getattr__(self, name):
            raise AssertionError("provider write/unexpected method: " + name)

    runtime = service.build_bitget_execution_runtime(
        dict(
            TRADER_MODE="DEMO",
            BITGET_MODE="DEMO",
            BITGET_EXECUTION_ENABLED="1",
            BITGET_MAX_MARGIN_PER_TRADE_USDT="1",
            BITGET_CANARY_MAX_ORDERS="20",
            BITGET_APPROVAL_REFERENCE="test-only",
            BITGET_MAX_CLOCK_SKEW_MS="5000",
            BITGET_API_KEY="fake",
            BITGET_API_SECRET="fake",
            BITGET_API_PASSPHRASE="fake",
        ),
        client_factory=lambda *a, **kw: Provider(),
        intent_store_factory=lambda: store,
    )
    assert runtime is not None
    if case == "callback-disabled":
        runtime.execution._recovery_protection = None
    count = await runtime.execution.recover_entry_lifecycles()
    assert count == (0 if case == "empty" else 1)
    assert runtime.execution.recovery_ready == (
        case in {"protected", "empty", "dispatcher-unprotected"}
    )
    assert all(call.startswith("get_") for call in calls)
    with _connect(dsn, schema) as conn:
        if case != "empty":
            state, reason = conn.execute(
                "SELECT state,terminal_reason FROM dispatches WHERE id=%s", (dispatch.id,)
            ).fetchone()
            assert state == "FILLED"
            assert conn.execute("SELECT sum(quantity) FROM fills").fetchone()[0] == Decimal("2")
            if case in {"protected", "manual", "pending", "dispatcher-unprotected"}:
                assert reason == "recovery-protection-verified"
            elif case == "callback-disabled":
                assert reason == "recovery-missing-protection:recovery-reader-unwired"
            else:
                assert reason.startswith("recovery-missing-protection:")
            assert conn.execute("SELECT count(*) FROM notifications_outbox").fetchone()[0] > 0
    if case == "dispatcher-unprotected":
        with _connect(dsn, schema) as conn:
            conn.execute(
                "UPDATE dispatches SET terminal_reason='missing-protection-escalated' WHERE id=%s",
                (dispatch.id,),
            )
        # Optional callback/latch cannot authorize over a durable dispatcher veto.
        runtime.execution._recovery_protection = None
        runtime.execution.recovery_ready = True
    if case not in {"protected", "empty"}:
        from dataclasses import replace
        from datetime import UTC, datetime
        from uuid import uuid4

        from fatty_trader.execution.bitget_admission import BitgetEntrySubmission

        next_dispatch = replace(dispatch, id=uuid4(), state="SUBMITTING", source_message_id=10)
        submission = BitgetEntrySubmission(
            quantity=Decimal("2"),
            effective_leverage=1,
            planned_margin_usdt=Decimal("1"),
            planned_notional_usdt=Decimal("200"),
            margin_mode="ISOLATED",
            balance_snapshot_id=uuid4(),
            margin_reservation_id=uuid4(),
            observed_at=datetime.now(UTC),
        )
        assert await runtime.execution.submit_entry(next_dispatch, submission) == "REJECTED"
        assert store.get(runtime.execution.client_oid(next_dispatch)) is None
        assert all(call.startswith("get_") for call in calls)
