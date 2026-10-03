"""Exercise the recovery SELECT on a local, in-memory SQL database only."""

import sqlite3
from uuid import UUID

import pytest

from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository


@pytest.fixture
def database():
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE live_order_intents (
            exchange TEXT, client_order_id TEXT, role TEXT, state TEXT,
            filled_qty NUMERIC, requested_qty NUMERIC, symbol TEXT, side TEXT,
            margin_reservation_id TEXT, provider_order_id TEXT, created_at TEXT);
        CREATE TABLE bitget_margin_reservations (
            id TEXT, exchange TEXT, client_order_id TEXT, dispatch_id TEXT,
            environment TEXT, symbol TEXT, state TEXT, resolution_reason TEXT,
            resolved_at TEXT, created_at TEXT);
        CREATE TABLE bitget_verified_close_bindings (
            exchange TEXT, reservation_id TEXT, close_client_order_id TEXT);
        CREATE TABLE dispatches (
            id TEXT, exchange TEXT, source_type TEXT, source_id TEXT, state TEXT,
            claimed_by TEXT, attempts INTEGER, created_at TEXT);
        CREATE TABLE canonical_signals (
            id TEXT, message_id TEXT, pair_token TEXT, direction TEXT,
            entry_price NUMERIC, stop_loss NUMERIC, take_profits TEXT);
        CREATE TABLE telegram_messages (id TEXT, channel_id INTEGER, message_id INTEGER);
        INSERT INTO dispatches VALUES (
            '12345678-1234-5678-1234-567812345678', 'bitget', 'canonical_signal',
            'source', 'FILLED', 'worker', 1, '2026-01-01');
        INSERT INTO canonical_signals VALUES ('source','message','BTCUSDT','LONG',100,90,'[110]');
        INSERT INTO telegram_messages VALUES ('message',7,9);
        INSERT INTO live_order_intents VALUES (
            'bitget','entry','ENTRY','filled',2,2,'BTCUSDT','BUY','reservation',
            'entry-provider','2026-01-01');
        INSERT INTO live_order_intents VALUES (
            'bitget','close','CLOSE','filled',2,2,'BTCUSDT','SELL',NULL,
            'close-provider','2026-01-02');
        INSERT INTO bitget_margin_reservations VALUES (
            'reservation','bitget','entry','12345678-1234-5678-1234-567812345678',
            'LIVE','BTCUSDT','released','verified-close:close','2026-01-03','2026-01-01');
        INSERT INTO bitget_verified_close_bindings VALUES ('bitget','reservation','close');
    """)
    yield connection
    connection.close()


def test_verified_close_retirement_is_not_a_recovery_candidate(database):
    assert PostgresBitgetDispatchRepository(lambda: database).recovery_candidates() == []


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE bitget_margin_reservations SET state='consumed'",
        "UPDATE bitget_margin_reservations SET resolution_reason='flat'",
        "UPDATE bitget_margin_reservations SET resolution_reason='reconciled'",
        "UPDATE bitget_margin_reservations SET resolution_reason='verified-close:other'",
        "UPDATE bitget_margin_reservations SET resolved_at=NULL",
        "UPDATE bitget_margin_reservations SET environment=NULL",
        "UPDATE bitget_margin_reservations SET environment='OTHER'",
        "UPDATE bitget_margin_reservations SET symbol='ETHUSDT'",
        "UPDATE live_order_intents SET margin_reservation_id='other' WHERE role='ENTRY'",
        "DELETE FROM bitget_verified_close_bindings",
        "UPDATE bitget_verified_close_bindings SET reservation_id='other'",
        "UPDATE bitget_verified_close_bindings SET exchange='binance'",
        "UPDATE dispatches SET source_type='manual'",
        "UPDATE live_order_intents SET exchange='binance' WHERE role='CLOSE'",
        "UPDATE live_order_intents SET symbol='ETHUSDT' WHERE role='CLOSE'",
        "UPDATE live_order_intents SET side='BUY' WHERE role='CLOSE'",
        "UPDATE live_order_intents SET state='requested' WHERE role='CLOSE'",
        "UPDATE live_order_intents SET role='ENTRY' WHERE client_order_id='close'",
        "UPDATE live_order_intents SET filled_qty=1 WHERE role='CLOSE'",
        "UPDATE live_order_intents SET provider_order_id=NULL WHERE role='CLOSE'",
        "UPDATE live_order_intents SET provider_order_id='' WHERE role='CLOSE'",
        "UPDATE live_order_intents SET requested_qty=3 WHERE role='CLOSE'",
        "UPDATE live_order_intents SET created_at='2025-12-31' WHERE role='CLOSE'",
        "UPDATE live_order_intents SET created_at='2026-01-04' WHERE role='CLOSE'",
        "UPDATE live_order_intents SET state='unknown' WHERE client_order_id='entry'",
    ],
)
def test_incomplete_or_mismatched_retirement_still_requires_recovery(database, mutation):
    database.execute(mutation)
    rows = PostgresBitgetDispatchRepository(lambda: database).recovery_candidates()
    assert len(rows) == 1
    assert rows[0][0].id == UUID("12345678-1234-5678-1234-567812345678")
    assert rows[0][1] == "entry"


def test_unowned_legacy_fill_still_fails_closed(database):
    database.execute("DELETE FROM bitget_margin_reservations")

    class Cursor:
        def __init__(self):
            self.value = database.cursor()

        def execute(self, statement, params=()):
            self.value.execute(statement.replace("%s", "?"), params)

        def fetchone(self):
            return self.value.fetchone()

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            database.commit()

        def rollback(self):
            database.rollback()

    repository = PostgresBitgetDispatchRepository(Connection)
    assert repository.recovery_candidates() == []
    assert repository.inventory_issues("LIVE") == ["orphan-entry-intent"]
