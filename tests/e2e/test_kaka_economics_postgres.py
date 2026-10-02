"""Kaka-only SQL proof; dedicated Unix test socket, isolated disposable schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from fatty_trader.kaka.parser import parse_kaka_event
from fatty_trader.kaka.worker import handle_event
from fatty_trader.storage.migrations import PAPER_KAKA_SCHEMA_SQL


@pytest.fixture
def paper_db():
    dsn = os.environ.get("FATTY_TEST_POSTGRES_DSN", "")
    if not dsn:
        pytest.skip("Dedicated test PostgreSQL required")
    info = psycopg.conninfo.conninfo_to_dict(dsn)
    assert info.get("dbname") == "fatty_test" and info.get("user") == "fatty_test"
    assert str(info.get("host", "")).startswith("/") and info.get("password") == "testonly"
    schema = "kaka_economics_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        assert admin.execute(
            "SELECT current_database(), current_user, inet_server_addr()"
        ).fetchone() == ("fatty_test", "fatty_test", None)
        admin.execute(f'CREATE SCHEMA "{schema}"')

    def connect():
        conn = psycopg.connect(dsn, row_factory=dict_row)
        conn.execute(f'SET search_path TO "{schema}", public')
        return conn

    with connect() as conn:
        conn.execute(PAPER_KAKA_SCHEMA_SQL)
    yield connect
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        assert not admin.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)
        ).fetchall()


def apply(conn, text, mid, price=Decimal("120")):
    handle_event(conn.cursor(), parse_kaka_event(text, message_id=mid), lambda _: price)


def test_quantity_weighted_dca_actual_exit_fees_and_duplicate(paper_db):
    with paper_db() as conn:
        apply(conn, "Long ETH ENTRY 100 SL 90 TP 130", 1)
        apply(conn, "ETH DCA 110", 2)
        apply(conn, "ETH DCA 110", 2)
    with paper_db() as conn:
        row = conn.execute("SELECT * FROM paper_kaka_trades").fetchone()
        assert row["legs"] == 2 and row["notional_usdt"] == 40
        quantity = Decimal(20) / 100 + Decimal(20) / 110
        assert row["entry_price"] == Decimal(40) / quantity
        apply(conn, "ETH Cancel TP", 3)
        row = conn.execute("SELECT * FROM paper_kaka_trades").fetchone()
        assert row["state"] == "open" and row["take_profit"] is None
        apply(conn, "ETH Close", 4)
    with paper_db() as conn:
        row = conn.execute("SELECT * FROM paper_kaka_trades").fetchone()
        expected = (quantity * 120 - 40 - (40 + quantity * 120) * Decimal("0.0006")).quantize(
            Decimal("0.000001")
        )
        assert row["realized_pnl_usdt"] == expected == Decimal("5.766691")
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM paper_kaka_events WHERE source_message_id=2"
            ).fetchone()["n"]
            == 1
        )
        print(f"PG_DCA_ENTRY={row['entry_price']} PG_DCA_PNL={row['realized_pnl_usdt']}")


def test_explicit_symbol_and_ambiguous_management(paper_db):
    with paper_db() as conn:
        apply(conn, "Long ETH ENTRY 100 SL 90", 1)
        apply(conn, "Long BTC ENTRY 200 SL 180", 2)
        apply(conn, "Move sl 95", 3)
        apply(conn, "ETH Move sl 96", 4)
        rows = conn.execute(
            "SELECT symbol, stop_loss FROM paper_kaka_trades ORDER BY symbol"
        ).fetchall()
        assert rows == [
            {"symbol": "BTCUSDT", "stop_loss": Decimal(180)},
            {"symbol": "ETHUSDT", "stop_loss": Decimal(96)},
        ]
        apply(conn, "SOL Close", 5)
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM paper_kaka_trades WHERE state='open'"
            ).fetchone()["n"]
            == 2
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM paper_kaka_events WHERE event_type LIKE 'UNMATCHED:%'"
            ).fetchone()["n"]
            == 2
        )


def test_concurrent_duplicate_and_distinct_dca(paper_db):
    with paper_db() as conn:
        apply(conn, "Long ETH ENTRY 100 SL 90", 1)

    def add(mid):
        with paper_db() as conn:
            apply(conn, "ETH DCA 110", mid)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(add, [2, 2, 3, 3]))
    with paper_db() as conn:
        row = conn.execute("SELECT legs, notional_usdt FROM paper_kaka_trades").fetchone()
        assert row == {"legs": 3, "notional_usdt": Decimal(60)}
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM paper_kaka_events WHERE event_type='ADD'"
            ).fetchone()["n"]
            == 2
        )


def test_rollback_does_not_consume_management(paper_db):
    with paper_db() as conn:
        apply(conn, "Long ETH ENTRY 100 SL 90", 1)
    with paper_db() as conn:
        apply(conn, "ETH DCA 110", 2)
        conn.rollback()
    with paper_db() as conn:
        apply(conn, "ETH DCA 110", 2)
    with paper_db() as conn:
        assert conn.execute("SELECT legs FROM paper_kaka_trades").fetchone()["legs"] == 2
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM paper_kaka_events WHERE event_type='ADD'"
            ).fetchone()["n"]
            == 1
        )
