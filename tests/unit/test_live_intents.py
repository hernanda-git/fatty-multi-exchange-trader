from decimal import Decimal

from fatty_trader.exchanges.bitget.live import LiveIntentRecord
from fatty_trader.storage.live_intents import PostgresLiveIntentStore


class Cursor:
    def __init__(self, row=None):
        self.row = row
        self.calls = []

    def execute(self, statement, params=()):
        self.calls.append((statement, params))

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, row=None):
        self.cursor_value = Cursor(row)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def record() -> LiveIntentRecord:
    return LiveIntentRecord(
        exchange="bitget",
        client_oid="live-bitget-BTCUSDT-0011223344556677",
        symbol="BTCUSDT",
        side="BUY",
        requested_qty=Decimal("0.001"),
    )


def test_save_uses_stable_uuid_and_commit() -> None:
    connection = Connection()
    store = PostgresLiveIntentStore(lambda: connection)
    store.save(record())
    params = connection.cursor_value.calls[0][1]
    assert str(params[0])
    assert params[1:3] == ("bitget", record().client_oid)
    assert connection.commits == 1


def test_get_maps_database_row_without_exposing_payload() -> None:
    connection = Connection(
        (
            "bitget",
            "live-bitget-BTCUSDT-0011223344556677",
            "BTCUSDT",
            "BUY",
            "ENTRY",
            "filled",
            "0.001",
            "0.001",
            "50000",
            "0.03",
            "provider-1",
        )
    )
    result = PostgresLiveIntentStore(lambda: connection).get(record().client_oid)
    assert result is not None
    assert result.state == "filled"
    assert result.filled_qty == Decimal("0.001")
    assert result.provider_order_id == "provider-1"
