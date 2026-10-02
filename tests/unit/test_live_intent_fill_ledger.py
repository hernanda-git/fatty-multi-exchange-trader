"""Offline tests for fill-evidence persistence on confirmed live intents.

Audit 2026-09-27: 14 of 23 filled/reconciled intents had no ``fills`` row, so realized
PnL could only be understated. These tests pin the behaviour that a filled intent always
leaves ledger evidence, without inventing provider ids or PnL.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from fatty_trader.exchanges.bitget.live import LiveIntentRecord
from fatty_trader.storage.live_intents import (
    PostgresLiveIntentStore,
    insert_status_derived_fill,
)


class RecordingCursor:
    def __init__(self, row: Any = ("1234567890",)) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self._row = row

    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> object:
        self.statements.append((" ".join(statement.split()), params))
        return None

    def fetchone(self) -> Any:
        return self._row

    def fills_statements(self) -> list[tuple[str, tuple[Any, ...]]]:
        return [entry for entry in self.statements if "INSERT INTO fills" in entry[0]]


class RecordingConnection:
    def __init__(self, cursor: RecordingCursor) -> None:
        self._cursor = cursor
        self.committed = False

    def cursor(self) -> RecordingCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass


def filled_record(**overrides: Any) -> LiveIntentRecord:
    fields: dict[str, Any] = {
        "exchange": "bitget",
        "client_oid": "live-bitget-BTCUSDT-abcdef0123456789",
        "symbol": "BTCUSDT",
        "side": "short",
        "role": "ENTRY",
        "state": "filled",
        "requested_qty": Decimal("0.01"),
        "filled_qty": Decimal("0.01"),
        "avg_price": Decimal("68000"),
        "fee": Decimal("0.03"),
        "provider_order_id": "1234567890",
        "planned_leverage": 20,
        "planned_margin_usdt": Decimal("1"),
        "planned_notional_usdt": Decimal("680"),
        "margin_mode": "isolated",
        "balance_snapshot_id": uuid4(),
        "margin_reservation_id": uuid4(),
    }
    fields.update(overrides)
    return LiveIntentRecord(**fields)


def test_filled_intent_without_provider_fills_still_records_ledger_evidence() -> None:
    cursor = RecordingCursor()

    insert_status_derived_fill(cursor, filled_record())

    statements = cursor.fills_statements()
    assert len(statements) == 1
    statement, params = statements[0]
    # Idempotent: never duplicate an existing row for the same intent.
    assert "WHERE NOT EXISTS" in statement
    assert "ON CONFLICT (exchange, provider_fill_id) DO NOTHING" in statement
    fill_id = params[3]
    assert fill_id == "status-derived:1234567890"
    assert params[5] == Decimal("68000")
    assert params[6] == Decimal("0.01")
    assert params[9] == Decimal("0")  # realized PnL is not invented


def test_status_derived_fill_falls_back_to_the_client_order_id() -> None:
    cursor = RecordingCursor()

    insert_status_derived_fill(cursor, filled_record(provider_order_id=None))

    _, params = cursor.fills_statements()[0]
    assert params[3] == "status-derived:live-bitget-BTCUSDT-abcdef0123456789"


def test_unfilled_or_incomplete_intents_record_no_ledger_row() -> None:
    for overrides in (
        {"state": "requested"},
        {"state": "accepted"},
        {"state": "rejected"},
        {"filled_qty": Decimal("0")},
        {"avg_price": None},
        {"avg_price": Decimal("0")},
    ):
        cursor = RecordingCursor()
        insert_status_derived_fill(cursor, filled_record(**overrides))
        assert cursor.fills_statements() == [], overrides


def test_store_update_persists_and_commits_the_ledger_row() -> None:
    cursor = RecordingCursor()
    connection = RecordingConnection(cursor)
    store = PostgresLiveIntentStore(lambda: connection)  # type: ignore[arg-type]

    store.update(filled_record())

    assert connection.committed is True
    assert len(cursor.fills_statements()) == 1


def test_store_update_leaves_real_provider_fills_untouched() -> None:
    cursor = RecordingCursor()
    connection = RecordingConnection(cursor)
    store = PostgresLiveIntentStore(lambda: connection)  # type: ignore[arg-type]

    store.update(
        filled_record(
            provider_fills=(
                {
                    "fillId": "provider-fill-777",
                    "quantity": "0.01",
                    "price": "68000",
                    "fee": "0.03",
                },
            )
        )
    )

    statements = cursor.fills_statements()
    assert len(statements) == 2  # the provider fill, then the status-derived guard
    assert statements[0][1][3] == "provider-fill-777"
    # The guard still runs, but its SQL only fires when no row exists for the intent.
    assert statements[1][1][3] == "status-derived:1234567890"
