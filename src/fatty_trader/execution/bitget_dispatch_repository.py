"""Durable PostgreSQL claim and transition boundary for Bitget dispatches."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
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
class BitgetDispatch:
    id: UUID
    state: str
    claimed_by: str
    attempts: int
    pair_token: str
    direction: str
    entry_price: Decimal
    stop_loss: Decimal
    take_profits: tuple[Decimal, ...]


_CLAIM_SQL = """
WITH next_dispatch AS (
    SELECT d.id
    FROM dispatches d
    WHERE d.exchange = 'bitget'
      AND d.state = 'QUEUED'
      AND (claimed_by IS NULL OR lease_until <= now())
    ORDER BY d.created_at, d.id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE dispatches d
SET claimed_by = %s,
    lease_until = now() + (%s * interval '1 second'),
    attempts = attempts + 1,
    updated_at = now()
FROM next_dispatch n, canonical_signals s
WHERE d.id = n.id AND s.id = d.source_id
RETURNING d.id, d.state, d.claimed_by, d.attempts, s.pair_token, s.direction,
          s.entry_price, s.stop_loss, s.take_profits;
"""


class PostgresBitgetDispatchRepository:
    """Lease-safe dispatch repository; every mutation is transactional and auditable."""

    def __init__(self, connection_factory: Callable[[], Connection]) -> None:
        self._connection_factory = connection_factory

    def claim(self, worker_id: str, lease_seconds: int) -> BitgetDispatch | None:
        if not worker_id:
            raise ValueError("worker_id is required")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(_CLAIM_SQL, (worker_id, lease_seconds))
            row = cursor.fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return _dispatch_from_row(row) if row is not None else None

    def transition(
        self,
        dispatch_id: UUID,
        *,
        expected_state: str,
        target_state: str,
        reason: str | None = None,
    ) -> None:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE dispatches
                SET state = %s, terminal_reason = %s, updated_at = now()
                WHERE id = %s AND exchange = 'bitget' AND state = %s
                RETURNING id
                """,
                (target_state, reason, dispatch_id, expected_state),
            )
            if cursor.fetchone() is None:
                raise ValueError("dispatch state changed concurrently or does not exist")
            cursor.execute(
                """
                INSERT INTO dispatch_transitions (id, dispatch_id, from_state, to_state, reason)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (uuid4(), dispatch_id, expected_state, target_state, reason),
            )
            cursor.execute(
                """
                INSERT INTO notifications_outbox (id, dedup_key, payload)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (dedup_key) DO NOTHING
                """,
                (
                    uuid4(),
                    f"dispatch-transition:{dispatch_id}:{expected_state}:{target_state}",
                    json.dumps(
                        {
                            "kind": "execution-event",
                            "dispatch_id": str(dispatch_id),
                            "from_state": expected_state,
                            "to_state": target_state,
                            "reason": reason or "none",
                        }
                    ),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def alert(self, dispatch_id: UUID, reason: str) -> None:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO notifications_outbox (id, dedup_key, payload)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (dedup_key) DO NOTHING
                """,
                (
                    uuid4(),
                    f"bitget-dispatch:{dispatch_id}:{reason}",
                    json.dumps(
                        {
                            "kind": "execution-alert",
                            "dispatch_id": str(dispatch_id),
                            "reason": reason,
                        }
                    ),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _dispatch_from_row(row: Any) -> BitgetDispatch:
    values = (
        row
        if isinstance(row, dict)
        else {
            "id": row[0],
            "state": row[1],
            "claimed_by": row[2],
            "attempts": row[3],
            "pair_token": row[4],
            "direction": row[5],
            "entry_price": row[6],
            "stop_loss": row[7],
            "take_profits": row[8],
        }
    )
    raw_take_profits = values["take_profits"] or []
    if isinstance(raw_take_profits, str):
        raw_take_profits = json.loads(raw_take_profits)
    return BitgetDispatch(
        id=UUID(str(values["id"])),
        state=str(values["state"]),
        claimed_by=str(values["claimed_by"]),
        attempts=int(values["attempts"]),
        pair_token=str(values["pair_token"]),
        direction=str(values["direction"]),
        entry_price=Decimal(str(values["entry_price"])),
        stop_loss=Decimal(str(values["stop_loss"])),
        take_profits=tuple(Decimal(str(value)) for value in raw_take_profits),
    )
