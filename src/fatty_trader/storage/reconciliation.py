"""Durable, GET-only reconciliation state and shared Bitget kill switch."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from fatty_trader.exchanges.bitget.live import LiveIntentRecord


class Cursor(Protocol):
    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> object: ...
    def fetchall(self) -> Sequence[Any]: ...
    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class ReconciliationRepository(Protocol):
    def unknown_intents(self, exchange: str) -> list[LiveIntentRecord]: ...
    def update_intent(self, record: LiveIntentRecord) -> None: ...
    def expected_position_symbols(self, exchange: str) -> set[str]: ...
    def kill_switch_active(self, scope: str) -> bool: ...
    def latch_kill_switch(self, scope: str, reason: str) -> None: ...


class KillSwitch(Protocol):
    def is_active(self, scope: str) -> bool: ...


class InMemoryReconciliationRepository:
    """Small deterministic implementation used only by monitor unit tests."""

    def __init__(
        self,
        *,
        intents: list[LiveIntentRecord] | None = None,
        expected_symbols: set[str] | None = None,
    ) -> None:
        self.intents = [replace(intent) for intent in intents or []]
        self._expected_symbols = set(expected_symbols or set())
        self._kill_switches: set[str] = set()
        self._alert_keys: set[tuple[str, str]] = set()
        self.alerts: list[str] = []

    def unknown_intents(self, exchange: str) -> list[LiveIntentRecord]:
        return [
            replace(intent)
            for intent in self.intents
            if intent.exchange == exchange and intent.state == "unknown"
        ]

    def update_intent(self, record: LiveIntentRecord) -> None:
        for index, current in enumerate(self.intents):
            if current.exchange == record.exchange and current.client_oid == record.client_oid:
                self.intents[index] = replace(record)
                return
        raise LookupError(f"unknown live intent: {record.client_oid}")

    def expected_position_symbols(self, exchange: str) -> set[str]:
        return set(self._expected_symbols) | {
            intent.symbol
            for intent in self.intents
            if intent.exchange == exchange and intent.state not in {"rejected", "cancelled"}
        }

    def kill_switch_active(self, scope: str) -> bool:
        return scope in self._kill_switches

    def latch_kill_switch(self, scope: str, reason: str) -> None:
        self._kill_switches.add(scope)
        key = (scope, reason)
        if key not in self._alert_keys:
            self._alert_keys.add(key)
            self.alerts.append(reason)

    def is_active(self, scope: str) -> bool:
        return self.kill_switch_active(scope)


class PostgresReconciliationRepository:
    """Persistent kill switch and reconciliation boundary; alerts use stable dedup keys."""

    def __init__(self, connection_factory: Callable[[], Connection]) -> None:
        self._connection_factory = connection_factory

    def unknown_intents(self, exchange: str) -> list[LiveIntentRecord]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        cursor.execute(
            """SELECT exchange, client_order_id, symbol, side, role, state, requested_qty,
                      filled_qty, filled_price, fee, provider_order_id, provider_fill_ids
               FROM live_order_intents WHERE exchange = %s AND state = 'unknown'""",
            (exchange,),
        )
        return [_intent_from_row(row) for row in cursor.fetchall()]

    def expected_position_symbols(self, exchange: str) -> set[str]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        cursor.execute(
            """SELECT DISTINCT symbol FROM live_order_intents
               WHERE exchange = %s AND state NOT IN ('rejected', 'cancelled')""",
            (exchange,),
        )
        return {
            str(row["symbol"] if isinstance(row, dict) else row[0]) for row in cursor.fetchall()
        }

    def update_intent(self, record: LiveIntentRecord) -> None:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """UPDATE live_order_intents
                   SET provider_order_id = COALESCE(provider_order_id, %s), state = %s,
                       filled_qty = %s, filled_price = %s, fee = %s,
                       provider_fill_ids = %s::jsonb, updated_at = CURRENT_TIMESTAMP
                   WHERE exchange = %s AND client_order_id = %s""",
                (
                    record.provider_order_id,
                    record.state,
                    record.filled_qty,
                    record.avg_price,
                    record.fee,
                    json.dumps(record.provider_fill_ids),
                    record.exchange,
                    record.client_oid,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def kill_switch_active(self, scope: str) -> bool:
        connection = self._connection_factory()
        cursor = connection.cursor()
        cursor.execute("SELECT active FROM venue_kill_switches WHERE scope = %s", (scope,))
        row = cursor.fetchone()
        if row is None:
            return False
        return bool(row["active"] if isinstance(row, dict) else row[0])

    def is_active(self, scope: str) -> bool:
        return self.kill_switch_active(scope)

    def latch_kill_switch(self, scope: str, reason: str) -> None:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """INSERT INTO venue_kill_switches (scope, active, reason, latched_at, updated_at)
                   VALUES (%s, TRUE, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                   ON CONFLICT (scope) DO UPDATE SET active = TRUE, reason = EXCLUDED.reason,
                       updated_at = CURRENT_TIMESTAMP""",
                (scope, reason),
            )
            cursor.execute(
                """INSERT INTO notifications_outbox (id, dedup_key, payload)
                   VALUES (%s, %s, %s::jsonb) ON CONFLICT (dedup_key) DO NOTHING""",
                (
                    uuid4(),
                    f"kill-switch:{scope}:{reason}",
                    json.dumps({"scope": scope, "reason": reason}),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _intent_from_row(row: Any) -> LiveIntentRecord:
    values = list(row.values()) if isinstance(row, dict) else list(row)
    raw_fill_ids = values[11] or "[]"
    if not isinstance(raw_fill_ids, str):
        raw_fill_ids = json.dumps(raw_fill_ids)
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
        provider_fill_ids=tuple(str(value) for value in json.loads(raw_fill_ids)),
    )
