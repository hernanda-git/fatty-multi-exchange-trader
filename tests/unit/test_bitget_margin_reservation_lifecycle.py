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


@pytest.mark.asyncio
async def test_acknowledged_entry_retains_margin_reservation_for_later_reconciliation() -> None:
    class AcknowledgedExecution(Execution):
        async def submit_entry(self, intent):
            return AsyncExecutionResult(
                intent.client_oid,
                LiveOrderStatus.ACCEPTED,
                Decimal("0"),
                None,
                Decimal("0"),
                None,
                (),
            )

    reservations = Reservations()
    status = await BitgetDispatchExecution(
        AcknowledgedExecution(), InMemoryLiveIntentStore(), reservation_repository=reservations
    ).submit_entry(_dispatch(), _submission())

    assert status == "ACKNOWLEDGED"
    assert reservations.outcomes == ["ACKNOWLEDGED"]


@pytest.mark.asyncio
async def test_startup_sweep_reconciles_acknowledged_reservation_with_provider_evidence() -> None:
    class FilledExecution(Execution):
        async def reconcile_intent(self, intent):
            return AsyncExecutionResult(
                intent.client_oid,
                LiveOrderStatus.FILLED,
                Decimal("0.002"),
                Decimal("64000"),
                Decimal("0"),
                "provider-1",
                (),
            )

    store = InMemoryLiveIntentStore()
    intent = BitgetDispatchExecution._intent(_dispatch(), _submission())

    class ActiveReservations(Reservations):
        def active_client_order_ids(self):
            return [(UUID(int=2), intent.client_oid, False)]

    store.save(intent)
    reservations = ActiveReservations()

    reconciled = await BitgetDispatchExecution(
        FilledExecution(), store, reservation_repository=reservations
    ).reconcile_active_reservations()

    assert reconciled == 1
    assert reservations.outcomes == ["FILLED"]
    assert store.get(intent.client_oid).state == "filled"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_startup_sweep_escalates_expired_unresolved_reservation_to_unknown() -> None:
    class AcceptedExecution(Execution):
        async def reconcile_intent(self, intent):
            return AsyncExecutionResult(
                intent.client_oid,
                LiveOrderStatus.ACCEPTED,
                Decimal("0"),
                None,
                Decimal("0"),
                None,
                (),
            )

    class ExpiredReservations(Reservations):
        def __init__(self) -> None:
            super().__init__()
            self.escalated: list[UUID] = []

        def active_client_order_ids(self):
            return [(UUID(int=2), "live-bitget-BTCUSDT-1234567812345678", True)]

        def escalate_expired(self, reservation_id: UUID) -> None:
            self.escalated.append(reservation_id)

    store = InMemoryLiveIntentStore()
    intent = BitgetDispatchExecution._intent(_dispatch(), _submission())
    store.save(intent)
    reservations = ExpiredReservations()

    reconciled = await BitgetDispatchExecution(
        AcceptedExecution(), store, reservation_repository=reservations
    ).reconcile_active_reservations()

    assert reconciled == 0
    assert reservations.escalated == [UUID(int=2)]
