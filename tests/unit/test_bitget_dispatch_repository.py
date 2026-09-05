from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository


class Cursor:
    def __init__(self, rows: list[dict[str, object] | None]) -> None:
        self.rows = rows
        self.statements: list[tuple[str, tuple[object, ...] | dict[str, object]]] = []

    def execute(self, statement: str, params: tuple[object, ...] | dict[str, object] = ()) -> None:
        self.statements.append((statement, params))

    def fetchone(self) -> dict[str, object] | None:
        return self.rows.pop(0)


class Connection:
    def __init__(self, cursor: Cursor) -> None:
        self.cursor_value = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> Cursor:
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _row(dispatch_id: UUID, *, claimed_by: str | None = None) -> dict[str, object]:
    return {
        "id": dispatch_id,
        "state": "QUEUED",
        "claimed_by": claimed_by,
        "lease_until": None,
        "attempts": 1,
        "pair_token": "BTCUSDT",
        "direction": "LONG",
        "entry_price": Decimal("64000"),
        "stop_loss": Decimal("63000"),
        "take_profits": ["65000"],
    }


def test_claim_uses_skip_locked_and_two_workers_receive_distinct_queued_rows() -> None:
    first_id, second_id = uuid4(), uuid4()
    first_cursor = Cursor([_row(first_id, claimed_by="worker-a")])
    second_cursor = Cursor([_row(second_id, claimed_by="worker-b")])
    first_connection = Connection(first_cursor)
    second_connection = Connection(second_cursor)

    first = PostgresBitgetDispatchRepository(lambda: first_connection).claim("worker-a", 30)
    second = PostgresBitgetDispatchRepository(lambda: second_connection).claim("worker-b", 30)

    assert first is not None and first.id == first_id
    assert second is not None and second.id == second_id
    assert "FOR UPDATE SKIP LOCKED" in first_cursor.statements[0][0]
    assert "exchange = 'bitget'" in first_cursor.statements[0][0]
    assert "state = 'QUEUED'" in first_cursor.statements[0][0]
    assert first_connection.commits == second_connection.commits == 1


def test_claim_recovers_only_expired_lease_and_never_steals_active_lease() -> None:
    recovered_id = uuid4()
    active_cursor = Cursor([None])
    recovered_cursor = Cursor([_row(recovered_id, claimed_by="worker-b")])
    active_connection = Connection(active_cursor)
    recovered_connection = Connection(recovered_cursor)

    assert PostgresBitgetDispatchRepository(lambda: active_connection).claim("worker-b", 30) is None
    recovered = PostgresBitgetDispatchRepository(lambda: recovered_connection).claim("worker-b", 30)

    assert recovered is not None and recovered.id == recovered_id
    sql = recovered_cursor.statements[0][0]
    assert "claimed_by IS NULL OR lease_until <= now()" in sql
    assert "attempts = attempts + 1" in sql
    assert active_connection.commits == recovered_connection.commits == 1
