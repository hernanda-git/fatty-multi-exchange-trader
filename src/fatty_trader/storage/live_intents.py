from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from fatty_trader.exchanges.bitget.live import LiveIntentRecord, LiveIntentStoreProtocol


class Cursor(Protocol):
    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> object: ...
    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


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
            provider_fill_ids=tuple(str(item) for item in json.loads(values[11] or "[]")),
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
            connection.commit()
        except Exception:
            connection.rollback()
            raise
