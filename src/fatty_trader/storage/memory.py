from dataclasses import dataclass
from uuid import UUID, uuid5

from fatty_trader.domain.enums import DispatchState, Exchange
from fatty_trader.domain.models import CanonicalSignal


@dataclass(frozen=True)
class Dispatch:
    id: UUID
    signal: CanonicalSignal
    exchange: Exchange
    state: DispatchState


class InMemoryDispatchRepository:
    """Fake-only repository modelling DB fan-out uniqueness for local safety tests."""

    def __init__(self) -> None:
        self._items: dict[UUID, Dispatch] = {}

    @property
    def count(self) -> int:
        return len(self._items)

    def by_signal(self, signal: CanonicalSignal) -> tuple[Dispatch, ...]:
        return tuple(item for item in self._items.values() if item.signal == signal)

    def create(self, signal: CanonicalSignal, exchange: Exchange) -> Dispatch:
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

    def set_state(self, dispatch_id: UUID, state: DispatchState) -> None:
        current = self._items[dispatch_id]
        self._items[dispatch_id] = Dispatch(current.id, current.signal, current.exchange, state)
