"""Durable live-trading schema + migrations (Plan Task 3, TDD).

Fresh-schema test: INITIAL + LIVE applied from scratch exposes every live table.
Migration test: INITIAL applied (simulating the deployed DB), rows inserted,
then migrations run — data must survive and new tables must appear.
"""

from __future__ import annotations

import sqlite3
from typing import Any


class SqliteCursorAdapter:
    """Adapt sqlite3 to the (execute/fetchall) cursor protocols in storage.

    Two sqlite-only shims, both confined to tests: multi-statement scripts go
    through ``executescript``, and the frozen v0 DDL's Postgres
    ``DEFAULT now()`` is rewritten to ``DEFAULT CURRENT_TIMESTAMP``
    (production DDL is untouched).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._cur = conn.cursor()

    def execute(self, statement: str) -> object:
        script = statement.replace("DEFAULT now()", "DEFAULT CURRENT_TIMESTAMP")
        parts = [part.strip() for part in script.split(";") if part.strip()]
        if len(parts) > 1:
            self._conn.executescript(script)
        else:
            self._cur.execute(script)
        return None

    def fetchall(self) -> list[Any]:
        return self._cur.fetchall()


def make_db() -> tuple[sqlite3.Connection, SqliteCursorAdapter]:
    conn = sqlite3.connect(":memory:")
    return conn, SqliteCursorAdapter(conn)


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
    return {row[0] for row in rows}


def column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


LIVE_TABLES = {
    "live_order_intents",
    "fills",
    "balance_snapshots",
    "position_snapshots",
    "protection_states",
    "reconciliation_state",
}


def test_fresh_schema_contains_all_live_tables() -> None:
    from fatty_trader.storage.schema import apply_initial_schema, apply_live_schema

    conn, cur = make_db()
    try:
        apply_initial_schema(cur)
        apply_live_schema(cur)
        names = table_names(conn)
        assert names >= LIVE_TABLES, f"missing: {LIVE_TABLES - names}"
    finally:
        conn.close()


def test_live_order_intents_columns_and_constraints() -> None:
    from fatty_trader.storage.schema import LIVE_SCHEMA_SQL, apply_live_schema

    for col in (
        "exchange",
        "client_order_id",
        "provider_order_id",
        "symbol",
        "side",
        "role",
        "requested_qty",
        "acknowledged_qty",
        "filled_qty",
        "requested_price",
        "acknowledged_price",
        "filled_price",
        "leverage",
        "margin_mode",
        "state",
    ):
        assert col in LIVE_SCHEMA_SQL, f"live_order_intents missing column {col}"
    assert "UNIQUE (exchange, client_order_id)" in LIVE_SCHEMA_SQL
    assert "UNIQUE (exchange, provider_order_id)" in LIVE_SCHEMA_SQL
    for state in (
        "requested",
        "acknowledged",
        "filled",
        "cancelled",
        "rejected",
        "unknown",
        "reconciled",
    ):
        assert state in LIVE_SCHEMA_SQL, f"state {state!r} not in live schema"

    conn, cur = make_db()
    try:
        apply_live_schema(cur)
        assert {
            "exchange",
            "client_order_id",
            "provider_order_id",
            "symbol",
            "side",
            "role",
            "requested_qty",
            "filled_qty",
            "leverage",
            "margin_mode",
            "state",
        } <= column_names(conn, "live_order_intents")
    finally:
        conn.close()


def test_live_schema_supports_fills_balances_positions_protection_reconcile() -> None:
    from fatty_trader.storage.schema import LIVE_SCHEMA_SQL, apply_live_schema

    for col in ("price", "quantity", "fee", "fee_ccy", "realized_pnl"):
        assert col in LIVE_SCHEMA_SQL, f"fills missing column {col}"
    for col in ("total_balance", "available_balance", "equity", "margin_coin", "captured_at"):
        assert col in LIVE_SCHEMA_SQL, f"balance_snapshots missing column {col}"
    for col in (
        "symbol",
        "side",
        "size",
        "entry_price",
        "mark_price",
        "liquidation_price",
        "leverage",
        "margin_mode",
        "unrealized_pnl",
        "captured_at",
    ):
        assert col in LIVE_SCHEMA_SQL, f"position_snapshots missing column {col}"
    for col in ("sl_order_id", "tp_order_id", "state", "updated_at"):
        assert col in LIVE_SCHEMA_SQL, f"protection_states missing column {col}"
    for col in ("last_run_at", "last_success_at", "mismatch_count"):
        assert col in LIVE_SCHEMA_SQL, f"reconciliation_state missing column {col}"

    conn, cur = make_db()
    try:
        apply_live_schema(cur)
        names = table_names(conn)
        assert names >= LIVE_TABLES
        assert {"price", "quantity", "fee", "realized_pnl"} <= column_names(conn, "fills")
        assert {"total_balance", "available_balance", "equity"} <= column_names(
            conn, "balance_snapshots"
        )
    finally:
        conn.close()


def test_order_intent_state_validator() -> None:
    from fatty_trader.storage.schema import ORDER_INTENT_STATES, validate_order_intent_state

    assert {
        "requested",
        "acknowledged",
        "filled",
        "cancelled",
        "rejected",
        "unknown",
        "reconciled",
    } == ORDER_INTENT_STATES
    for state in ORDER_INTENT_STATES:
        assert validate_order_intent_state(state) == state
    for bad in ("QUEUED", "", "FILLED", "pending"):
        try:
            validate_order_intent_state(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def test_unique_client_order_id_per_exchange_enforced() -> None:
    import sqlite3 as _sqlite3

    from fatty_trader.storage.schema import apply_live_schema

    conn, cur = make_db()
    try:
        apply_live_schema(cur)
        conn.execute(
            "INSERT INTO live_order_intents (id, exchange, client_order_id, symbol, side,"
            " role, state, requested_qty) VALUES ('a', 'binance', 'c1', 'BTCUSDT', 'BUY',"
            " 'ENTRY', 'requested', 1)"
        )
        try:
            conn.execute(
                "INSERT INTO live_order_intents (id, exchange, client_order_id, symbol, side,"
                " role, state, requested_qty) VALUES ('b', 'binance', 'c1', 'BTCUSDT', 'BUY',"
                " 'ENTRY', 'requested', 1)"
            )
        except _sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("duplicate (exchange, client_order_id) was accepted")
        # Same client id on the other exchange is fine.
        conn.execute(
            "INSERT INTO live_order_intents (id, exchange, client_order_id, symbol, side,"
            " role, state, requested_qty) VALUES ('c', 'bitget', 'c1', 'BTCUSDT', 'BUY',"
            " 'ENTRY', 'requested', 1)"
        )
    finally:
        conn.close()


def test_provider_order_id_unique_where_present_nulls_allowed() -> None:
    import sqlite3 as _sqlite3

    from fatty_trader.storage.schema import apply_live_schema

    conn, cur = make_db()
    try:
        apply_live_schema(cur)
        conn.execute(
            "INSERT INTO live_order_intents (id, exchange, client_order_id, symbol, side,"
            " role, state, requested_qty) VALUES ('a', 'binance', 'c1', 'BTCUSDT', 'BUY',"
            " 'ENTRY', 'requested', 1)"
        )
        conn.execute(
            "INSERT INTO live_order_intents (id, exchange, client_order_id, symbol, side,"
            " role, state, requested_qty) VALUES ('b', 'binance', 'c2', 'BTCUSDT', 'BUY',"
            " 'ENTRY', 'requested', 1)"
        )
        conn.execute("UPDATE live_order_intents SET provider_order_id = 'p1' WHERE id = 'a'")
        try:
            conn.execute("UPDATE live_order_intents SET provider_order_id = 'p1' WHERE id = 'b'")
        except _sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("duplicate (exchange, provider_order_id) was accepted")
    finally:
        conn.close()


def test_invalid_intent_state_rejected_by_check_constraint() -> None:
    import sqlite3 as _sqlite3

    from fatty_trader.storage.schema import apply_live_schema

    conn, cur = make_db()
    try:
        apply_live_schema(cur)
        try:
            conn.execute(
                "INSERT INTO live_order_intents (id, exchange, client_order_id, symbol, side,"
                " role, state, requested_qty) VALUES ('a', 'binance', 'c1', 'BTCUSDT', 'BUY',"
                " 'ENTRY', 'BOGUS', 1)"
            )
        except _sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("invalid intent state was accepted")
    finally:
        conn.close()


def test_migration_from_deployed_schema_preserves_data() -> None:
    from fatty_trader.storage.migrations import apply_migrations
    from fatty_trader.storage.schema import apply_initial_schema

    conn, cur = make_db()
    try:
        apply_initial_schema(cur)  # what is currently deployed
        conn.execute(
            "INSERT INTO telegram_messages (id, channel_id, message_id, revision_hash,"
            " raw_text, intake_state) VALUES ('11111111-1111-1111-1111-111111111111', 1, 2,"
            " 'ab', 'hello', 'RECEIVED')"
        )
        conn.execute(
            "INSERT INTO orders (id, exchange, client_order_id, role, state)"
            " VALUES ('22222222-2222-2222-2222-222222222222', 'binance', 'keep-me',"
            " 'ENTRY', 'requested')"
        )
        applied = apply_migrations(cur)
        assert applied, "expected pending migrations to be applied"
        names = table_names(conn)
        assert names >= LIVE_TABLES, f"missing after migrate: {LIVE_TABLES - names}"
        kept = conn.execute(
            "SELECT client_order_id FROM orders WHERE exchange = 'binance'"
        ).fetchall()
        assert kept == [("keep-me",)]
        msgs = conn.execute("SELECT COUNT(*) FROM telegram_messages").fetchone()
        assert msgs == (1,)
    finally:
        conn.close()


def test_migrations_are_idempotent_and_pending_only() -> None:
    from fatty_trader.storage.migrations import MIGRATIONS, apply_migrations
    from fatty_trader.storage.schema import apply_initial_schema

    assert [v for v, _ in MIGRATIONS] == sorted(v for v, _ in MIGRATIONS)
    assert MIGRATIONS[0][0] >= 1

    conn, cur = make_db()
    try:
        apply_initial_schema(cur)
        first = apply_migrations(cur)
        assert first == [v for v, _ in MIGRATIONS]
        # New live row written between runs must survive the second run.
        conn.execute(
            "INSERT INTO live_order_intents (id, exchange, client_order_id, symbol, side,"
            " role, state, requested_qty) VALUES ('a', 'binance', 'c1', 'BTCUSDT', 'BUY',"
            " 'ENTRY', 'requested', 1)"
        )
        second = apply_migrations(cur)
        assert second == []
        kept = conn.execute(
            "SELECT COUNT(*) FROM live_order_intents WHERE client_order_id = 'c1'"
        ).fetchone()
        assert kept == (1,)
        versions = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        assert [row[0] for row in versions] == [v for v, _ in MIGRATIONS]
    finally:
        conn.close()
