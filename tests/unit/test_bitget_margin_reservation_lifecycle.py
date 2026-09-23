from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from fatty_trader.exchanges.bitget.async_execution import AsyncExecutionResult
from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveOrderStatus
from fatty_trader.execution.bitget_admission import BitgetEntrySubmission
from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
from fatty_trader.execution.bitget_dispatch_repository import BitgetDispatch


class Execution:
    async def submit_entry(self, intent):
        return AsyncExecutionResult(
            intent.client_oid, LiveOrderStatus.REJECTED, Decimal("0"), None, Decimal("0"), None, ()
        )

    async def reconcile_intent(self, intent):
        return await self.submit_entry(intent)

    async def protect_filled_position(self, intent, plan, store):
        raise AssertionError("rejected entry must not install protection")


class Reservations:
    def __init__(self) -> None:
        self.outcomes: list[str] = []

    def resolve(self, reservation_id, outcome: str) -> None:
        self.outcomes.append(outcome)


def _dispatch() -> BitgetDispatch:
    return BitgetDispatch(
        UUID(int=3),
        "QUEUED",
        "worker",
        1,
        "BTCUSDT",
        "LONG",
        Decimal("64000"),
        Decimal("63000"),
        (Decimal("65000"),),
    )


def _submission() -> BitgetEntrySubmission:
    return BitgetEntrySubmission(
        Decimal("0.002"),
        20,
        Decimal("10"),
        Decimal("128"),
        "ISOLATED",
        UUID(int=1),
        UUID(int=2),
        datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_terminal_rejection_releases_durable_margin_reservation() -> None:
    reservations = Reservations()
    status = await BitgetDispatchExecution(
        Execution(), InMemoryLiveIntentStore(), reservation_repository=reservations
    ).submit_entry(_dispatch(), _submission())

    assert status == "REJECTED"
    assert reservations.outcomes == ["REJECTED"]
