from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from fatty_trader.exchanges.bitget.live import (
    LiveIntentRecord,
    LiveIntentStoreProtocol,
    normalize_fill,
)


class Cursor(Protocol):
    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> object: ...
    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


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
            if quantity_value <= 0 or price_value <= 0:
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

    def save(self, record: LiveIntentRecord) -> None:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO live_order_intents
                    (id, exchange, client_order_id, provider_order_id, symbol, side,
                     role, state, requested_qty, filled_qty)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (exchange, client_order_id) DO NOTHING
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
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def get(self, client_oid: str) -> LiveIntentRecord | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT exchange, client_order_id, symbol, side, role, state,
                   requested_qty, filled_qty, filled_price, fee, provider_order_id,
                   provider_fill_ids
            FROM live_order_intents
            WHERE client_order_id = %s
            """,
            (client_oid,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        values = list(row.values()) if isinstance(row, dict) else list(row)
        raw_fill_ids = values[11] or []
        if isinstance(raw_fill_ids, str):
            raw_fill_ids = json.loads(raw_fill_ids)
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
                    provider_fill_ids = %s::jsonb, updated_at = CURRENT_TIMESTAMP
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
                    record.exchange,
                    record.client_oid,
                    record.provider_order_id,
                ),
            )
            if cursor.fetchone() is None:
                raise ValueError("live intent provider order id conflict or missing record")
            if record.provider_fills:
                insert_provider_fills(cursor, record, record.provider_fills)
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def record_fills(self, record: LiveIntentRecord, fills: tuple[dict[str, Any], ...]) -> None:
        connection = self._connection_factory()
        try:
            insert_provider_fills(connection.cursor(), record, fills)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
