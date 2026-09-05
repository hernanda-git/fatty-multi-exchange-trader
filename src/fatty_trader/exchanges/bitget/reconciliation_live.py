from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from fatty_trader.exchanges.bitget.read_model import BitgetPositionState
from fatty_trader.execution.protection import (
    ProtectionReport,
    ProtectionState,
    protection_is_confirmed,
)


class ProtectionReadiness(StrEnum):
    FLAT = "flat"
    PROTECTED = "protected"
    MISSING_STOP_LOSS = "missing_stop_loss"
    MISSING_TAKE_PROFIT = "missing_take_profit"


async def evaluate_position_protection(
    read_position: Callable[[], Awaitable[BitgetPositionState | None]],
) -> ProtectionReadiness:
    """Classify live protection from a fresh provider read without mutating the venue."""
    position = await read_position()
    if position is None:
        return ProtectionReadiness.FLAT
    if position.stop_loss_id is None:
        return ProtectionReadiness.MISSING_STOP_LOSS
    if position.take_profit_id is None:
        return ProtectionReadiness.MISSING_TAKE_PROFIT
    return ProtectionReadiness.PROTECTED


def _positive_decimal(payload: dict[str, Any], *fields: str) -> Decimal | None:
    for field in fields:
        raw = payload.get(field)
        if raw is None:
            continue
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            return None
        return value if value >= 0 else None
    return None


def _plan_matches_quantity(plan: dict[str, Any], expected_quantity: Decimal) -> bool:
    quantity = _positive_decimal(plan, "size", "executeSize", "quantity")
    return quantity == expected_quantity


async def confirm_native_protection(
    read_position: Callable[[], Awaitable[Any]],
    read_pending_plans: Callable[[], Awaitable[Any]],
    *,
    expected_quantity: Decimal,
) -> ProtectionReport:
    """Confirm native SL and TP by fresh provider reads for the open filled quantity.

    A placement acknowledgement or plan ID is deliberately insufficient. The open position
    must remain isolated at exactly ``expected_quantity`` and its provider plan read must
    contain both a loss and profit plan at that quantity.
    """
    try:
        raw_position = await read_position()
        raw_plans = await read_pending_plans()
    except Exception:
        return ProtectionReport(ProtectionState.FAILED, Decimal("0"), "provider-read-failed")
    if not isinstance(raw_position, list):
        return ProtectionReport(ProtectionState.FAILED, Decimal("0"), "provider-position-invalid")
    positions = [row for row in raw_position if isinstance(row, dict)]
    open_positions = [
        row
        for row in positions
        if (quantity := _positive_decimal(row, "total", "size", "quantity")) is not None
        and quantity > 0
    ]
    if len(open_positions) != 1:
        return ProtectionReport(ProtectionState.FAILED, Decimal("0"), "position-not-open")
    position = open_positions[0]
    observed = _positive_decimal(position, "total", "size", "quantity")
    if observed is None:
        return ProtectionReport(ProtectionState.FAILED, Decimal("0"), "provider-position-invalid")
    if str(position.get("marginMode", "")).lower() != "isolated":
        return ProtectionReport(ProtectionState.DEGRADED, observed, "margin-mode-not-isolated")
    if observed != expected_quantity:
        return ProtectionReport(ProtectionState.DEGRADED, observed, "position-quantity-mismatch")
    if not isinstance(raw_plans, list) or not all(isinstance(plan, dict) for plan in raw_plans):
        return ProtectionReport(ProtectionState.FAILED, observed, "provider-plans-invalid")
    plan_types = {
        str(plan.get("planType", plan.get("type", ""))).lower()
        for plan in raw_plans
        if _plan_matches_quantity(plan, expected_quantity)
    }
    has_stop_loss = any("loss" in plan_type or "stop_loss" in plan_type for plan_type in plan_types)
    has_take_profit = any(
        "profit" in plan_type or "surplus" in plan_type or "take_profit" in plan_type
        for plan_type in plan_types
    )
    if not has_stop_loss:
        return ProtectionReport(ProtectionState.DEGRADED, observed, "missing-stop-loss")
    if not has_take_profit:
        return ProtectionReport(ProtectionState.DEGRADED, observed, "missing-take-profit")
    report = ProtectionReport(ProtectionState.VENUE_PROTECTED, observed)
    return report if protection_is_confirmed(report, expected_quantity) else ProtectionReport(
        ProtectionState.DEGRADED, observed, "position-quantity-mismatch"
    )
