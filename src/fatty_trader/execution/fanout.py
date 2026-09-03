from fatty_trader.domain.enums import Exchange
from fatty_trader.domain.models import CanonicalSignal
from fatty_trader.storage.memory import Dispatch, InMemoryDispatchRepository


class FanoutPlanner:
    """Creates isolated venue dispatches from one immutable canonical signal."""

    def __init__(self, repository: InMemoryDispatchRepository) -> None:
        self._repository = repository

    def plan(self, signal: CanonicalSignal) -> tuple[Dispatch, ...]:
        return tuple(
            self._repository.create(signal, exchange)
            for exchange in (Exchange.BINANCE, Exchange.BITGET)
        )
