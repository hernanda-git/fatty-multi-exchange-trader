from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest

from fatty_trader.exchanges.bitget.live import LiveIntentRecord
from fatty_trader.storage.live_intents import PostgresLiveIntentStore


class Cursor:
    def __init__(self) -> None:
        self.statement = ""
        self.params: tuple[object, ...] = ()

    def execute(self, statement: str, params: tuple[object, ...] = ()) -> None:
        self.statement = statement
        self.params = params

    def fetchone(self) -> object:
        return ("oid",)


class Connection:
    def __init__(self) -> None:
        self.cursor_value = Cursor()

    def cursor(self) -> Cursor:
        return self.cursor_value

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


@pytest.mark.parametrize("method", ["claim", "save"])
def test_insert_persists_entry_admission_fields(method) -> None:
    connection = Connection()
    record = LiveIntentRecord(
        exchange="bitget",
        client_oid="oid",
        symbol="BTCUSDT",
        side="BUY",
        requested_qty=Decimal("0.002"),
        planned_leverage=20,
        planned_margin_usdt=Decimal("10"),
        planned_notional_usdt=Decimal("128"),
        margin_mode="ISOLATED",
        balance_snapshot_id=UUID("12345678-1234-5678-1234-567812345678"),
        margin_reservation_id=UUID("87654321-4321-8765-4321-876543218765"),
        planned_stop_loss=Decimal("63000.1"),
        planned_take_profits=(Decimal("65000.1"), Decimal("66000.2")),
    )

    result = getattr(PostgresLiveIntentStore(lambda: connection), method)(record)
    assert result is (True if method == "claim" else None)

    assert "leverage" in connection.cursor_value.statement
    assert "planned_margin_usdt" in connection.cursor_value.statement
    assert "balance_snapshot_id" in connection.cursor_value.statement
    assert "margin_reservation_id" in connection.cursor_value.statement
    assert record.planned_leverage in connection.cursor_value.params
    assert record.planned_margin_usdt in connection.cursor_value.params
    assert record.margin_reservation_id in connection.cursor_value.params
    assert "planned_stop_loss" in connection.cursor_value.statement
    assert "planned_take_profits" in connection.cursor_value.statement
    assert record.planned_stop_loss in connection.cursor_value.params
    assert '["65000.1", "66000.2"]' in connection.cursor_value.params
    assert connection.cursor_value.statement.count("%s") == len(connection.cursor_value.params)


def test_get_restores_validated_decimal_plan_exactly():
    connection = Connection()
    connection.cursor_value.fetchone = lambda: (
        "bitget",
        "oid",
        "BTCUSDT",
        "BUY",
        "ENTRY",
        "filled",
        Decimal("0.002"),
        Decimal("0.002"),
        Decimal("64000"),
        Decimal("0.1"),
        "provider-1",
        [],
        None,
        None,
        None,
        None,
        None,
        None,
        Decimal("63000.1"),
        '["65000.1", "66000.2"]',
    )
    record = PostgresLiveIntentStore(lambda: connection).get("oid")
    assert record is not None
    assert record.planned_stop_loss == Decimal("63000.1")
    assert record.planned_take_profits == (Decimal("65000.1"), Decimal("66000.2"))


def test_get_retains_missing_historical_plan_without_inventing_prices():
    connection = Connection()
    connection.cursor_value.fetchone = lambda: (
        "bitget",
        "oid",
        "BTCUSDT",
        "BUY",
        "ENTRY",
        "filled",
        Decimal("0.002"),
        Decimal("0.002"),
        Decimal("64000"),
        Decimal("0.1"),
        "provider-1",
        [],
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    record = PostgresLiveIntentStore(lambda: connection).get("oid")
    assert record is not None
    assert record.planned_stop_loss is None
    assert record.planned_take_profits is None
