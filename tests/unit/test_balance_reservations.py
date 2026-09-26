from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fatty_trader.storage.balance_reservations import (
    BalanceAdmission,
    PostgresBitgetMarginReservationRepository,
)


class Cursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, statement: str, params: tuple[object, ...] = ()) -> None:
        self.calls.append((statement, params))

    def fetchone(self) -> tuple[object, ...]:
        # Active reservation sum query returns zero; INSERT ... RETURNING returns IDs.
        statement = self.calls[-1][0]
        if "SUM(planned_margin_usdt)" in statement:
            return (Decimal("0"),)
        if "INSERT INTO balance_snapshots" in statement:
            return (UUID("12345678-1234-5678-1234-567812345678"),)
        return (UUID("87654321-4321-8765-4321-876543218765"),)


class Connection:
    def __init__(self) -> None:
        self.cursor_value = Cursor()
        self.committed = False

    def cursor(self) -> Cursor:
        return self.cursor_value

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        raise AssertionError("unexpected rollback")


def test_reserve_serializes_fresh_balance_and_keeps_unknown_commitments() -> None:
    connection = Connection()
    repository = PostgresBitgetMarginReservationRepository(lambda: connection)

    result = repository.reserve(
        exchange="bitget",
        dispatch_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        client_order_id="live-bitget-BTCUSDT-1",
        total_balance=Decimal("100"),
        available_balance=Decimal("100"),
        equity=Decimal("100"),
        margin_coin="USDT",
        observed_at=datetime.now(UTC),
        planned_margin_usdt=Decimal("10"),
        headroom=Decimal("0.5"),
        ttl=timedelta(seconds=30),
        # Test cap, deliberately above the planned margin: this case exercises
        # serialization, not the LIVE 1 USDT ceiling.
        max_margin_per_trade_usdt=Decimal("100"),
    )

    assert result.accepted is True
    assert result.snapshot_id == UUID("12345678-1234-5678-1234-567812345678")
    assert result.reservation_id == UUID("87654321-4321-8765-4321-876543218765")
    sql = "\n".join(statement for statement, _ in connection.cursor_value.calls)
    assert "pg_advisory_xact_lock(hashtext(%s))" in sql
    assert "state = 'unknown'" in sql
    assert "state IN ('reserved', 'unknown')" in sql
    assert "INSERT INTO balance_snapshots" in sql
    assert "INSERT INTO bitget_margin_reservations" in sql
    assert connection.committed is True


def test_reserve_rejects_planned_margin_above_configured_cap() -> None:
    connection = Connection()
    result = PostgresBitgetMarginReservationRepository(lambda: connection).reserve(
        exchange="bitget",
        dispatch_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        client_order_id="live-bitget-BTCUSDT-1",
        total_balance=Decimal("100"),
        available_balance=Decimal("100"),
        equity=Decimal("100"),
        margin_coin="USDT",
        observed_at=datetime.now(UTC),
        planned_margin_usdt=Decimal("10"),
        headroom=Decimal("0.5"),
        ttl=timedelta(seconds=30),
        max_margin_per_trade_usdt=Decimal("1"),
    )

    assert result == BalanceAdmission.rejected("margin-cap-exceeded")
    assert connection.committed is False
    assert "INSERT INTO bitget_margin_reservations" not in "\n".join(
        statement for statement, _ in connection.cursor_value.calls
    )


def test_reserve_rejects_invalid_margin_cap() -> None:
    connection = Connection()
    result = PostgresBitgetMarginReservationRepository(lambda: connection).reserve(
        exchange="bitget",
        dispatch_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        client_order_id="live-bitget-BTCUSDT-1",
        total_balance=Decimal("100"),
        available_balance=Decimal("100"),
        equity=Decimal("100"),
        margin_coin="USDT",
        observed_at=datetime.now(UTC),
        planned_margin_usdt=Decimal("1"),
        headroom=Decimal("0.5"),
        ttl=timedelta(seconds=30),
        max_margin_per_trade_usdt=Decimal("0"),
    )

    assert result == BalanceAdmission.rejected("invalid-margin-cap")
    assert "INSERT INTO bitget_margin_reservations" not in "\n".join(
        statement for statement, _ in connection.cursor_value.calls
    )


def test_reserve_returns_rejection_without_creating_margin_reservation() -> None:
    class OvercommittedCursor(Cursor):
        def fetchone(self) -> tuple[object, ...]:
            if "SUM(planned_margin_usdt)" in self.calls[-1][0]:
                return (Decimal("45"),)
            return super().fetchone()

    connection = Connection()
    connection.cursor_value = OvercommittedCursor()
    result = PostgresBitgetMarginReservationRepository(lambda: connection).reserve(
        exchange="bitget",
        dispatch_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        client_order_id="live-bitget-BTCUSDT-1",
        total_balance=Decimal("100"),
        available_balance=Decimal("100"),
        equity=Decimal("100"),
        margin_coin="USDT",
        observed_at=datetime.now(UTC),
        planned_margin_usdt=Decimal("10"),
        headroom=Decimal("0.5"),
        ttl=timedelta(seconds=30),
        max_margin_per_trade_usdt=Decimal("100"),
    )

    assert result == BalanceAdmission.rejected("insufficient-reserved-headroom")
    assert "INSERT INTO bitget_margin_reservations" not in "\n".join(
        statement for statement, _ in connection.cursor_value.calls
    )


def test_acknowledged_order_keeps_margin_reservation_active() -> None:
    connection = Connection()
    repository = PostgresBitgetMarginReservationRepository(lambda: connection)

    repository.resolve(UUID("87654321-4321-8765-4321-876543218765"), "ACKNOWLEDGED")

    assert connection.committed is True
    statement, params = connection.cursor_value.calls[-1]
    assert "state = state" in statement
    assert params == (UUID("87654321-4321-8765-4321-876543218765"),)
