"""Production-v18 exact legacy binding must survive without guessing environment."""

from unittest.mock import patch
from uuid import uuid4

import psycopg
import pytest
from test_postgres_migration_and_margin_admission import _connect
from test_postgres_migration_and_margin_admission import postgres_schema as postgres_schema

from fatty_trader.storage.migrations import MIGRATIONS, apply_migrations
from fatty_trader.storage.schema import apply_initial_schema


def seed(dsn, schema, mismatch=False):
    with _connect(dsn, schema) as c:
        apply_initial_schema(c.cursor())
        with patch(
            "fatty_trader.storage.migrations.MIGRATIONS", [(v, s) for v, s in MIGRATIONS if v <= 18]
        ):
            apply_migrations(c.cursor())
        dispatch, snapshot, reservation, intent = (uuid4() for _ in range(4))
        c.execute(
            (
                "INSERT INTO dispatches (id,source_type,source_id,revision,exchange,sta"
                "te) VALUES (%s,'legacy',%s,%s,'bitget','QUEUED')"
            ),
            (dispatch, uuid4(), "a" * 64),
        )
        c.execute(
            (
                "INSERT INTO balance_snapshots (id,exchange,total_balance,available_bal"
                "ance,equity,margin_coin) VALUES (%s,'bitget',100,99,101,'USDT')"
            ),
            (snapshot,),
        )
        c.execute(
            (
                "INSERT INTO bitget_margin_reservations "
                "(id,exchange,dispatch_id,client_order_id,balance_snapshot_id,planned_m"
                "argin_usdt,state,expires_at) VALUES (%s,'bitget',%s,'legacy-hbar',%s,."
                "995904,'consumed',now())"
            ),
            (reservation, dispatch, snapshot),
        )
        c.execute(
            (
                "INSERT INTO live_order_intents (id,exchange,client_order_id,provider_o"
                "rder_id,symbol,side,role,state,requested_qty,filled_qty,filled_price,f"
                "ee,leverage,planned_margin_usdt,planned_notional_usdt,balance_snapshot"
                "_id,margin_reservation_id) VALUES (%s,'bitget','legacy-hbar','venue-un"
                "changed','HBARUSDT','BUY','ENTRY','filled',152,152,.13104,.002,20,%s,1"
                "9.91808,%s,%s)"
            ),
            (intent, "0.995905" if mismatch else "0.995904", snapshot, reservation),
        )


def evidence(c):
    return [
        c.execute(f"SELECT to_jsonb(t) FROM {table} t ORDER BY id").fetchall()
        for table in ("live_order_intents", "bitget_margin_reservations", "balance_snapshots")
    ]


def test_exact_legacy_binding_survives_upgrade(postgres_schema):
    dsn, schema = postgres_schema
    seed(dsn, schema)
    with _connect(dsn, schema) as c:
        before = evidence(c)
        apply_migrations(c.cursor())
        after = evidence(c)
        assert before[0] == after[0]
        assert before[2] == after[2]
        for (_,), (row,) in zip(before[1], after[1], strict=False):
            assert row["symbol"] is None
            assert row["legacy_binding_symbol"] == "HBARUSDT"
            assert row["binding_symbol"] == "HBARUSDT"
            assert row["environment"] is None
        assert [
            {k: v for k, v in row.items() if k in old}
            for (row,), (old,) in zip(after[1], before[1], strict=False)
        ] == [old for (old,) in before[1]]
        assert apply_migrations(c.cursor()) == []
        with pytest.raises(psycopg.errors.ForeignKeyViolation), c.transaction():
            c.execute("UPDATE live_order_intents SET symbol='BTCUSDT'")


@pytest.mark.parametrize(
    "invalid",
    ["margin", "client", "exchange", "snapshot", "role", "null-margin", "blank-symbol", "orphan"],
)
def test_invalid_legacy_binding_still_rejects_upgrade(postgres_schema, invalid):
    dsn, schema = postgres_schema
    seed(dsn, schema)
    changes = {
        "margin": "planned_margin_usdt=.995905",
        "client": "client_order_id='wrong-owner'",
        "exchange": "exchange='binance'",
        "snapshot": "balance_snapshot_id=NULL",
        "role": "role='SL'",
        "null-margin": "planned_margin_usdt=NULL",
        "blank-symbol": "symbol=''",
        "orphan": "margin_reservation_id='00000000-0000-0000-0000-000000000001'",
    }
    with _connect(dsn, schema) as c:
        c.execute("UPDATE live_order_intents SET " + changes[invalid])
    with _connect(dsn, schema) as c:
        before = evidence(c)
        with (
            pytest.raises((psycopg.errors.ForeignKeyViolation, psycopg.errors.CheckViolation)),
            c.transaction(),
        ):
            apply_migrations(c.cursor())
        assert evidence(c) == before


def test_new_reservation_cannot_claim_legacy_provenance(postgres_schema):
    dsn, schema = postgres_schema
    seed(dsn, schema)
    with _connect(dsn, schema) as c:
        apply_migrations(c.cursor())
        with pytest.raises(psycopg.errors.ForeignKeyViolation), c.transaction():
            c.execute(
                """INSERT INTO bitget_margin_reservations
                    (id,exchange,dispatch_id,client_order_id,balance_snapshot_id,
                     planned_margin_usdt,state,expires_at,legacy_binding_symbol)
                    SELECT %s,exchange,dispatch_id,'new-client',balance_snapshot_id,
                           planned_margin_usdt,'released',expires_at,legacy_binding_symbol
                    FROM bitget_margin_reservations""",
                (uuid4(),),
            )
        with pytest.raises(psycopg.errors.CheckViolation), c.transaction():
            c.execute("UPDATE bitget_margin_reservations SET symbol='HBARUSDT'")
        for statement in (
            "UPDATE admission_legacy_bindings SET symbol='BTCUSDT'",
            "DELETE FROM admission_legacy_bindings",
            "INSERT INTO admission_legacy_bindings SELECT * FROM admission_legacy_bindings",
        ):
            with (
                pytest.raises(
                    psycopg.errors.CheckViolation, match="immutable legacy admission provenance"
                ),
                c.transaction(),
            ):
                c.execute(statement)
