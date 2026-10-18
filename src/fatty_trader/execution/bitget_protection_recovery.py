"""GET-only restart evidence and account inventory admission boundary.

No placement, replay, close, kill-switch release or fallback registration lives here.
Flat historical fills are escalated: absence of a position is not a close fill.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from fatty_trader.exchanges.bitget.async_execution import AsyncProtectionResult
from fatty_trader.exchanges.bitget.live import LiveIntentRecord
from fatty_trader.exchanges.bitget.reconciliation_live import (
    NativeProtectionExpectation,
    confirm_native_protection,
)
from fatty_trader.execution.protection import ProtectionPlan, ProtectionState


def _position_quantity(payload: Any) -> Decimal | None:
    """Sum open quantity across a position payload; None when unreadable."""
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return None
    total = Decimal(0)
    for row in rows:
        if not isinstance(row, dict):
            return None
        raw = row.get("total", row.get("size"))
        if raw is None:
            return None
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not value.is_finite() or value < 0:
            return None
        total += value
    return total


def _is_empty(payload: Any) -> bool:
    """True only for a payload that proves the venue returned no rows."""
    rows = payload.get("data") if isinstance(payload, dict) else payload
    return isinstance(rows, list) and not rows


def _rows_for_symbol(payload: Any, symbol: str) -> list[dict[str, Any]] | None:
    """Rows matching ``symbol``; None when the payload cannot prove anything."""
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return None
    if any(not isinstance(row.get("symbol"), str) or not row.get("symbol") for row in rows):
        # A row without an identity cannot be scoped, so nothing is proven.
        return None
    wanted = symbol.upper()
    return [row for row in rows if str(row.get("symbol")).upper() == wanted]


class GetOnlyProtectionRecovery:
    def __init__(
        self,
        client: Any,
        repository: Any,
        *,
        environment: str,
        intent_store: Any = None,
        reservations: Any = None,
    ) -> None:
        self._client = client
        self._repository = repository
        self._environment = environment
        self._verified: dict[str, tuple[str, str, Decimal]] = {}
        self._intent_store = intent_store
        self._reservations = reservations

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

    async def retire_proved_empty_orphans(self) -> list[dict[str, Any]]:
        """GET-only: retire reservations the venue proves have no effect.

        A reservation that holds ownership with no entry intent used to block
        dispatch readiness until an operator ran the approved baseline script. When
        the symbol shows no position, no pending order and no pending plan, that
        commitment is provably moot, so it is resolved as released and announced.
        Anything unproven leaves the reservation exactly as it was.
        """
        if self._reservations is None:
            return []
        candidates = getattr(self._reservations, "orphan_candidates", None)
        if not callable(candidates):
            return []
        retired: list[dict[str, Any]] = []
        for row in candidates(self._environment):
            symbol = str(row.get("symbol") or "")
            if not symbol:
                # Without a symbol the venue cannot be asked; never guess one.
                continue
            try:
                positions = await self._client.get_single_position(symbol)
                quantity = _position_quantity(positions)
                pending = _rows_for_symbol(await self._client.get_pending_orders(), symbol)
                plans = await self._client.get_pending_plan_orders(symbol)
            except Exception:
                continue
            if quantity != 0 or pending is None or pending or not _is_empty(plans):
                continue
            self._reservations.resolve(row["id"], "REJECTED")
            alert = getattr(self._repository, "alert_orphan_retirement", None)
            if callable(alert):
                alert(
                    reservation_id=str(row["id"]),
                    symbol=symbol,
                    reason="recovery-proved-empty",
                )
            print(
                json.dumps(
                    {
                        "event": "orphan-reservation-retired",
                        "reservation_id": str(row["id"]),
                        "symbol": symbol,
                        "reason": "recovery-proved-empty",
                    }
                ),
                flush=True,
            )
            retired.append(
                {
                    "reservation_id": str(row["id"]),
                    "symbol": symbol,
                    "previous_state": str(row.get("state")),
                }
            )
        return retired

    async def settle_absent_protection(self) -> list[dict[str, Any]]:
        """Settle unknown non-entry intents whose order the venue provably no longer holds.

        A stopped-out trade leaves protection intents that no readback can resolve (no
        plan, no position) and that block both recovery and an approved baseline. When the
        symbol is flat, the venue lists no plan and no pending order, and the intent filled
        nothing, its absence is proof enough to cancel it — with one alert.
        """
        if self._intent_store is None:
            return []
        candidates = getattr(self._intent_store, "unknown_non_entry", None)
        settle = getattr(self._intent_store, "settle_absent", None)
        if not callable(candidates) or not callable(settle):
            return []
        settled: list[dict[str, Any]] = []
        for intent in candidates():
            if intent.filled_qty != 0:
                continue
            symbol = str(intent.symbol)
            try:
                positions = await self._client.get_single_position(symbol)
                quantity = _position_quantity(positions)
                plans = await self._client.get_pending_plan_orders(symbol)
                pending = _rows_for_symbol(await self._client.get_pending_orders(), symbol)
            except Exception:
                continue
            if quantity != 0 or pending is None or pending or not _is_empty(plans):
                continue
            if not settle(intent.client_oid):
                continue
            alert = getattr(self._repository, "alert_protection_settled", None)
            if callable(alert):
                alert(
                    client_oid=intent.client_oid,
                    symbol=symbol,
                    reason="protection-plan-absent-settled",
                )
            print(
                json.dumps(
                    {
                        "event": "protection-plan-absent-settled",
                        "intent": intent.client_oid,
                        "symbol": symbol,
                        "dispatch_id": str(intent.dispatch_id or ""),
                    }
                ),
                flush=True,
            )
            settled.append(
                {
                    "intent": intent.client_oid,
                    "symbol": symbol,
                    "dispatch_id": str(intent.dispatch_id or ""),
                }
            )
        return settled

    @staticmethod
    def _matches_pending_entry(intent: LiveIntentRecord, row: dict[str, Any]) -> bool:
        """Only an exact durable non-reduce-only LIMIT leg may remain working."""
        if (
            intent.role != "ENTRY"
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
