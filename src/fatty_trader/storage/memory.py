from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from fatty_trader.domain.enums import DispatchState, Exchange
from fatty_trader.domain.models import CanonicalSignal


@dataclass(frozen=True)
class Dispatch:
    id: UUID
    signal: CanonicalSignal
    exchange: Exchange
    state: DispatchState
    claimed_by: str | None = None
    lease_until: datetime | None = None
    attempts: int = 0
    terminal_reason: str | None = None


class InMemoryDispatchRepository:
    """Test repository with the same claim/lease invariants as the SQL queue."""

    def __init__(self) -> None:
        self._items: dict[UUID, Dispatch] = {}

    @property
    def count(self) -> int:
        return len(self._items)

    def by_signal(self, signal: CanonicalSignal) -> tuple[Dispatch, ...]:
        return tuple(item for item in self._items.values() if item.signal == signal)

    def pending_exchanges(self) -> set[Exchange]:
        return {
            item.exchange
            for item in self._items.values()
            if item.state in {DispatchState.QUEUED, DispatchState.RETRY_WAIT}
            and item.claimed_by is None
        }

    def create(self, signal: CanonicalSignal, exchange: Exchange) -> Dispatch:
        from uuid import uuid5

        dispatch_id = uuid5(
            UUID("1911cf64-6f1e-446a-a4bb-1b890de87519"),
            f"{signal.source_revision}:{signal.source_message_id}:{exchange.value}",
        )
        item = self._items.get(dispatch_id)
        if item is None:
            item = Dispatch(dispatch_id, signal, exchange, DispatchState.QUEUED)
            self._items[dispatch_id] = item
        return item

    def get(self, dispatch_id: UUID) -> Dispatch:
        return self._items[dispatch_id]

    def claim(
        self, worker_id: str, *, now: datetime | None = None, lease_seconds: int = 60
    ) -> Dispatch | None:
        if not worker_id or lease_seconds <= 0:
            raise ValueError("worker_id and positive lease_seconds are required")
        current_time = now or datetime.now().astimezone()
        for item in self._items.values():
            lease_expired = item.lease_until is not None and item.lease_until <= current_time
            eligible = item.state in {DispatchState.QUEUED, DispatchState.RETRY_WAIT}
            if eligible and (item.claimed_by is None or lease_expired):
                claimed = Dispatch(
                    item.id, item.signal, item.exchange, item.state, worker_id,
                    current_time + timedelta(seconds=lease_seconds),
                    item.attempts + 1, item.terminal_reason,
                )
                self._items[item.id] = claimed
                return claimed
        return None

    def release(self, dispatch_id: UUID) -> None:
        item = self._items[dispatch_id]
        self._items[dispatch_id] = Dispatch(
            item.id,
            item.signal,
            item.exchange,
            item.state,
            None,
            None,
            item.attempts,
            item.terminal_reason,
        )

    def set_state(self, dispatch_id: UUID, state: DispatchState) -> None:
        item = self._items[dispatch_id]
        self._items[dispatch_id] = Dispatch(
            item.id, item.signal, item.exchange, state, item.claimed_by, item.lease_until,
            item.attempts, item.terminal_reason
        )
