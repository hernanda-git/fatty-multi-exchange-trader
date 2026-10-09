from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from fatty_trader.storage.verified_closes import PostgresVerifiedCloseLifecycle
from uuid import NAMESPACE_URL, uuid5

from fatty_trader.exchanges.bitget.live import (
    LiveIntentRecord,
    LiveIntentStoreProtocol,
    normalize_fill,
)

# States in which the provider has confirmed a completed fill for the intent, so the
# ledger must carry fill evidence for it.
_FILLED_STATES = {"filled", "reconciled"}


class Cursor(Protocol):
    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> object: ...
    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


def insert_status_derived_fill(cursor: Cursor, record: LiveIntentRecord) -> None:
    """Persist one ledger row for a filled intent the provider reported without fills.

    Bitget can confirm a full fill on order status while returning an empty fill list,
    and fills are only ever written from that list — which left closed positions with no
    fill evidence and understated realized PnL (14 of 23 filled intents on 2026-09-27).
    When the intent is filled, carries a quantity and price, and has no fill row yet,
    record exactly what the order status reported. The provider_fill_id is explicitly
    synthetic so it can never be mistaken for a provider-issued id, and realized PnL
    stays zero rather than being invented.
    """
    if record.state not in _FILLED_STATES:
        return
    if record.filled_qty <= 0 or record.avg_price is None or record.avg_price <= 0:
        return
    provider_fill_id = f"status-derived:{record.provider_order_id or record.client_oid}"
    cursor.execute(
        """
        INSERT INTO fills
            (id, exchange, client_order_id, provider_fill_id, symbol, price, quantity,
             fee, fee_ccy, realized_pnl, filled_at)
        SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP
        WHERE NOT EXISTS (
            SELECT 1 FROM fills WHERE exchange = %s AND client_order_id = %s
        )
        ON CONFLICT (exchange, provider_fill_id) DO NOTHING
        """,
        (
            uuid5(NAMESPACE_URL, f"fatty-fill:{record.exchange}:{provider_fill_id}"),
            record.exchange,
            record.client_oid,
            provider_fill_id,
            record.symbol,
            record.avg_price,
            record.filled_qty,
            abs(record.fee),
            "USDT",
            Decimal("0"),
            record.exchange,
            record.client_oid,
        ),
    )


def insert_provider_fills(
    cursor: Cursor,
    record: LiveIntentRecord,
    fills: tuple[dict[str, Any], ...],
) -> None:
    """Persist provider fills without inventing IDs or prices."""
    for raw_fill in fills:
        fill = normalize_fill(raw_fill)
        provider_fill_id = fill.get("fillId", fill.get("tradeId", fill.get("id")))
        quantity = fill.get(
            "quantity", fill.get("size", fill.get("fillQty", fill.get("baseVolume")))
        )
        price = fill.get("price", fill.get("fillPrice", fill.get("priceAvg")))
        if provider_fill_id is None or quantity is None or price is None:
            continue
        try:
            quantity_value = Decimal(str(quantity))
            price_value = Decimal(str(price))
            if (
                not quantity_value.is_finite()
                or not price_value.is_finite()
                or quantity_value <= 0
                or price_value <= 0
                or str(provider_fill_id).startswith("status-derived:")
            ):
                continue
            fee = abs(Decimal(str(fill.get("fee", "0") or "0")))
            realized_pnl = Decimal(
                str(
                    fill.get("realizedPnl", fill.get("profit", fill.get("totalProfits", "0")))
                    or "0"
                )
            )
        except Exception:
            continue
        timestamp_ms = fill.get("cTime", fill.get("uTime"))
        cursor.execute(
            """
            INSERT INTO fills
                (id, exchange, client_order_id, provider_fill_id, symbol, price,
                 quantity, fee, fee_ccy, realized_pnl, filled_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    COALESCE(to_timestamp(%s / 1000.0), CURRENT_TIMESTAMP))
            ON CONFLICT (exchange, provider_fill_id) DO NOTHING
            """,
            (
                uuid5(NAMESPACE_URL, f"fatty-fill:{record.exchange}:{provider_fill_id}"),
                record.exchange,
                record.client_oid,
                str(provider_fill_id),
                record.symbol,
                price_value,
                quantity_value,
                fee,
                fill.get("feeCcy", fill.get("feeCoin", "USDT")),
                realized_pnl,
                timestamp_ms,
            ),
        )
        # Provider evidence supersedes the provisional aggregate in this same
        # transaction. Durable intent retains status quantity/price/fee evidence.
        cursor.execute(
            """DELETE FROM fills WHERE exchange = %s AND client_order_id = %s
                 AND provider_fill_id LIKE 'status-derived:%%'
                 AND EXISTS (SELECT 1 FROM fills WHERE exchange = %s
                     AND client_order_id = %s
                     AND provider_fill_id NOT LIKE 'status-derived:%%')""",
            (record.exchange, record.client_oid, record.exchange, record.client_oid),
        )


