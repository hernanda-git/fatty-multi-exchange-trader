"""GET-only restart evidence and account inventory admission boundary.

No placement, replay, close, kill-switch release or fallback registration lives here.
Flat historical fills are escalated: absence of a position is not a close fill.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from fatty_trader.exchanges.bitget.async_execution import AsyncProtectionResult
from fatty_trader.exchanges.bitget.live import LiveIntentRecord, summarize_fills
from fatty_trader.exchanges.bitget.reconciliation_live import (
    NativeProtectionExpectation,
    canonical_provider_epoch,
    confirm_native_protection,
    owned_opening_fill_side,
)
from fatty_trader.execution.protection import ProtectionPlan, ProtectionState


class GetOnlyProtectionRecovery:
    def __init__(self, client: Any, repository: Any, *, environment: str) -> None:
        self._client = client
        self._repository = repository
        self._environment = environment
        self._verified: dict[str, tuple[str, str, Decimal, str]] = {}

    def begin(self) -> None:
        self._verified.clear()

    async def __call__(
        self, intent: LiveIntentRecord, plan: ProtectionPlan
    ) -> AsyncProtectionResult:
        self._verified.pop(intent.client_oid, None)
        # Filled status/position totals alone do not prove canonical ownership.
        if (
            not intent.provider_order_id
            or not intent.provider_fill_ids
            or any(str(fid).startswith("status-derived:") for fid in intent.provider_fill_ids)
        ):
            return AsyncProtectionResult(
                ProtectionState.DEGRADED, intent.filled_qty, "entry-fill-proof-missing"
            )
        if (
            plan.symbol != intent.symbol
            or plan.exchange.value != intent.exchange
            or intent.side != ("BUY" if plan.direction.value == "LONG" else "SELL")
        ):
            return AsyncProtectionResult(
                ProtectionState.DEGRADED, intent.filled_qty, "protection-entry-identity-mismatch"
            )
        if plan.quantity != intent.filled_qty:
            return AsyncProtectionResult(
                ProtectionState.DEGRADED, intent.filled_qty, "protection-quantity-mismatch"
            )
        epoch = _entry_position_epoch(intent)
        if epoch is None:
            return AsyncProtectionResult(
                ProtectionState.DEGRADED, intent.filled_qty, "entry-position-epoch-unproven"
            )
        expectation = NativeProtectionExpectation(
            symbol=plan.symbol,
            hold_side="buy" if plan.direction.value == "LONG" else "sell",
            quantity=plan.quantity,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profits[0] if plan.take_profits else None,
            stop_loss_client_oid=intent.client_oid + "-sl",
            take_profit_client_oid=intent.client_oid + "-tp" if plan.take_profits else None,
            provider_position_epoch=epoch,
        )
        report = await confirm_native_protection(
            lambda: self._client.get_single_position(plan.symbol),
            lambda: self._client.get_pending_plan_orders(plan.symbol),
            expectation=expectation,
        )
        reason = report.reason
        if reason == "position-not-open":
            reason = "owned-flat-close-fill-unproven"
        if report.state is ProtectionState.VENUE_PROTECTED:
            self._verified[intent.client_oid] = (
                plan.symbol,
                expectation.hold_side,
                plan.quantity,
                epoch,
            )
        return AsyncProtectionResult(report.state, report.observed_quantity, reason)

    async def inventory(self) -> tuple[str, ...]:
        issues = list(self._repository.inventory_issues(self._environment))
        positions = await self._client.get_all_positions()
        if not isinstance(positions, list):
            raise ValueError("recovery account positions invalid")
        observed: list[tuple[Any, str | None, Decimal, str | None]] = []
        verified: Sequence[tuple[Any, str | None, Decimal, str | None]] = list(
            self._verified.values()
        )
        sides: dict[object, str] = {"long": "buy", "short": "sell", "buy": "buy", "sell": "sell"}
        for row in positions:
            if not isinstance(row, dict):
                raise ValueError("recovery position row invalid")
            quantity = Decimal(str(row["total"]))
            if not quantity.is_finite() or quantity < 0:
                raise ValueError("recovery position quantity invalid")
            if quantity == 0:
                continue
            side = sides.get(row.get("holdSide"))
            key = (row.get("symbol"), side, quantity, canonical_provider_epoch(row.get("cTime")))
            observed.append(key)
            if verified.count(key) != 1:
                issues.append("provider-position-not-uniquely-owned-and-protected")
        for key in self._verified.values():
            if observed.count(key) != 1:
                issues.append("owned-position-inventory-mismatch")
        pending = await self._client.get_pending_orders()
        if not isinstance(pending, list) or not all(isinstance(row, dict) for row in pending):
            raise ValueError("recovery pending inventory invalid")
        # No open order is silently classified safe; ambiguous/manual entries block.
        if pending:
            issues.append("pending-orders-require-reconciliation")
        return tuple(sorted(set(issues)))


def _entry_position_epoch(intent: LiveIntentRecord) -> str | None:
    """Require a complete owned opening-fill ledger, not just opaque fill IDs."""
    fills = intent.provider_fills
    if not fills or intent.role != "ENTRY":
        return None
    epochs = []
    for fill in fills:
        epoch = canonical_provider_epoch(fill.get("cTime"))
        if (
            epoch is None
            or fill.get("orderId") != intent.provider_order_id
            or fill.get("clientOid") not in {None, intent.client_oid}
            or fill.get("symbol") != intent.symbol
            or not owned_opening_fill_side(fill, intent.side.lower())
        ):
            return None
        epochs.append(epoch)
    try:
        quantity, _, _, fill_ids = summarize_fills(fills)
    except (ArithmeticError, TypeError, ValueError):
        return None
    if (
        quantity != intent.filled_qty
        or len(fill_ids) != len(fills)
        or set(fill_ids) != set(intent.provider_fill_ids)
    ):
        return None
    return min(epochs, key=int)
