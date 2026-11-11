from decimal import Decimal

from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveIntentRecord
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
            '["fill-1", "fill-2"]',
        )
    )
    result = PostgresLiveIntentStore(lambda: connection).get(record().client_oid)
    assert result is not None
    assert result.state == "filled"
    assert result.filled_qty == Decimal("0.001")
    assert result.provider_order_id == "provider-1"
    assert result.fee == Decimal("0.03")
    assert result.provider_fill_ids == ("fill-1", "fill-2")


def test_get_accepts_jsonb_list_value() -> None:
    row = list(
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
            ["fill-1"],
        )
    )
    result = PostgresLiveIntentStore(lambda: Connection(tuple(row))).get(record().client_oid)

    assert result is not None
    assert result.provider_fill_ids == ("fill-1",)


def test_update_rejects_conflicting_provider_order_id() -> None:
    store = InMemoryLiveIntentStore()
    existing = record()
    existing.provider_order_id = "provider-a"
    conflicting = record()
    conflicting.provider_order_id = "provider-b"
    store.save(existing)

    try:
        store.update(conflicting)
    except ValueError as exc:
        assert "conflict" in str(exc)
    else:
        raise AssertionError("provider order-id conflict must fail closed")

def test_update_persists_requested_qty_in_sql() -> None:
    connection = Connection(("provider-1",))
    store = PostgresLiveIntentStore(lambda: connection)
    rec = record()
    rec.requested_qty = Decimal("0.024")
    rec.filled_qty = Decimal("0.024")
    rec.provider_order_id = "provider-1"
    rec.state = "filled"
    store.update(rec)
    sql, params = connection.cursor_value.calls[0]
    assert "requested_qty = %s" in sql
    assert params[2] == Decimal("0.024")
    assert connection.commits == 1

def test_in_memory_remaining_owned_quantity_does_not_double_count_aggregated_child() -> None:
    store = InMemoryLiveIntentStore()
    root = record()
    root.requested_qty = Decimal("9009")
    root.filled_qty = Decimal("36036")  # Already aggregated
    root.provider_fill_ids = ("f-market", "f-limit")
    child = record()
    child.client_oid = root.client_oid + "-limit"
    child.entry_leg = "limit"
    child.requested_qty = Decimal("27027")
    child.filled_qty = Decimal("27027")
    child.provider_fill_ids = ("f-limit",)
    store.save(root)
    store.save(child)
    assert store.remaining_owned_quantity(root.client_oid) == Decimal("36036")


def test_in_memory_remaining_owned_quantity_sums_unaggregated_child() -> None:
    store = InMemoryLiveIntentStore()
    root = record()
    root.requested_qty = Decimal("9009")
    root.filled_qty = Decimal("9009")  # Not aggregated yet
    root.provider_fill_ids = ("f-market",)
    child = record()
    child.client_oid = root.client_oid + "-limit"
    child.entry_leg = "limit"
    child.requested_qty = Decimal("27027")
    child.filled_qty = Decimal("27027")
    child.provider_fill_ids = ("f-limit",)
    store.save(root)
    store.save(child)
    assert store.remaining_owned_quantity(root.client_oid) == Decimal("36036")