def build_emergency_close_intent(entry: LiveIntentRecord, quantity: Decimal) -> LiveIntentRecord:
    """Build the one stable reduce-only containment intent for an unsafe fill."""
    if quantity <= 0:
        raise ValueError("emergency close quantity must be positive")
    if entry.side not in {"BUY", "SELL"}:
        raise ValueError("emergency close entry side must be BUY or SELL")
    return LiveIntentRecord(
        exchange=entry.exchange,
        client_oid=f"{entry.client_oid}-emergency",
        symbol=entry.symbol,
        side="SELL" if entry.side == "BUY" else "BUY",
        role="EMERGENCY_CLOSE",
        state="requested",
        requested_qty=quantity,
        filled_qty=Decimal("0"),
    )


class PostgresLiveIntentStore(LiveIntentStoreProtocol):
    """PostgreSQL-backed live intent store with insert-before-submit semantics."""

    def __init__(self, connection_factory: Callable[[], Connection]) -> None:
        self._connection_factory = connection_factory

    @property
    def verified_close_lifecycle(self) -> PostgresVerifiedCloseLifecycle:
        """Evidence-only lifecycle shared by operator POST and monitor reconciliation."""
        from fatty_trader.storage.verified_closes import PostgresVerifiedCloseLifecycle

        return PostgresVerifiedCloseLifecycle(self._connection_factory, self)

    @staticmethod
    def _insert(cursor: Cursor, record: LiveIntentRecord) -> bool:
        cursor.execute(
            """
            INSERT INTO live_order_intents
                (id, exchange, client_order_id, provider_order_id, symbol, side,
                 role, state, requested_qty, filled_qty, leverage, margin_mode,
                 planned_margin_usdt, planned_notional_usdt, balance_snapshot_id,
                 margin_reservation_id, planned_stop_loss, planned_take_profits,
                 dispatch_id, entry_leg, order_type, limit_price, active_stop_loss_price,
                 stop_mutation_root_oid, stop_mutation_take_profit_id, protection_root_oid)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,
                    %s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (exchange, client_order_id) DO NOTHING
            RETURNING client_order_id
            """,
            (
                uuid5(NAMESPACE_URL, f"fatty-live:{record.exchange}:{record.client_oid}"),
                record.exchange,
                record.client_oid,
                record.provider_order_id,
                record.symbol,
                record.side,
                record.role,
                record.state,
                record.requested_qty,
                record.filled_qty,
                record.planned_leverage,
                record.margin_mode,
                record.planned_margin_usdt,
                record.planned_notional_usdt,
                record.balance_snapshot_id,
                record.margin_reservation_id,
                record.planned_stop_loss,
                json.dumps([str(p) for p in record.planned_take_profits])
                if record.planned_take_profits is not None
                else None,
                record.dispatch_id,
                record.entry_leg,
                record.order_type,
                record.limit_price,
                record.active_stop_loss_price,
                record.stop_mutation_root_oid,
                record.stop_mutation_take_profit_id,
                record.protection_root_oid,
            ),
        )
        return cursor.fetchone() is not None

    def update_active_stop(
        self, symbol: str, entry_root_oid: str, stop: Decimal, management_id: str
    ) -> None:
        if not stop.is_finite() or stop <= 0:
            raise ValueError("active stop must be finite positive")
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """SELECT e.margin_reservation_id FROM live_order_intents e
                JOIN live_order_intents m ON m.exchange=e.exchange AND m.client_order_id=%s
                WHERE e.exchange='bitget' AND e.client_order_id=%s AND e.symbol=%s
                  AND e.role='ENTRY' AND e.entry_leg IS DISTINCT FROM 'limit'
                  AND m.role='SL' AND m.symbol=e.symbol
                  AND m.stop_mutation_root_oid=e.client_order_id
                  AND m.planned_stop_loss=%s
                  AND m.state IN ('requested','unknown','acknowledged','reconciled')
                FOR UPDATE OF e,m""",
                (management_id, entry_root_oid, symbol, stop),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("active stop management ownership mismatch")
            cursor.execute(
                """UPDATE live_order_intents SET active_stop_loss_price=%s,
                    updated_at=clock_timestamp()
                WHERE exchange='bitget' AND role='ENTRY' AND symbol=%s
                  AND margin_reservation_id=%s
                  AND client_order_id IN (%s,%s)""",
                (stop, symbol, row[0], entry_root_oid, entry_root_oid + "-limit"),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def remaining_owned_quantity(self, entry_root_oid: str) -> Decimal:
        """Subtract only real fills of pre-bound owned exits; never edit entry fills."""
        root = self.get(entry_root_oid)
        if root is None or root.role != "ENTRY" or root.entry_leg == "limit":
            raise ValueError("remaining quantity requires canonical entry root")
        child = self.get(entry_root_oid + "-limit")
        total = root.filled_qty + (child.filled_qty if child is not None else Decimal("0"))
        connection = self._connection_factory()
        cursor = connection.cursor()
        cursor.execute(
            """SELECT c.client_order_id,c.symbol,c.side,c.role,c.provider_order_id,
                      c.filled_qty,c.provider_fill_ids,
                      COALESCE(sum(f.quantity),0),count(f.provider_fill_id)
            FROM bitget_verified_close_bindings b
            JOIN live_order_intents c ON c.exchange=b.exchange
                AND c.client_order_id=b.close_client_order_id
            LEFT JOIN fills f ON f.exchange=c.exchange AND f.client_order_id=c.client_order_id
                AND f.provider_fill_id NOT LIKE 'status-derived:%%'
                AND c.provider_fill_ids ? f.provider_fill_id
            WHERE b.reservation_id=%s
            GROUP BY c.client_order_id,c.symbol,c.side,c.role,c.provider_order_id,
                     c.filled_qty,c.provider_fill_ids""",
            (root.margin_reservation_id,),
        )
        closed = Decimal("0")
        while (row := cursor.fetchone()) is not None:
            if row[5] == 0:
                continue
            ids = json.loads(row[6]) if isinstance(row[6], str) else row[6]
            if (
                row[1] != root.symbol
                or row[2] != ("SELL" if root.side == "BUY" else "BUY")
                or row[3] not in {"CLOSE", "EMERGENCY_CLOSE", "SL", "TP"}
                or not row[4]
                or row[7] != row[5]
                or row[8] != len(ids)
                or any(str(fid).startswith("status-derived:") for fid in ids)
            ):
                raise ValueError("bound close lacks exact real provider fill evidence")
            closed += row[7]
        if closed > total:
            raise ValueError("bound closes exceed owned entry fills")
        return total - closed

    def claim_protection(self, records: tuple[LiveIntentRecord, ...]) -> bool:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            won = [self._insert(cursor, record) for record in records]
            if not all(won):
                connection.rollback()
                return False
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise

    def protection_intents(self, root_oid: str) -> tuple[LiveIntentRecord, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        cursor.execute(
            """SELECT client_order_id FROM live_order_intents WHERE exchange='bitget'
                AND role IN ('SL','TP') AND protection_root_oid=%s
            ORDER BY client_order_id""",
            (root_oid,),
        )
        ids = []
        while (row := cursor.fetchone()) is not None:
            ids.append(row[0])
        records = [self.get(oid) for oid in ids]
        return tuple(record for record in records if record is not None)

    def bind_native_close(
        self, root_oid: str, close_oid: str, plan_oid: str, executed_order_id: str
    ) -> None:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """SELECT e.margin_reservation_id FROM live_order_intents e
                JOIN live_order_intents p ON p.exchange=e.exchange AND p.client_order_id=%s
                JOIN live_order_intents c ON c.exchange=e.exchange AND c.client_order_id=%s
                WHERE e.exchange='bitget' AND e.role='ENTRY' AND e.client_order_id=%s
                  AND p.protection_root_oid=e.client_order_id AND p.role IN ('SL','TP')
                  AND p.provider_order_id IS NOT NULL AND p.symbol=e.symbol
                  AND c.role='CLOSE' AND c.symbol=e.symbol AND c.side<>e.side
                  AND c.provider_order_id=%s AND c.filled_qty>0
                  AND c.provider_fill_ids <> '[]'::jsonb
                  AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(c.provider_fill_ids) fid
                                  WHERE fid LIKE 'status-derived:%%')
                  AND c.filled_qty=(SELECT COALESCE(sum(f.quantity),0) FROM fills f
                      WHERE f.exchange=c.exchange AND f.client_order_id=c.client_order_id
                        AND c.provider_fill_ids ? f.provider_fill_id)
                FOR UPDATE OF e,p,c""",
                (plan_oid, close_oid, root_oid, executed_order_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("native close ownership or real fill evidence mismatch")
            cursor.execute(
                """INSERT INTO bitget_verified_close_bindings(close_client_order_id,reservation_id)
                VALUES (%s,%s) ON CONFLICT (close_client_order_id) DO NOTHING""",
                (close_oid, row[0]),
            )
            cursor.execute(
                "SELECT reservation_id FROM bitget_verified_close_bindings "
                "WHERE close_client_order_id=%s",
                (close_oid,),
            )
            if cursor.fetchone()[0] != row[0]:
                raise ValueError("native close already bound to another owner")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def claim(self, record: LiveIntentRecord) -> bool:
        connection = self._connection_factory()
        try:
            claimed = self._insert(connection.cursor(), record)
            connection.commit()
            return claimed
        except Exception:
            connection.rollback()
            raise

    def claim_split(self, market: LiveIntentRecord, limit: LiveIntentRecord) -> bool:
        """Both legs become durable together; a replay never recreates a child."""
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            claimed = self._insert(cursor, market)
            if claimed and not self._insert(cursor, limit):
                raise ValueError("split child exists without root")
            connection.commit()
            return claimed
        except Exception:
            connection.rollback()
            raise

    def claim_entry_post(self, client_oid: str) -> bool:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """UPDATE live_order_intents SET state='requested', updated_at=clock_timestamp()
                WHERE exchange='bitget' AND client_order_id=%s AND role='ENTRY'
                  AND entry_leg='limit' AND state='staged' AND cancel_requested_by IS NULL
                RETURNING client_order_id""",
                (client_oid,),
            )
            won = cursor.fetchone() is not None
            connection.commit()
            return won
        except Exception:
            connection.rollback()
            raise

    def claim_cancel(self, client_oid: str, management_id: Any) -> bool:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """UPDATE live_order_intents
                SET cancel_requested_by=%s, provider_terminal=(state='staged'),
                    state=CASE WHEN state='staged' THEN 'cancelled' ELSE state END,
                    updated_at=clock_timestamp()
                WHERE exchange='bitget' AND client_order_id=%s AND role='ENTRY'
                  AND entry_leg='limit' AND order_type='limit' AND dispatch_id IS NOT NULL
                  AND cancel_requested_by IS NULL AND NOT provider_terminal
                RETURNING client_order_id""",
                (management_id, client_oid),
            )
            won = cursor.fetchone() is not None
            connection.commit()
            return won
        except Exception:
            connection.rollback()
            raise

    def claim_cancel_post(self, client_oid: str) -> bool:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """UPDATE live_order_intents SET cancel_post_claimed=TRUE,
                    updated_at=clock_timestamp()
                WHERE exchange='bitget' AND client_order_id=%s AND role='ENTRY'
                  AND entry_leg='limit' AND cancel_requested_by IS NOT NULL
                  AND NOT cancel_post_claimed AND NOT provider_terminal
                RETURNING client_order_id""",
                (client_oid,),
            )
            won = cursor.fetchone() is not None
            connection.commit()
            return won
        except Exception:
            connection.rollback()
            raise

    def pending_entries(
        self, symbol: str | None = None, management_id: Any = None
    ) -> tuple[LiveIntentRecord, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        cursor.execute(
            """SELECT client_order_id FROM live_order_intents
            WHERE exchange='bitget' AND role='ENTRY' AND entry_leg='limit'
              AND (NOT provider_terminal OR cancel_requested_by = %s::uuid)
              AND (%s::text IS NULL OR symbol = %s::text)
            ORDER BY client_order_id""",
            (management_id, symbol, symbol),
        )
        ids = []
        while (row := cursor.fetchone()) is not None:
            ids.append(row[0])
        loaded = [self.get(oid) for oid in ids]
        if any(record is None for record in loaded):
            raise ValueError("pending entry disappeared")
        return tuple(record for record in loaded if record is not None)

    def record_provider_event(self, observation: Any, client_oid: str) -> None:
        """Persist provider source classification without duplicating a fill event."""
        exchange = str(observation.exchange)
        provider_fill_id = str(observation.provider_fill_id)
        payload = observation.payload
        observed_at = payload.get("fillTime", payload.get("uTime", payload.get("cTime")))
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO provider_reconciliation_events
                    (id, exchange, provider_order_id, provider_fill_id, client_order_id,
                     symbol, side, source, quantity, price, fee, realized_pnl, state, observed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'reconciled',
                        COALESCE(to_timestamp(%s / 1000.0), CURRENT_TIMESTAMP))
                ON CONFLICT (exchange, provider_fill_id) DO NOTHING
                """,
                (
                    uuid5(NAMESPACE_URL, f"fatty-provider-event:{exchange}:{provider_fill_id}"),
                    exchange,
                    observation.provider_order_id,
                    provider_fill_id,
                    client_oid,
                    observation.symbol,
                    observation.side,
                    observation.source,
                    observation.quantity,
                    observation.price,
                    observation.fee,
                    observation.realized_pnl,
                    observed_at,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def save(self, record: LiveIntentRecord) -> None:
        self.claim(record)

    def get(self, client_oid: str) -> LiveIntentRecord | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT exchange, client_order_id, symbol, side, role, state,
                   requested_qty, filled_qty, filled_price, fee, provider_order_id,
                   provider_fill_ids, leverage, planned_margin_usdt, planned_notional_usdt,
                   margin_mode, balance_snapshot_id, margin_reservation_id,
                   planned_stop_loss, planned_take_profits, dispatch_id, entry_leg,
                   order_type, limit_price, cancel_requested_by, provider_terminal,
                   cancel_post_claimed, active_stop_loss_price, stop_mutation_root_oid,
                   stop_mutation_take_profit_id, protection_root_oid
            FROM live_order_intents
            WHERE client_order_id = %s
            """,
            (client_oid,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        values = list(row.values()) if isinstance(row, dict) else list(row)
        # Preserve compatibility with legacy row fakes and pre-admission snapshots;
        # production SELECTs include all admission columns.
        values.extend([None] * (31 - len(values)))
        raw_fill_ids = values[11] or []
        if isinstance(raw_fill_ids, str):
            raw_fill_ids = json.loads(raw_fill_ids)
        raw_targets = values[19]
        if isinstance(raw_targets, str):
            raw_targets = json.loads(raw_targets)
        return LiveIntentRecord(
            exchange=str(values[0]),
            client_oid=str(values[1]),
            symbol=str(values[2]),
            side=str(values[3]),
            role=str(values[4]),
            state=str(values[5]),
            requested_qty=Decimal(str(values[6])),
            filled_qty=Decimal(str(values[7])),
            avg_price=Decimal(str(values[8])) if values[8] is not None else None,
            fee=Decimal(str(values[9] or "0")),
            provider_order_id=str(values[10]) if values[10] is not None else None,
            provider_fill_ids=tuple(str(item) for item in raw_fill_ids),
            planned_leverage=int(values[12]) if values[12] is not None else None,
            planned_margin_usdt=Decimal(str(values[13])) if values[13] is not None else None,
            planned_notional_usdt=Decimal(str(values[14])) if values[14] is not None else None,
            margin_mode=str(values[15]) if values[15] is not None else None,
            balance_snapshot_id=values[16],
            margin_reservation_id=values[17],
            planned_stop_loss=Decimal(str(values[18])) if values[18] is not None else None,
            planned_take_profits=(
                tuple(Decimal(str(price)) for price in raw_targets)
                if raw_targets is not None
                else None
            ),
            dispatch_id=values[20],
            entry_leg=values[21],
            order_type=values[22] or "market",
            limit_price=Decimal(str(values[23])) if values[23] is not None else None,
            cancel_requested_by=values[24],
            provider_terminal=bool(values[25]),
            cancel_post_claimed=bool(values[26]),
            active_stop_loss_price=Decimal(str(values[27])) if values[27] is not None else None,
            stop_mutation_root_oid=values[28],
            stop_mutation_take_profit_id=values[29],
            protection_root_oid=values[30],
        )

    def update(self, record: LiveIntentRecord) -> None:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE live_order_intents
                SET provider_order_id = COALESCE(provider_order_id, %s),
                    state = %s, filled_qty = %s, filled_price = %s, fee = %s,
                    provider_fill_ids = %s::jsonb,
                    provider_terminal = provider_terminal OR %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE exchange = %s AND client_order_id = %s
                  AND (provider_order_id IS NULL OR provider_order_id = %s)
                RETURNING provider_order_id
                """,
                (
                    record.provider_order_id,
                    record.state,
                    record.filled_qty,
                    record.avg_price,
                    record.fee,
                    json.dumps(record.provider_fill_ids),
                    record.provider_terminal,
                    record.exchange,
                    record.client_oid,
                    record.provider_order_id,
                ),
            )
            if cursor.fetchone() is None:
                raise ValueError("live intent provider order id conflict or missing record")
            if record.provider_fills:
                insert_provider_fills(cursor, record, record.provider_fills)
            insert_status_derived_fill(cursor, record)
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def record_fills(self, record: LiveIntentRecord, fills: tuple[dict[str, Any], ...]) -> None:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT id FROM live_order_intents "
                "WHERE exchange = %s AND client_order_id = %s FOR UPDATE",
                (record.exchange, record.client_oid),
            )
            insert_provider_fills(cursor, record, fills)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
