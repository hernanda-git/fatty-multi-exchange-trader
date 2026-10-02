from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from fatty_trader.execution.bitget_admission import BitgetEntrySubmission
from fatty_trader.execution.bitget_dispatch_repository import BitgetDispatch
from fatty_trader.execution.bitget_dispatcher import BitgetAdmission, BitgetDispatcher, DispatchGate


class Repository:
    def __init__(self, dispatch: BitgetDispatch) -> None:
        self.dispatch = dispatch
        self.transitions: list[tuple[str, str]] = []

    def claim(self, worker_id: str, lease_seconds: int) -> BitgetDispatch:
        return self.dispatch

    def transition(
        self,
        dispatch_id: object,
        *,
        expected_state: str,
        target_state: str,
        reason: str | None = None,
    ) -> None:
        self.transitions.append((expected_state, target_state))

    def alert(self, dispatch_id: object, reason: str) -> None:
        pass

    def reserve_canary_entry(self, dispatch_id: object, exchange: str, max_orders: int) -> bool:
        return True

    def release_canary_entry(self, dispatch_id: object, exchange: str) -> None:
        pass


@dataclass
class Execution:
    submission: BitgetEntrySubmission | None = None

    async def submit_entry(
        self, dispatch: BitgetDispatch, submission: BitgetEntrySubmission
    ) -> str:
        self.submission = submission
        return "REJECTED"


def _dispatch() -> BitgetDispatch:
    return BitgetDispatch(
        id=uuid4(),
        state="QUEUED",
        claimed_by="worker",
        attempts=1,
        pair_token="BTCUSDT",
        direction="LONG",
        entry_price=Decimal("64000"),
        stop_loss=Decimal("63000"),
        take_profits=(Decimal("65000"),),
    )


def _submission() -> BitgetEntrySubmission:
    return BitgetEntrySubmission(
        quantity=Decimal("0.004"),
        effective_leverage=20,
        planned_margin_usdt=Decimal("10"),
        planned_notional_usdt=Decimal("256"),
        margin_mode="ISOLATED",
        balance_snapshot_id=uuid4(),
        margin_reservation_id=uuid4(),
        observed_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_production_admission_is_passed_unchanged_to_entry_execution() -> None:
    dispatch = _dispatch()
    repository = Repository(dispatch)
    execution = Execution()
    submission = _submission()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=execution,
        preflight=lambda _: BitgetAdmission(submission=submission),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "rejected"
    assert execution.submission is submission
    assert repository.transitions == [
        ("QUEUED", "PREFLIGHT"),
        ("PREFLIGHT", "SIZED"),
        ("SIZED", "VALIDATED"),
        ("VALIDATED", "SUBMITTING"),
        ("SUBMITTING", "REJECTED"),
    ]
