"""PostgreSQL-serialized Bitget balance evidence and margin commitments."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4


class Cursor(Protocol):
    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> object: ...
    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@dataclass(frozen=True)
class BalanceAdmission:
    accepted: bool
    reason: str | None = None
    snapshot_id: UUID | None = None
    reservation_id: UUID | None = None

    @classmethod
    def rejected(cls, reason: str) -> BalanceAdmission:
        return cls(False, reason=reason)


class PostgresBitgetMarginReservationRepository:
    """Reserve margin under a per-exchange transaction advisory lock.

    The lock serializes this bot's workers only; the caller must obtain the provider
    account read immediately before calling this method and reject stale evidence.
    """

    def __init__(self, connection_factory: Callable[[], Connection]) -> None:
        self._connection_factory = connection_factory

    def reserve(
        self,
        *,
        exchange: str,
        dispatch_id: UUID,
        client_order_id: str,
        total_balance: Decimal,
        available_balance: Decimal,
        equity: Decimal,
        margin_coin: str,
        observed_at: datetime,
        planned_margin_usdt: Decimal,
        headroom: Decimal,
        ttl: timedelta,
    ) -> BalanceAdmission:
        if exchange != "bitget":
            return BalanceAdmission.rejected("unsupported-exchange")
        if any(
            not value.is_finite() or value <= 0
            for value in (total_balance, available_balance, equity, planned_margin_usdt)
        ):
            return BalanceAdmission.rejected("invalid-balance-snapshot")
        if not headroom.is_finite() or headroom <= 0 or headroom > 1:
            return BalanceAdmission.rejected("invalid-balance-headroom")
        if not margin_coin.strip() or observed_at.tzinfo is None or observed_at.utcoffset() is None:
            return BalanceAdmission.rejected("invalid-balance-snapshot")
        if ttl.total_seconds() <= 0:
            return BalanceAdmission.rejected("invalid-reservation-ttl")
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (exchange,))
            # Ambiguous expiry remains committed until evidence-led reconciliation.
            cursor.execute(
                """
                UPDATE bitget_margin_reservations
                SET state = 'unknown', resolved_at = CURRENT_TIMESTAMP,
                    resolution_reason = COALESCE(resolution_reason, 'reservation-expired')
                WHERE exchange = %s AND state = 'reserved' AND expires_at <= CURRENT_TIMESTAMP
                """,
                (exchange,),
            )
            snapshot_id = uuid4()
            cursor.execute(
                """
                INSERT INTO balance_snapshots
                    (id, exchange, total_balance, available_balance, equity,
                     margin_coin, captured_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    snapshot_id,
                    exchange,
                    total_balance,
                    available_balance,
                    equity,
                    margin_coin,
                    observed_at,
                ),
            )
            returned_snapshot = cursor.fetchone()
            if returned_snapshot is not None:
                snapshot_id = _uuid_from_row(returned_snapshot, snapshot_id)
            cursor.execute(
                """
                SELECT COALESCE(SUM(planned_margin_usdt), 0)
                FROM bitget_margin_reservations
                WHERE exchange = %s AND state IN ('reserved', 'unknown')
                """,
                (exchange,),
            )
            active = Decimal(str(_first(cursor.fetchone(), Decimal("0")) or "0"))
            if active + planned_margin_usdt > available_balance * headroom:
                connection.commit()
                return BalanceAdmission.rejected("insufficient-reserved-headroom")
            reservation_id = uuid4()
            cursor.execute(
                """
                INSERT INTO bitget_margin_reservations
                    (id, exchange, dispatch_id, client_order_id, balance_snapshot_id,
                     planned_margin_usdt, state, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'reserved', CURRENT_TIMESTAMP + %s::interval)
                RETURNING id
                """,
                (
                    reservation_id,
                    exchange,
                    dispatch_id,
                    client_order_id,
                    snapshot_id,
                    planned_margin_usdt,
                    f"{ttl.total_seconds()} seconds",
                ),
            )
            returned_reservation = cursor.fetchone()
            if returned_reservation is not None:
                reservation_id = _uuid_from_row(returned_reservation, reservation_id)
            connection.commit()
            return BalanceAdmission(True, snapshot_id=snapshot_id, reservation_id=reservation_id)
        except Exception:
            connection.rollback()
            raise

    def record(self, observation: Any) -> None:
        """Append a provider post-fill observation without synthesizing absent fields."""
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """INSERT INTO bitget_post_fill_reconciliations
                (id, exchange, client_order_id, planned_leverage, planned_margin_usdt,
                 planned_notional_usdt, observed_leverage, observed_margin_mode,
                 observed_quantity, observed_entry_price, observed_mark_price,
                 observed_margin_usdt, status, reason, observed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    uuid4(),
                    observation.exchange,
                    observation.client_order_id,
                    observation.planned_leverage,
                    observation.planned_margin_usdt,
                    observation.planned_notional_usdt,
                    observation.observed_leverage,
                    observation.observed_margin_mode,
                    observation.observed_quantity,
                    observation.observed_entry_price,
                    observation.observed_mark_price,
                    observation.observed_margin_usdt,
                    observation.status,
                    observation.reason,
                    observation.observed_at,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def resolve(self, reservation_id: UUID, outcome: str) -> None:
        normalized = outcome.upper()
        if normalized in {"ACKNOWLEDGED", "SUBMITTED"}:
            connection = self._connection_factory()
            try:
                cursor = connection.cursor()
                cursor.execute(
                    "UPDATE bitget_margin_reservations SET state = state WHERE id = %s",
                    (reservation_id,),
                )
                connection.commit()
                return
            except Exception:
                connection.rollback()
                raise
        state = {
            "FILLED": "consumed",
            "PARTIAL": "consumed",
            "REJECTED": "released",
            "CANCELLED": "released",
            "UNKNOWN": "unknown",
        }.get(outcome.upper())
        if state is None:
            raise ValueError("unknown margin reservation outcome")
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE bitget_margin_reservations
                SET state = CASE WHEN state IN ('consumed', 'released') THEN state ELSE %s END,
                    resolved_at = COALESCE(resolved_at, CURRENT_TIMESTAMP),
                    resolution_reason = COALESCE(resolution_reason, %s)
                WHERE id = %s
                """,
                (state, outcome.upper(), reservation_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _first(row: Any, default: Any) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return next(iter(row.values()), default)
    if isinstance(row, (tuple, list)):
        return row[0] if row else default
    return row


def _uuid_from_row(row: Any, default: UUID) -> UUID:
    value = _first(row, default)
    return value if isinstance(value, UUID) else UUID(str(value))
