"""Dispatcher-to-Bitget execution adapter tests with fakes only; no network."""

from __future__ import annotations

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


def _result(status: LiveOrderStatus = LiveOrderStatus.FILLED) -> AsyncExecutionResult:
    return AsyncExecutionResult(
        client_oid="live-bitget-BTCUSDT-1234567812345678",
        status=status,
        filled_qty=Decimal("0.002") if status is not LiveOrderStatus.ACCEPTED else Decimal("0"),
        avg_price=Decimal("64001") if status is not LiveOrderStatus.ACCEPTED else None,
        fee=Decimal("0.01"),
        provider_order_id="provider-order-1",
        provider_fill_ids=("fill-1",),
    )


@pytest.mark.asyncio
async def test_persists_intent_then_posts_once_and_confirms_native_protection() -> None:
    store = InMemoryLiveIntentStore()
    execution = Execution(
        result=_result(),
        protection=AsyncProtectionResult(ProtectionState.VENUE_PROTECTED, Decimal("0.002")),
    )

    status = await BitgetDispatchExecution(execution, store).submit_entry(
        _dispatch(), Decimal("0.002")
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
        _dispatch(), Decimal("0.002")
    )

    assert status == "FILLED"
    assert execution.submit_calls == []
    assert execution.reconcile_calls == [oid]
    assert execution.protect_calls == [oid]


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
        _dispatch(), Decimal("0.002")
    )

    assert status == "UNKNOWN"
    assert execution.submit_calls == ["live-bitget-BTCUSDT-1234567812345678"]
    assert execution.protect_calls == ["live-bitget-BTCUSDT-1234567812345678"]


@pytest.mark.asyncio
async def test_acknowledged_entry_returns_without_a_protection_post() -> None:
    store = InMemoryLiveIntentStore()
    execution = Execution(result=_result(LiveOrderStatus.ACCEPTED))

    status = await BitgetDispatchExecution(execution, store).submit_entry(
        _dispatch(), Decimal("0.002")
    )

    assert status == "ACKNOWLEDGED"
    assert execution.submit_calls == ["live-bitget-BTCUSDT-1234567812345678"]
    assert execution.protect_calls == []
