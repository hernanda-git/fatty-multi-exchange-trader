from __future__ import annotations

from decimal import Decimal
from uuid import UUID

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


def test_claim_persists_entry_admission_fields() -> None:
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
    )

    assert PostgresLiveIntentStore(lambda: connection).claim(record) is True

    assert "leverage" in connection.cursor_value.statement
    assert "planned_margin_usdt" in connection.cursor_value.statement
    assert "balance_snapshot_id" in connection.cursor_value.statement
    assert "margin_reservation_id" in connection.cursor_value.statement
    assert record.planned_leverage in connection.cursor_value.params
    assert record.planned_margin_usdt in connection.cursor_value.params
    assert record.margin_reservation_id in connection.cursor_value.params
