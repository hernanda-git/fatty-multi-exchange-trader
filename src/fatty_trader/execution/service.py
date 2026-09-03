from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid5

from fatty_trader.domain.enums import DispatchState, Exchange
from fatty_trader.domain.models import CanonicalSignal, SizingPlan
from fatty_trader.domain.state_machines import transition_dispatch
from fatty_trader.storage.memory import Dispatch, InMemoryDispatchRepository


@dataclass(frozen=True)
class PaperOrderRequest:
    signal: CanonicalSignal
    exchange: Exchange
    sizing: SizingPlan

    @property
    def client_order_id(self) -> str:
        return (
            f"paper-{self.exchange.value}-{self.signal.source_message_id}-"
            f"{self.signal.source_revision[:16]}"
        )


@dataclass(frozen=True)
class PaperOrderResult:
    client_order_id: str
    venue_order_id: str
    filled_quantity: Decimal
    average_price: Decimal


class PaperExecutor(Protocol):
    def __call__(self, request: PaperOrderRequest) -> PaperOrderResult: ...


class DurableExecutionService:
    """Small durable-worker seam: claim, persist every transition, execute PAPER only."""

    def __init__(
        self, repository: InMemoryDispatchRepository, adapters: dict[Exchange, PaperExecutor]
    ) -> None:
        self._repository = repository
        self._adapters = adapters

    def execute_once(
        self,
        worker_id: str,
        *,
        now: datetime | None = None,
        sizing: SizingPlan | None = None,
    ) -> PaperOrderResult:
        pending = self._repository.pending_exchanges()
        missing = pending - self._adapters.keys()
        if missing:
            raise ValueError(f"missing PAPER adapter for {next(iter(missing)).value}")
        claimed = self._repository.claim(worker_id, now=now, lease_seconds=60)
        if claimed is None:
            raise LookupError("no dispatch available")
        if sizing is None:
            raise ValueError("sizing plan is required")
        try:
            self._move(claimed, DispatchState.PREFLIGHT)
            self._move(claimed, DispatchState.SIZED)
            self._move(claimed, DispatchState.VALIDATED)
            self._move(claimed, DispatchState.SUBMITTING)
            result = self._adapters[claimed.exchange](
                PaperOrderRequest(claimed.signal, claimed.exchange, sizing)
            )
            if result.client_order_id != PaperOrderRequest(
                claimed.signal, claimed.exchange, sizing
            ).client_order_id:
                raise ValueError("paper adapter returned mismatched client order id")
            self._move(claimed, DispatchState.ACKNOWLEDGED)
            self._move(claimed, DispatchState.FILLED)
            self._repository.release(claimed.id)
            return result
        except Exception:
            current = self._repository.get(claimed.id)
            if current.state is DispatchState.SUBMITTING:
                self._repository.set_state(claimed.id, DispatchState.UNKNOWN)
            elif current.state not in {DispatchState.UNKNOWN, DispatchState.FILLED}:
                self._repository.set_state(claimed.id, DispatchState.REJECTED)
            self._repository.release(claimed.id)
            raise

    def _move(self, dispatch: Dispatch, target: DispatchState) -> None:
        current = self._repository.get(dispatch.id).state
        self._repository.set_state(dispatch.id, transition_dispatch(current, target))


def stable_paper_order_id(dispatch_id: UUID) -> str:
    return f"paper-{uuid5(dispatch_id, 'entry').hex[:24]}"


def utc_now() -> datetime:
    return datetime.now(UTC)
