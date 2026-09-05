from decimal import Decimal

from fatty_trader.exchanges.bitget.live import LiveIntentRecord
from fatty_trader.storage.live_intents import PostgresLiveIntentStore


class Cursor:
    def __init__(self, row: tuple[str, ...]) -> None:
        self.row = row

    def execute(self, statement: str, params: tuple[object, ...] = ()) -> None:
        del statement, params

    def fetchone(self) -> tuple[str, ...]:
        return self.row


class Connection:
    def __init__(self, row: tuple[str, ...]) -> None:
        self.cursor_value = Cursor(row)

    def cursor(self) -> Cursor:
        return self.cursor_value

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


def test_restarted_store_retains_durable_readback_fields() -> None:
    row = (
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
        '["fill-1", "fill-2"]',
    )
    connection = Connection(row)
    restarted_store = PostgresLiveIntentStore(lambda: connection)

    record = restarted_store.get("live-bitget-BTCUSDT-0011223344556677")

    assert record == LiveIntentRecord(
        exchange="bitget",
        client_oid="live-bitget-BTCUSDT-0011223344556677",
        symbol="BTCUSDT",
        side="BUY",
        role="ENTRY",
        state="filled",
        requested_qty=Decimal("0.001"),
        filled_qty=Decimal("0.001"),
        avg_price=Decimal("50000"),
        fee=Decimal("0.03"),
        provider_order_id="provider-1",
        provider_fill_ids=("fill-1", "fill-2"),
    )
