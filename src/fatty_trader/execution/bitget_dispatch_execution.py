"""Durable dispatcher adapter for the async Bitget entry/protection workflow."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from fatty_trader.domain.enums import Direction, Exchange
from fatty_trader.exchanges.bitget.async_execution import (
    AsyncExecutionResult,
    AsyncProtectionResult,
)
from fatty_trader.exchanges.bitget.live import (
    LiveIntentRecord,
    LiveIntentStoreProtocol,
    LiveOrderStatus,
)
from fatty_trader.execution.bitget_dispatch_repository import BitgetDispatch
from fatty_trader.execution.protection import ProtectionPlan, ProtectionState


class AsyncDispatchExecution(Protocol):
    """The narrow async execution seam used by the durable dispatcher."""

    async def submit_entry(self, intent: LiveIntentRecord) -> AsyncExecutionResult: ...

    async def reconcile_intent(self, intent: LiveIntentRecord) -> AsyncExecutionResult: ...

    async def protect_filled_position(
        self,
        intent: LiveIntentRecord,
        plan: ProtectionPlan,
        store: LiveIntentStoreProtocol,
    ) -> AsyncProtectionResult: ...


class BitgetDispatchExecution:
    """Persist an intent, submit exactly once, read it back, then prove protection.

    A durable intent keyed by the dispatch UUID makes a restart GET-only.  The
    underlying execution client never retries an ambiguous POST. A filled or
    partial entry is reported as successful only after native protection is
    read back; otherwise containment is attempted by the execution client and
    this adapter returns ``UNKNOWN`` for operator reconciliation.
    """

    def __init__(self, execution: AsyncDispatchExecution, store: LiveIntentStoreProtocol) -> None:
        self._execution = execution
        self._store = store

    async def submit_entry(self, dispatch: BitgetDispatch, quantity: Decimal) -> str:
        intent = self._intent(dispatch, quantity)
        existing = self._store.get(intent.client_oid)
        if existing is None:
            self._store.save(intent)
            result = await self._execution.submit_entry(intent)
        else:
            intent = existing
            result = await self._execution.reconcile_intent(intent)
        self._persist_readback(intent, result)
        if result.status not in {LiveOrderStatus.FILLED, LiveOrderStatus.PARTIAL}:
            return _dispatcher_status(result.status)
        protection = await self._execution.protect_filled_position(
            intent, self._protection_plan(dispatch, result.filled_qty), self._store
        )
        if protection.state is not ProtectionState.VENUE_PROTECTED:
            return "UNKNOWN"
        return _dispatcher_status(result.status)

    @staticmethod
    def _intent(dispatch: BitgetDispatch, quantity: Decimal) -> LiveIntentRecord:
        if quantity <= 0:
            raise ValueError("dispatch quantity must be positive")
        side = "BUY" if dispatch.direction == Direction.LONG.value else "SELL"
        return LiveIntentRecord(
            exchange=Exchange.BITGET.value,
            client_oid=f"live-bitget-{dispatch.pair_token}-{dispatch.id.hex[:16]}",
            symbol=dispatch.pair_token,
            side=side,
            requested_qty=quantity,
        )

    @staticmethod
    def _protection_plan(dispatch: BitgetDispatch, filled_qty: Decimal) -> ProtectionPlan:
        return ProtectionPlan(
            exchange=Exchange.BITGET,
            symbol=dispatch.pair_token,
            direction=Direction(dispatch.direction),
            quantity=filled_qty,
            stop_loss=dispatch.stop_loss,
            take_profits=dispatch.take_profits,
        )

    def _persist_readback(self, intent: LiveIntentRecord, result: AsyncExecutionResult) -> None:
        if result.client_oid != intent.client_oid:
            raise ValueError("Bitget readback client OID does not match durable intent")
        intent.state = {
            LiveOrderStatus.ACCEPTED: "acknowledged",
            LiveOrderStatus.PARTIAL: "partially_filled",
            LiveOrderStatus.FILLED: "filled",
            LiveOrderStatus.REJECTED: "rejected",
            LiveOrderStatus.UNKNOWN: "unknown",
        }[result.status]
        intent.filled_qty = result.filled_qty
        intent.avg_price = result.avg_price
        intent.fee = result.fee
        intent.provider_order_id = result.provider_order_id
        intent.provider_fill_ids = result.provider_fill_ids
        self._store.update(intent)


def _dispatcher_status(status: LiveOrderStatus) -> str:
    return {
        LiveOrderStatus.ACCEPTED: "ACKNOWLEDGED",
        LiveOrderStatus.PARTIAL: "PARTIAL",
        LiveOrderStatus.FILLED: "FILLED",
        LiveOrderStatus.REJECTED: "REJECTED",
        LiveOrderStatus.UNKNOWN: "UNKNOWN",
    }[status]
