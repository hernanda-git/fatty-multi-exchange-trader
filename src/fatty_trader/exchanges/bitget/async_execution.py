"""Async Bitget entry execution with intent-first, GET-only reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue
from fatty_trader.exchanges.bitget.client import BitgetUnknownResultError
from fatty_trader.exchanges.bitget.live import (
    LiveIntentRecord,
    LiveIntentStoreProtocol,
    LiveOrderStatus,
    classify_live_order,
    summarize_fills,
)
from fatty_trader.exchanges.bitget.reconciliation_live import confirm_native_protection
from fatty_trader.exchanges.bitget.validation import validate_order
from fatty_trader.execution.protection import ProtectionPlan, ProtectionReport, ProtectionState
from fatty_trader.storage.live_intents import build_emergency_close_intent


class AsyncBitgetExecutionClient(Protocol):
    async def place_entry_order(
        self, *, symbol: str, side: str, quantity: str, client_oid: str
    ) -> dict[str, Any]: ...

    async def get_order_detail(self, symbol: str, *, client_oid: str) -> Any: ...

    async def get_fills(self, symbol: str) -> Any: ...

    async def get_single_position(self, symbol: str) -> Any: ...

    async def get_pending_plan_orders(self, symbol: str) -> Any: ...

    async def place_position_tpsl(
        self,
        *,
        symbol: str,
        hold_side: str,
        quantity: str,
        stop_loss: str,
        stop_loss_execute_price: str,
        take_profit: str,
        take_profit_execute_price: str,
        stop_loss_client_oid: str,
        take_profit_client_oid: str,
    ) -> dict[str, Any]: ...

    async def place_market_close(
        self, *, symbol: str, side: str, quantity: str, client_oid: str
    ) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class AsyncExecutionResult:
    client_oid: str
    status: LiveOrderStatus
    filled_qty: Decimal
    avg_price: Decimal | None
    fee: Decimal
    provider_order_id: str | None
    provider_fill_ids: tuple[str, ...]


@dataclass(frozen=True)
class AsyncProtectionResult:
    state: ProtectionState
    observed_quantity: Decimal
    reason: str | None = None
    emergency_close_oid: str | None = None


class AsyncBitgetExecution:
    """Production async execution adapter; POST is followed only by read-back GETs."""

    def __init__(self, client: AsyncBitgetExecutionClient, venue: AsyncBitgetVenue) -> None:
        self._client = client
        self._venue = venue
        self._degraded = False

    @property
    def degraded(self) -> bool:
        """Whether an unsafe fill has halted this adapter from additional entries."""
        return self._degraded

    async def aclose(self) -> None:
        """Close the owned async transport exactly once through its client boundary."""
        await self._client.aclose()

    async def submit_entry(self, intent: LiveIntentRecord) -> AsyncExecutionResult:
        if self._degraded:
            raise RuntimeError("Bitget execution is degraded; additional dispatches are blocked")
        snapshot = await self._venue.preflight(intent.symbol)
        validate_order(
            intent.symbol,
            intent.side,
            snapshot.current_price,
            intent.requested_qty,
            snapshot.metadata,
        )
        try:
            submitted = await self._client.place_entry_order(
                symbol=intent.symbol,
                side=intent.side,
                quantity=str(intent.requested_qty),
                client_oid=intent.client_oid,
            )
        except (BitgetUnknownResultError, TimeoutError):
            return await self.reconcile_intent(intent)
        return await self.reconcile_intent(intent, submitted)

    async def reconcile_intent(
        self, intent: LiveIntentRecord, submitted: dict[str, Any] | None = None
    ) -> AsyncExecutionResult:
        detail = await self._client.get_order_detail(intent.symbol, client_oid=intent.client_oid)
        fills = await self._client.get_fills(intent.symbol)
        if not isinstance(detail, dict):
            raise ValueError("Bitget order detail response must be an object")
        if not isinstance(fills, list) or not all(isinstance(fill, dict) for fill in fills):
            raise ValueError("Bitget fills response must be a list of objects")
        typed_fills = [dict(fill) for fill in fills]
        filled_qty, avg_price, fee, fill_ids = summarize_fills(typed_fills)
        provider_order_id = detail.get("orderId")
        if provider_order_id is None and submitted is not None:
            provider_order_id = submitted.get("orderId")
        return AsyncExecutionResult(
            client_oid=intent.client_oid,
            status=classify_live_order(detail, typed_fills),
            filled_qty=filled_qty,
            avg_price=avg_price,
            fee=fee,
            provider_order_id=str(provider_order_id) if provider_order_id is not None else None,
            provider_fill_ids=fill_ids,
        )

    async def protect_filled_position(
        self,
        intent: LiveIntentRecord,
        plan: ProtectionPlan,
        store: LiveIntentStoreProtocol,
    ) -> AsyncProtectionResult:
        """Install and read back native protection before accepting a filled position.

        Any unconfirmed result latches the adapter degraded. Containment is intent-first
        and deterministic, so an unknown close POST is never retried blindly.
        """
        filled_quantity = intent.filled_qty
        if filled_quantity <= 0:
            self._degraded = True
            return AsyncProtectionResult(ProtectionState.FAILED, Decimal("0"), "no-filled-quantity")
        if plan.quantity != filled_quantity:
            self._degraded = True
            return await self._contain(
                intent,
                store,
                ProtectionReport(
                    ProtectionState.FAILED, filled_quantity, "protection-quantity-mismatch"
                ),
            )
        try:
            await self._client.place_position_tpsl(
                symbol=plan.symbol,
                hold_side="long" if plan.direction.value == "LONG" else "short",
                quantity=str(filled_quantity),
                stop_loss=str(plan.stop_loss),
                stop_loss_execute_price=str(plan.stop_loss),
                take_profit=str(plan.take_profits[0]),
                take_profit_execute_price=str(plan.take_profits[0]),
                stop_loss_client_oid=f"{intent.client_oid}-sl",
                take_profit_client_oid=f"{intent.client_oid}-tp",
            )
            report = await confirm_native_protection(
                lambda: self._client.get_single_position(plan.symbol),
                lambda: self._client.get_pending_plan_orders(plan.symbol),
                expected_quantity=filled_quantity,
            )
        except Exception:
            report = ProtectionReport(
                ProtectionState.FAILED, Decimal("0"), "protection-submit-failed"
            )
        if report.state is ProtectionState.VENUE_PROTECTED:
            return AsyncProtectionResult(report.state, report.observed_quantity, report.reason)
        self._degraded = True
        return await self._contain(intent, store, report)

    async def _contain(
        self,
        entry: LiveIntentRecord,
        store: LiveIntentStoreProtocol,
        report: ProtectionReport,
    ) -> AsyncProtectionResult:
        """Persist then submit at most one emergency close, never for an observed flat account."""
        if report.reason == "position-not-open":
            return AsyncProtectionResult(report.state, report.observed_quantity, report.reason)
        close_intent = build_emergency_close_intent(entry, entry.filled_qty)
        existing = store.get(close_intent.client_oid)
        if existing is not None:
            return AsyncProtectionResult(
                report.state, report.observed_quantity, report.reason, close_intent.client_oid
            )
        store.save(close_intent)
        try:
            submitted = await self._client.place_market_close(
                symbol=close_intent.symbol,
                side=close_intent.side,
                quantity=str(close_intent.requested_qty),
                client_oid=close_intent.client_oid,
            )
        except (BitgetUnknownResultError, TimeoutError):
            close_intent.state = "unknown"
            store.update(close_intent)
        else:
            provider_order_id = submitted.get("orderId")
            close_intent.provider_order_id = (
                str(provider_order_id) if provider_order_id is not None else None
            )
            close_intent.state = "submitted"
            store.update(close_intent)
        return AsyncProtectionResult(
            report.state, report.observed_quantity, report.reason, close_intent.client_oid
        )
