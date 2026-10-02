"""PostgreSQL-serialized Bitget balance evidence and margin commitments."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from fatty_trader.intake.freshness import SOURCE_ELIGIBLE_SQL, expire_source_dispatch


class Cursor(Protocol):
    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> object: ...
    def fetchone(self) -> Any: ...
    def fetchall(self) -> list[Any]: ...


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
        max_margin_per_trade_usdt: Decimal,
        symbol: str | None = None,
        environment: str | None = None,
        max_positions: int | None = None,
        provider_active_symbols: tuple[str, ...] | None = None,
        max_snapshot_age: timedelta = timedelta(seconds=30),
        max_future_skew: timedelta = timedelta(seconds=1),
    ) -> BalanceAdmission:
        """Reserve margin for one dispatch.

        ``max_margin_per_trade_usdt`` is deliberately a required argument: it is a
        hard LIVE ceiling, and a caller that forgets it would otherwise reserve
        uncapped margin. The LIVE risk policy passes exactly 1 USDT.
        """
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
        if not max_margin_per_trade_usdt.is_finite() or max_margin_per_trade_usdt <= 0:
            return BalanceAdmission.rejected("invalid-margin-cap")
        # Independent of sizing: even a caller bug cannot reserve above the cap.
        if planned_margin_usdt > max_margin_per_trade_usdt:
            return BalanceAdmission.rejected("margin-cap-exceeded")
        if ttl.total_seconds() <= 0:
            return BalanceAdmission.rejected("invalid-reservation-ttl")
        if (
            not symbol
            or not symbol.strip()
            or environment not in {"DEMO", "LIVE"}
            or type(max_positions) is not int
            or max_positions <= 0
            or provider_active_symbols is None
        ):
            return BalanceAdmission.rejected("missing-position-admission-evidence")
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (exchange,))
            cursor.execute(
                "SELECT d.state, COALESCE(("
                + SOURCE_ELIGIBLE_SQL.format(alias="tm")
                + "),false) FROM dispatches d LEFT JOIN canonical_signals s ON s.id=d.source_id "
                "LEFT JOIN telegram_messages tm ON tm.id=s.message_id "
                "WHERE d.id=%s FOR UPDATE OF d",
                (dispatch_id,),
            )
            source = cursor.fetchone()
            entry_states = {"QUEUED", "PREFLIGHT", "SIZED", "VALIDATED", "SUBMITTING"}
            if source is None or source[0] not in entry_states or not source[1]:
                if source is not None and source[0] in entry_states:
                    expire_source_dispatch(cursor, dispatch_id, source[0])
                connection.commit()
                return BalanceAdmission.rejected("stale-source-message")
            # CURRENT_TIMESTAMP is transaction-start time: unsafe after lock waits.
            cursor.execute("SELECT clock_timestamp()")
            now = _first(cursor.fetchone(), None)
            if max_snapshot_age <= timedelta(0) or max_future_skew < timedelta(0):
                connection.commit()
                return BalanceAdmission.rejected("invalid-snapshot-age-policy")
            if now - observed_at > max_snapshot_age:
                connection.commit()
                return BalanceAdmission.rejected("stale-balance-snapshot")
            if observed_at - now > max_future_skew:
                connection.commit()
                return BalanceAdmission.rejected("future-balance-snapshot")
            cursor.execute(
                """SELECT symbol FROM bitget_margin_reservations
                WHERE exchange = %s AND (environment = %s OR environment IS NULL)
                  AND state IN ('reserved', 'unknown', 'consumed')""",
                (exchange, environment),
            )
            owners = [_first(row, None) for row in cursor.fetchall()]
            if None in owners:
                connection.commit()
                return BalanceAdmission.rejected("legacy-unowned-reservation")
            if symbol in owners or symbol in provider_active_symbols:
                connection.commit()
                return BalanceAdmission.rejected("symbol-already-owned")
            # Provider exposure includes manual/hedged positions. Count it as supplied,
            # then only unique in-flight symbols not already reflected at the provider.
            slots = len(provider_active_symbols) + len(set(owners) - set(provider_active_symbols))
            if slots >= max_positions:
                connection.commit()
                return BalanceAdmission.rejected("position-cap-exceeded")
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
                WHERE exchange = %s AND state IN ('reserved', 'unknown', 'consumed')
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
                     planned_margin_usdt, symbol, environment, state, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'reserved',
                        clock_timestamp() + %s::interval)
                RETURNING id
                """,
                (
                    reservation_id,
                    exchange,
                    dispatch_id,
                    client_order_id,
                    snapshot_id,
                    planned_margin_usdt,
                    symbol,
                    environment,
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

    def release_verified_close(
        self,
        *,
        environment: str,
        symbol: str,
        dispatch_id: UUID,
        entry_client_order_id: str,
        close_client_order_id: str,
        authenticated: bool,
        observed_at: datetime,
        flat_quantity: Decimal,
        fills: tuple[dict[str, Any], ...],
        max_snapshot_age: timedelta = timedelta(seconds=30),
    ) -> BalanceAdmission:
        """Release canonical consumed ownership after authenticated fills AND flat.

        Caller must attest a fresh authenticated full-symbol read (both hold sides),
        and resolve the close to this entry dispatch, not merely match a symbol.
        Fills must already be durably reconciled; this API never writes economics.
        No existing operator/monitor caller currently supplies this evidence contract.
        """
        if (
            authenticated is not True
            or environment not in {"DEMO", "LIVE"}
            or not symbol
            or symbol != symbol.strip().upper()
            or not isinstance(flat_quantity, Decimal)
            or not flat_quantity.is_finite()
            or flat_quantity != 0
            or not fills
            or observed_at.tzinfo is None
            or observed_at.utcoffset() is None
            or max_snapshot_age <= timedelta(0)
        ):
            return BalanceAdmission.rejected("unverified-close-evidence")
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('bitget'))")
            cursor.execute("SELECT clock_timestamp()")
            now = _first(cursor.fetchone(), None)
            if now - observed_at > max_snapshot_age or observed_at - now > timedelta(seconds=1):
                connection.commit()
                return BalanceAdmission.rejected("stale-or-future-close-evidence")
            cursor.execute(
                """SELECT id, state, created_at, resolution_reason FROM bitget_margin_reservations
                WHERE exchange = 'bitget' AND environment = %s AND symbol = %s
                  AND dispatch_id = %s AND client_order_id = %s FOR UPDATE""",
                (environment, symbol, dispatch_id, entry_client_order_id),
            )
            rows = cursor.fetchall()
            if len(rows) != 1:
                connection.commit()
                return BalanceAdmission.rejected("close-owner-not-found")
            row = rows[0]
            values = list(row.values()) if isinstance(row, dict) else list(row)
            reservation_id, state, created_at, reason = values

            def reject(reason: str) -> BalanceAdmission:
                connection.commit()
                return BalanceAdmission.rejected(reason)

            receipt = f"verified-close:{close_client_order_id}"
            if state != "consumed" and not (state == "released" and reason == receipt):
                return reject("close-owner-not-consumed")
            cursor.execute(
                """SELECT id FROM bitget_margin_reservations
                WHERE exchange = 'bitget' AND environment = %s AND symbol = %s
                  AND state IN ('reserved', 'unknown', 'consumed') AND id <> %s FOR UPDATE""",
                (environment, symbol, reservation_id),
            )
            if cursor.fetchall():
                return reject("ambiguous-close-ownership")
            cursor.execute(
                """SELECT client_order_id, symbol, side, role, state, requested_qty,
                    filled_qty, provider_order_id, margin_reservation_id, created_at
                FROM live_order_intents WHERE exchange = 'bitget'
                  AND client_order_id IN (%s, %s) FOR UPDATE""",
                (entry_client_order_id, close_client_order_id),
            )
            intents = [
                tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in cursor.fetchall()
            ]
            by_oid = {r[0]: r for r in intents}
            if len(by_oid) != 2:
                return reject("close-intent-not-bound")
            entry = by_oid[entry_client_order_id]
            close_intent = by_oid[close_client_order_id]
            cursor.execute(
                """SELECT reservation_id FROM bitget_verified_close_bindings
                WHERE exchange='bitget' AND close_client_order_id=%s FOR UPDATE""",
                (close_client_order_id,),
            )
            binding = cursor.fetchone()
            if binding is None or str(binding[0]) != str(reservation_id):
                return reject("close-intent-not-bound")
            if (
                entry[1] != symbol
                or entry[3] != "ENTRY"
                or entry[4] not in {"filled", "reconciled"}
                or str(entry[8]) != str(reservation_id)
                or close_intent[1] != symbol
                or close_intent[2] != ("SELL" if entry[2] == "BUY" else "BUY")
                or close_intent[3] not in {"CLOSE", "EMERGENCY_CLOSE", "SL", "TP"}
                or close_intent[4] not in {"filled", "reconciled"}
                or not close_intent[7]
                or close_intent[9] < entry[9]
                or close_intent[9] < created_at
                or close_intent[9] > observed_at
                or not all(
                    isinstance(q, Decimal) and q.is_finite() and q > 0
                    for q in (entry[6], close_intent[5], close_intent[6])
                )
                or close_intent[5] != close_intent[6]
                or close_intent[6] != entry[6]
            ):
                return reject("close-intent-not-bound")
            cursor.execute(
                """SELECT provider_fill_id, symbol, price, quantity, fee, realized_pnl, filled_at
                FROM fills WHERE exchange = 'bitget' AND client_order_id = %s FOR UPDATE""",
                (close_client_order_id,),
            )
            ledger = [
                tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in cursor.fetchall()
            ]
            evidence_ids = [f.get("provider_fill_id") for f in fills]
            if (
                len(ledger) != len(fills)
                or len(set(evidence_ids)) != len(fills)
                or {r[0] for r in ledger} != set(evidence_ids)
            ):
                return reject("unverified-close-fills")
            evidence_by_id = {f["provider_fill_id"]: f for f in fills}
            for fill_id, fill_symbol, price, quantity, fee, pnl, filled_at in ledger:
                supplied = evidence_by_id[fill_id]
                if (
                    not fill_id
                    or fill_id.startswith("status-derived:")
                    or fill_symbol != symbol
                    or filled_at < close_intent[9]
                    or filled_at > observed_at
                    or supplied.get("provider_order_id") != close_intent[7]
                    or supplied.get("symbol") != symbol
                    or supplied.get("side") != close_intent[2]
                    or supplied.get("trade_side") != "close"
                    or any(
                        not isinstance(supplied.get(k), Decimal)
                        or not supplied[k].is_finite()
                        or supplied[k] != v
                        or not v.is_finite()
                        for k, v in (
                            ("price", price),
                            ("quantity", quantity),
                            ("fee", fee),
                            ("realized_pnl", pnl),
                        )
                    )
                ):
                    return reject("unverified-close-fills")
            if sum((r[3] for r in ledger), Decimal(0)) != close_intent[6]:
                return reject("incomplete-close-fills")
            # Row/intent/fill locks can also wait after the initial clock check.
            # Recheck immediately before releasing capacity, never transaction time.
            cursor.execute("SELECT clock_timestamp()")
            now = _first(cursor.fetchone(), None)
            if now - observed_at > max_snapshot_age or observed_at - now > timedelta(seconds=1):
                return reject("stale-or-future-close-evidence")
            if state == "released":
                connection.commit()
                return BalanceAdmission(True, reservation_id=UUID(str(reservation_id)))
            cursor.execute(
                """UPDATE bitget_margin_reservations SET state = 'released',
                    resolved_at = clock_timestamp(), resolution_reason = %s
                WHERE id = %s AND state = 'consumed' RETURNING id""",
                (f"verified-close:{close_client_order_id}", reservation_id),
            )
            released = cursor.fetchone()
            connection.commit()
            if released is None:
                return BalanceAdmission.rejected("close-owner-not-consumed")
            return BalanceAdmission(True, reservation_id=UUID(str(reservation_id)))
        except Exception:
            connection.rollback()
            raise
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()

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
            "PARTIAL": "unknown",
            "REJECTED": "released",
            "CANCELLED": "released",
            "UNKNOWN": "unknown",
        }.get(outcome.upper())
        if state is None:
            raise ValueError("unknown margin reservation outcome")
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('bitget'))")
            cursor.execute(
                """
                UPDATE bitget_margin_reservations
                SET state = CASE
                        WHEN state = 'consumed' THEN state
                        WHEN %s IN ('consumed', 'unknown') AND %s THEN %s
                        WHEN state = 'released' THEN state
                        WHEN %s = 'released' AND has_exposure THEN 'consumed'
                        ELSE %s END,
                    has_exposure = has_exposure OR %s,
                    resolved_at = clock_timestamp(),
                    resolution_reason = %s
                WHERE id = %s
                """,
                (
                    state,
                    normalized in {"FILLED", "PARTIAL"},
                    state,
                    state,
                    state,
                    normalized in {"FILLED", "PARTIAL"},
                    normalized,
                    reservation_id,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def active_client_order_ids(self) -> list[tuple[UUID, str, bool]]:
        """Return commitments requiring GET-only startup reconciliation."""
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """SELECT id, client_order_id, expires_at <= CURRENT_TIMESTAMP
                FROM bitget_margin_reservations
                WHERE exchange = 'bitget' AND state IN ('reserved', 'unknown')
                ORDER BY created_at, id"""
            )
            rows = cursor.fetchall()
            result: list[tuple[UUID, str, bool]] = []
            for row in rows:
                values = list(row.values()) if isinstance(row, dict) else list(row)
                result.append((UUID(str(values[0])), str(values[1]), bool(values[2])))
            return result
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()

    def escalate_expired(self, reservation_id: UUID) -> None:
        """Fail closed after startup GET cannot terminalize an expired commitment."""
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """UPDATE bitget_margin_reservations
                SET state = 'unknown', resolved_at = COALESCE(resolved_at, CURRENT_TIMESTAMP),
                    resolution_reason = COALESCE(
                        resolution_reason, 'reservation-expired-unreconciled'
                    )
                WHERE id = %s AND state = 'reserved' AND expires_at <= CURRENT_TIMESTAMP""",
                (reservation_id,),
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
