"""Dispatcher-to-Bitget execution adapter tests with fakes only; no network."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from fatty_trader.exchanges.bitget.async_execution import (
    AsyncExecutionResult,
    AsyncProtectionResult,
)
from fatty_trader.exchanges.bitget.live import (
    InMemoryLiveIntentStore,
    LiveIntentRecord,
    LiveIntentStoreProtocol,
    LiveOrderStatus,
)
from fatty_trader.execution.bitget_admission import BitgetEntrySubmission
from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
from fatty_trader.execution.bitget_dispatch_repository import BitgetDispatch
from fatty_trader.execution.protection import ProtectionPlan, ProtectionState


class Execution:
    def __init__(
        self,
        *,
        result: AsyncExecutionResult,
        protection: AsyncProtectionResult | None = None,
    ) -> None:
        self.result = result
        self.protection = protection
        self.submit_calls: list[str] = []
        self.reconcile_calls: list[str] = []
        self.protect_calls: list[str] = []

    async def submit_entry(self, intent: LiveIntentRecord) -> AsyncExecutionResult:
        self.submit_calls.append(intent.client_oid)
        return self.result

    async def reconcile_intent(self, intent: LiveIntentRecord) -> AsyncExecutionResult:
        self.reconcile_calls.append(intent.client_oid)
        return self.result

    async def protect_filled_position(
        self,
        intent: LiveIntentRecord,
        plan: ProtectionPlan,
        store: LiveIntentStoreProtocol,
    ) -> AsyncProtectionResult:
        self.protect_calls.append(intent.client_oid)
        assert plan.quantity == Decimal("0.002")
        assert store is not None
        assert self.protection is not None
        return self.protection


def _dispatch() -> BitgetDispatch:
    return BitgetDispatch(
        id=UUID("12345678-1234-5678-1234-567812345678"),
        state="QUEUED",
        claimed_by="worker",
        attempts=1,
        pair_token="BTCUSDT",
        direction="LONG",
        entry_price=Decimal("64000"),
        stop_loss=Decimal("63000"),
        take_profits=(Decimal("65000"),),
    )


def _submission(*, leverage: int = 20) -> BitgetEntrySubmission:
    return BitgetEntrySubmission(
        quantity=Decimal("0.002"),
        effective_leverage=leverage,
        planned_margin_usdt=Decimal("10"),
        planned_notional_usdt=Decimal("128"),
        margin_mode="ISOLATED",
        balance_snapshot_id=UUID("12345678-1234-5678-1234-567812345678"),
        margin_reservation_id=UUID("87654321-4321-8765-4321-876543218765"),
        observed_at=datetime(2026, 9, 23, tzinfo=UTC),
    )


def test_intent_copies_immutable_sizing_admission() -> None:
    intent = BitgetDispatchExecution._intent(_dispatch(), _submission())

    assert intent.requested_qty == Decimal("0.002")
    assert intent.planned_leverage == 20
    assert intent.planned_margin_usdt == Decimal("10")
    assert intent.margin_mode == "ISOLATED"
    assert intent.balance_snapshot_id == UUID("12345678-1234-5678-1234-567812345678")
    assert intent.margin_reservation_id == UUID("87654321-4321-8765-4321-876543218765")


def test_source_identity_makes_replayed_dispatches_share_client_oid() -> None:
    first = BitgetDispatchExecution._intent(
        BitgetDispatch(
            **{
                **_dispatch().__dict__,
                "id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                "source_channel_id": 7,
                "source_message_id": 999999999,
            }
        ),
        _submission(),
    )
    second = BitgetDispatchExecution._intent(
        BitgetDispatch(
            **{
                **_dispatch().__dict__,
                "id": UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                "source_channel_id": 7,
                "source_message_id": 999999999,
            }
        ),
        _submission(),
    )

    assert first.client_oid == second.client_oid


def _result(status: LiveOrderStatus = LiveOrderStatus.FILLED) -> AsyncExecutionResult:
    return AsyncExecutionResult(
        client_oid="live-bitget-BTCUSDT-1234567812345678",
        status=status,
        filled_qty=Decimal("0.002") if status is not LiveOrderStatus.ACCEPTED else Decimal("0"),
        avg_price=Decimal("64001") if status is not LiveOrderStatus.ACCEPTED else None,
        fee=Decimal("0.01"),
        provider_order_id="provider-order-1",
        provider_fill_ids=("fill-1",),
        provider_fills=(
            {
                "fillId": "fill-1",
                "size": "0.002",
                "price": "64001",
                "fee": "-0.01",
                "feeCoin": "USDT",
            },
        ),
    )


@pytest.mark.asyncio
async def test_persists_intent_then_posts_once_and_confirms_native_protection() -> None:
    store = InMemoryLiveIntentStore()
    execution = Execution(
        result=_result(),
        protection=AsyncProtectionResult(ProtectionState.VENUE_PROTECTED, Decimal("0.002")),
    )

    status = await BitgetDispatchExecution(execution, store).submit_entry(
        _dispatch(), _submission()
    )

    oid = "live-bitget-BTCUSDT-1234567812345678"
    assert status == "FILLED"
    assert execution.submit_calls == [oid]
    assert execution.reconcile_calls == []
    assert execution.protect_calls == [oid]
    stored = store.get(oid)
    assert stored is not None
    assert stored.state == "filled"
    assert stored.provider_order_id == "provider-order-1"
    assert stored.filled_qty == Decimal("0.002")
    assert store.fills == [
        (
            oid,
            {
                "fillId": "fill-1",
                "size": "0.002",
                "price": "64001",
                "fee": "-0.01",
                "feeCoin": "USDT",
            },
        )
    ]


@pytest.mark.asyncio
async def test_entry_submission_uses_atomic_intent_claim() -> None:
    class ClaimOnlyStore(InMemoryLiveIntentStore):
        def save(self, record: LiveIntentRecord) -> None:
            raise AssertionError("entry submission must use atomic claim")

    store = ClaimOnlyStore()
    execution = Execution(
        result=_result(),
        protection=AsyncProtectionResult(ProtectionState.VENUE_PROTECTED, Decimal("0.002")),
    )

    status = await BitgetDispatchExecution(execution, store).submit_entry(
        _dispatch(), _submission()
    )

    assert status == "FILLED"
    assert execution.submit_calls == ["live-bitget-BTCUSDT-1234567812345678"]


@pytest.mark.asyncio
async def test_existing_durable_intent_uses_get_readback_without_a_second_post() -> None:
    store = InMemoryLiveIntentStore()
    oid = "live-bitget-BTCUSDT-1234567812345678"
    store.save(
        LiveIntentRecord(
            exchange="bitget",
            client_oid=oid,
            symbol="BTCUSDT",
            side="BUY",
            requested_qty=Decimal("0.002"),
        )
    )
    execution = Execution(
        result=_result(),
        protection=AsyncProtectionResult(ProtectionState.VENUE_PROTECTED, Decimal("0.002")),
    )

    status = await BitgetDispatchExecution(execution, store).submit_entry(
        _dispatch(), _submission()
    )

    assert status == "FILLED"
    assert execution.submit_calls == []
    assert execution.reconcile_calls == [oid]
    assert execution.protect_calls == [oid]


@pytest.mark.asyncio
async def test_replayed_fill_runs_post_fill_observation_before_reporting_success() -> None:
    class ReplayExecution(Execution):
        def __init__(self) -> None:
            super().__init__(
                result=_result(),
                protection=AsyncProtectionResult(ProtectionState.VENUE_PROTECTED, Decimal("0.002")),
            )
            self.post_fill_calls: list[str] = []

        async def reconcile_post_fill(
            self, intent: LiveIntentRecord, result: AsyncExecutionResult
        ) -> None:
            self.post_fill_calls.append(intent.client_oid)

    store = InMemoryLiveIntentStore()
    oid = "live-bitget-BTCUSDT-1234567812345678"
    store.save(LiveIntentRecord("bitget", oid, "BTCUSDT", "BUY", requested_qty=Decimal("0.002")))
    execution = ReplayExecution()

    status = await BitgetDispatchExecution(execution, store).submit_entry(
        _dispatch(), _submission()
    )

    assert status == "FILLED"
    assert execution.submit_calls == []
    assert execution.reconcile_calls == [oid]
    assert execution.post_fill_calls == [oid]


@pytest.mark.asyncio
async def test_unconfirmed_native_protection_returns_unknown_after_containment() -> None:
    store = InMemoryLiveIntentStore()
    execution = Execution(
        result=_result(),
        protection=AsyncProtectionResult(
            ProtectionState.DEGRADED,
            Decimal("0"),
            "native-protection-not-confirmed",
            "live-bitget-BTCUSDT-1234567812345678-emergency",
        ),
    )

    status = await BitgetDispatchExecution(execution, store).submit_entry(
        _dispatch(), _submission()
    )

    assert status == "UNKNOWN"
    assert execution.submit_calls == ["live-bitget-BTCUSDT-1234567812345678"]
    assert execution.protect_calls == ["live-bitget-BTCUSDT-1234567812345678"]


@pytest.mark.asyncio
async def test_fallback_registered_fill_is_not_reported_as_provider_unknown() -> None:
    store = InMemoryLiveIntentStore()
    execution = Execution(
        result=_result(),
        protection=AsyncProtectionResult(
            ProtectionState.DEGRADED,
            Decimal("0.002"),
            "native-protection-unsupported-fallback-registered",
        ),
    )

    status = await BitgetDispatchExecution(execution, store).submit_entry(
        _dispatch(), _submission()
    )

    assert status == "FILLED_FALLBACK"
    assert execution.submit_calls == ["live-bitget-BTCUSDT-1234567812345678"]


@pytest.mark.asyncio
async def test_acknowledged_entry_returns_without_a_protection_post() -> None:
    store = InMemoryLiveIntentStore()
    execution = Execution(result=_result(LiveOrderStatus.ACCEPTED))

    status = await BitgetDispatchExecution(execution, store).submit_entry(
        _dispatch(), _submission()
    )

    assert status == "ACKNOWLEDGED"
    assert execution.submit_calls == ["live-bitget-BTCUSDT-1234567812345678"]
    assert execution.protect_calls == []
