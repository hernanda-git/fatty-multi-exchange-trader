"""GET-only restart evidence and account inventory admission boundary.

No placement, replay, close, kill-switch release or fallback registration lives here.
Flat historical fills are escalated: absence of a position is not a close fill.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from fatty_trader.exchanges.bitget.async_execution import AsyncProtectionResult
from fatty_trader.exchanges.bitget.live import LiveIntentRecord
from fatty_trader.exchanges.bitget.reconciliation_live import (
    NativeProtectionExpectation,
    confirm_native_protection,
)
from fatty_trader.execution.protection import ProtectionPlan, ProtectionState


class GetOnlyProtectionRecovery:
    def __init__(
        self, client: Any, repository: Any, *, environment: str, intent_store: Any = None
    ) -> None:
        self._client = client
        self._repository = repository
        self._environment = environment
        self._verified: dict[str, tuple[str, str, Decimal]] = {}
        self._intent_store = intent_store

    def begin(self) -> None:
        self._verified.clear()

    async def verify_account_binding(self) -> None:
        """Prove baseline exclusions belong to the currently authenticated account."""
        binding = getattr(self._repository, "baseline_binding_issues", None)
        if not callable(binding):
            return
        client_dict = getattr(self._client, "__dict__", {})
        client_class = getattr(self._client, "__class__", None)
        has_get = "_get" in client_dict or (
            client_class is not None and hasattr(client_class, "_get")
        )
        if not has_get:
            return
        try:
            getter = getattr(self._client, "_get", None)
        except Exception:
            return
        if not callable(getter):
            return
        identity = await getter("/api/v2/spot/account/info")
        account_id = identity.get("userId") if isinstance(identity, dict) else None
        if (
            not isinstance(account_id, str)
            or not account_id.isascii()
            or not account_id.isdecimal()
            or int(account_id) <= 0
        ):
            raise ValueError("recovery authenticated account identity is invalid")
        if binding(account_id, self._environment):
            raise ValueError("baseline account/environment binding mismatch")

    async def __call__(
        self, intent: LiveIntentRecord, plan: ProtectionPlan
    ) -> AsyncProtectionResult:
        # Filled status/position totals alone do not prove canonical ownership.
        if (
            not intent.provider_order_id
            or not intent.provider_fill_ids
            or any(str(fid).startswith("status-derived:") for fid in intent.provider_fill_ids)
        ):
            return AsyncProtectionResult(
                ProtectionState.DEGRADED, intent.filled_qty, "entry-fill-proof-missing"
            )
        expectation = NativeProtectionExpectation(
            symbol=plan.symbol,
            hold_side="buy" if plan.direction.value == "LONG" else "sell",
            quantity=plan.quantity,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profits[0] if plan.take_profits else None,
            stop_loss_client_oid=intent.client_oid + "-sl",
            take_profit_client_oid=intent.client_oid + "-tp" if plan.take_profits else None,
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
            self._verified[intent.client_oid] = (plan.symbol, expectation.hold_side, plan.quantity)
        return AsyncProtectionResult(report.state, report.observed_quantity, reason)

    async def inventory(self) -> tuple[str, ...]:
        await self.verify_account_binding()
        issues = list(self._repository.inventory_issues(self._environment))
        positions = await self._client.get_all_positions()
        if not isinstance(positions, list):
            raise ValueError("recovery account positions invalid")
        observed: list[tuple[Any, str | None, Decimal]] = []
        verified: Sequence[tuple[Any, str | None, Decimal]] = list(self._verified.values())
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
            key = (row.get("symbol"), side, quantity)
            observed.append(key)
            if verified.count(key) != 1:
                issues.append("provider-position-not-uniquely-owned-and-protected")
        for key in self._verified.values():
            if observed.count(key) != 1:
                issues.append("owned-position-inventory-mismatch")
        pending = await self._client.get_pending_orders()
        if not isinstance(pending, list) or not all(isinstance(row, dict) for row in pending):
            raise ValueError("recovery pending inventory invalid")
        owned = self._intent_store.pending_entries() if self._intent_store is not None else ()
        matched: set[str] = set()
        for row in pending:
            candidates = [intent for intent in owned if intent.client_oid == row.get("clientOid")]
            if len(candidates) != 1 or not self._matches_pending_entry(candidates[0], row):
                issues.append("pending-orders-require-reconciliation")
                continue
            if candidates[0].client_oid in matched:
                issues.append("pending-entry-duplicate")
            matched.add(candidates[0].client_oid)
        if any(intent.client_oid not in matched for intent in owned):
            issues.append("owned-pending-entry-inventory-mismatch")
        return tuple(sorted(set(issues)))

    @staticmethod
    def _matches_pending_entry(intent: LiveIntentRecord, row: dict[str, Any]) -> bool:
        """Only an exact durable non-reduce-only LIMIT leg may remain working."""
        if (
            intent.role != "ENTRY"
            or getattr(intent, "entry_leg", None) != "limit"
            or getattr(intent, "order_type", None) != "limit"
            or not isinstance(row.get("symbol"), str)
            or row["symbol"].upper() != intent.symbol
            or not isinstance(row.get("side"), str)
            or row["side"].upper() != intent.side
            or row.get("orderType") != "limit"
            or row.get("reduceOnly") not in ("NO", "no", False)
            or row.get("state", row.get("status")) not in ("live", "partially_filled")
            or not row.get("orderId")
            or (intent.provider_order_id is not None and row["orderId"] != intent.provider_order_id)
        ):
            return False
        try:
            price = Decimal(str(row.get("price")))
            size = Decimal(str(row.get("size")))
            filled = Decimal(str(row.get("baseVolume", "0")))
        except Exception:
            return False
        return (
            price.is_finite()
            and size.is_finite()
            and filled.is_finite()
            and price == getattr(intent, "limit_price", None)
            and size == intent.requested_qty
            and Decimal("0") <= filled <= size
        )
