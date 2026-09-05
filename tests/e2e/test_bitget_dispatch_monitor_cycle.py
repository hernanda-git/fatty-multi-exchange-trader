from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import uuid4

import pytest

from fatty_trader.execution.bitget_dispatch_repository import BitgetDispatch
from fatty_trader.execution.bitget_dispatcher import BitgetDispatcher, DispatchGate


@dataclass
class DispatchRepository:
    dispatch: BitgetDispatch
    transitions: list[tuple[str, str, str | None]] = field(default_factory=list)

    def claim(self, worker_id: str, lease_seconds: int) -> BitgetDispatch | None:
        return self.dispatch

    def transition(
        self,
        dispatch_id: object,
        *,
        expected_state: str,
        target_state: str,
        reason: str | None = None,
    ) -> None:
        self.transitions.append((expected_state, target_state, reason))

    def alert(self, dispatch_id: object, reason: str) -> None:
        return None


class MutationCounter:
    calls = 0

    async def submit_entry(self, dispatch: BitgetDispatch, quantity: Decimal) -> str:
        self.calls += 1
        return "FILLED"


@pytest.mark.asyncio
async def test_closed_cutover_cycle_never_submits_a_provider_mutation() -> None:
    dispatch = BitgetDispatch(
        id=uuid4(),
        state="QUEUED",
        claimed_by="",
        attempts=0,
        pair_token="BTCUSDT",
        direction="LONG",
        entry_price=Decimal("60000"),
        stop_loss=Decimal("59000"),
        take_profits=(Decimal("62000"),),
    )
    repository = DispatchRepository(dispatch)
    venue = MutationCounter()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=False),
        execution=venue,
        preflight=lambda _: (_ for _ in ()).throw(AssertionError("preflight must remain closed")),
    )

    status = await dispatcher.run_once("e2e-observe-only", 30)

    assert status == "cutover-gated"
    assert venue.calls == 0
    assert repository.transitions == [("QUEUED", "REJECTED", "cutover-gated")]
