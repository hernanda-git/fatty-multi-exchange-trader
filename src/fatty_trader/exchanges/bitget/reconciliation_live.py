from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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


@dataclass(frozen=True)
class NativeProtectionExpectation:
    """Provider-backed contract expected after a native protection POST."""

    symbol: str
    hold_side: str
    quantity: Decimal
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    stop_loss_client_oid: str | None = None
    take_profit_client_oid: str | None = None
    stop_loss_provider_order_id: str | None = None
    take_profit_provider_order_id: str | None = None
    stop_loss_size: Decimal | None = None
    take_profit_size: Decimal | None = None
    position_level: bool = True

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("native protection symbol is required")
        if self.hold_side.strip().lower() not in {"long", "short", "buy", "sell"}:
            raise ValueError("native protection hold side is invalid")
        if self.quantity <= 0:
            raise ValueError("native protection quantity must be positive")
        if self.stop_loss is None and self.take_profit is None:
            raise ValueError("native protection requires a stop loss or take profit")
        for value, name in (
            (self.stop_loss, "stop loss"),
            (self.take_profit, "take profit"),
            (self.stop_loss_size, "stop loss size"),
            (self.take_profit_size, "take profit size"),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"native protection {name} must be positive")
        if self.stop_loss_size is not None and self.stop_loss_size > self.quantity:
            raise ValueError("native protection stop loss size exceeds position quantity")
        if self.take_profit_size is not None and self.take_profit_size > self.quantity:
            raise ValueError("native protection take profit size exceeds position quantity")


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


def _canonical_hold_side(value: Any) -> str | None:
    normalized = str(value).strip().lower()
    if normalized in {"long", "buy"}:
        return "buy"
    if normalized in {"short", "sell"}:
        return "sell"
    return None


def _parse_decimal(
    payload: dict[str, Any], *fields: str, zero_if_empty: bool = False
) -> Decimal | None:
    for field in fields:
        if field not in payload:
            continue
        raw = payload.get(field)
        if raw is None or str(raw).strip() == "":
            return Decimal("0") if zero_if_empty else None
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            return None
        return value if value.is_finite() else None
    return Decimal("0") if zero_if_empty else None


def _plan_type(plan: dict[str, Any]) -> str:
    return str(plan.get("planType", plan.get("type", ""))).strip().lower()


def _plan_field(plan: dict[str, Any], leg: str, field: str) -> Any:
    specific = plan.get(f"{leg}{field}")
    if specific is not None:
        return specific
    generic_fields = {
        "TriggerType": "triggerType",
        "ExecutePrice": "executePrice",
    }
    return plan.get(generic_fields.get(field, field))


def _plan_order_id(plan: dict[str, Any]) -> str | None:
    for field in ("orderId", "planOrderId", "id"):
        value = plan.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _plan_client_oid(plan: dict[str, Any], leg: str) -> str | None:
    for field in (f"{leg}ClientOid", "clientOid", "clientOrderId"):
        value = plan.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


_ACTIVE_PLAN_STATUSES = frozenset(
    {
        "active",
        "in_progress",
        "in_progress_tracking",
        "live",
        "new",
        "not_triggered",
        "open",
        "pending",
        "waiting",
    }
)


def _find_exact_plan(
    plans: list[dict[str, Any]],
    *,
    expectation: NativeProtectionExpectation,
    leg: str,
    provider_order_id: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    leg_name = "stop-loss" if leg == "stopLoss" else "take-profit"
    expected_type = (
        "pos_loss"
        if leg == "stopLoss" and expectation.position_level
        else "pos_profit"
        if leg == "stopSurplus" and expectation.position_level
        else "loss_plan"
        if leg == "stopLoss"
        else "profit_plan"
    )
    candidates = [plan for plan in plans if _plan_type(plan) == expected_type]
    if provider_order_id is not None:
        candidates = [plan for plan in candidates if _plan_order_id(plan) == provider_order_id]
    expected_client_oid = (
        expectation.stop_loss_client_oid
        if leg == "stopLoss"
        else expectation.take_profit_client_oid
    )
    if expected_client_oid is not None:
        candidates = [
            plan for plan in candidates if _plan_client_oid(plan, leg) == expected_client_oid
        ]
    if len(candidates) != 1:
        return (
            None,
            f"{leg_name}-plan-not-unique",
        )
    return candidates[0], None


def _verify_exact_leg(
    plan: dict[str, Any],
    *,
    expectation: NativeProtectionExpectation,
    leg: str,
    trigger: Decimal,
    provider_order_id: str | None,
    client_oid: str | None,
    expected_size: Decimal | None,
) -> str | None:
    leg_name = "stop-loss" if leg == "stopLoss" else "take-profit"
    symbol = str(plan.get("symbol", "")).strip().upper()
    if symbol != expectation.symbol.strip().upper():
        return f"{leg_name}-symbol-mismatch"
    hold_side = _canonical_hold_side(plan.get("holdSide", plan.get("side")))
    expected_hold_side = _canonical_hold_side(expectation.hold_side)
    if hold_side != expected_hold_side:
        return f"{leg_name}-side-mismatch"
    status = str(plan.get("planStatus", plan.get("status", ""))).strip().lower()
    if status not in _ACTIVE_PLAN_STATUSES:
        return f"{leg_name}-status-mismatch"
    observed_order_id = _plan_order_id(plan)
    if observed_order_id is None:
        return f"{leg_name}-id-missing"
    if provider_order_id is not None and observed_order_id != provider_order_id:
        return f"{leg_name}-id-mismatch"
    observed_client_oid = _plan_client_oid(plan, leg)
    if observed_client_oid is None:
        return f"{leg_name}-client-oid-missing"
    if client_oid is not None and observed_client_oid != client_oid:
        return f"{leg_name}-client-oid-mismatch"

    observed_trigger = _parse_decimal(plan, f"{leg}TriggerPrice", "triggerPrice")
    if observed_trigger is None or observed_trigger != trigger:
        return f"{leg_name}-trigger-mismatch"
    trigger_type = str(_plan_field(plan, leg, "TriggerType") or "").strip().lower()
    if trigger_type != "mark_price":
        return f"{leg_name}-trigger-type-mismatch"
    execute_price = _parse_decimal(plan, f"{leg}ExecutePrice", "executePrice", zero_if_empty=True)
    if execute_price != Decimal("0"):
        return f"{leg_name}-execute-mode-mismatch"

    if expectation.position_level:
        raw_size = plan.get("size")
        if raw_size is not None and str(raw_size).strip() not in {"", "0", "0.0"}:
            return f"{leg_name}-size-mismatch"
    elif expected_size is None:
        return f"{leg_name}-size-expectation-missing"
    else:
        observed_size = _parse_decimal(plan, "size", "executeSize", "quantity")
        if observed_size != expected_size:
            return f"{leg_name}-size-mismatch"
    return None


async def _confirm_exact_native_protection(
    read_position: Callable[[], Awaitable[Any]],
    read_pending_plans: Callable[[], Awaitable[Any]],
    expectation: NativeProtectionExpectation,
) -> ProtectionReport:
    try:
        raw_position = await read_position()
    except Exception:
        return ProtectionReport(ProtectionState.FAILED, Decimal("0"), "provider-read-failed")
    if not isinstance(raw_position, list):
        return ProtectionReport(ProtectionState.FAILED, Decimal("0"), "provider-position-invalid")
    positions: list[dict[str, Any]] = [row for row in raw_position if isinstance(row, dict)]
    open_positions: list[dict[str, Any]] = []
    for row in positions:
        quantity = _positive_decimal(row, "total", "size", "quantity")
        if quantity is not None and quantity > 0:
            open_positions.append(row)
    if len(open_positions) != 1:
        return ProtectionReport(ProtectionState.FAILED, Decimal("0"), "position-not-open")
    position = open_positions[0]
    observed_quantity = _positive_decimal(position, "total", "size", "quantity")
    if observed_quantity is None:
        return ProtectionReport(ProtectionState.FAILED, Decimal("0"), "provider-position-invalid")
    expected_symbol = expectation.symbol.strip().upper()
    if str(position.get("symbol", "")).strip().upper() != expected_symbol:
        return ProtectionReport(
            ProtectionState.DEGRADED, observed_quantity, "position-symbol-mismatch"
        )
    if _canonical_hold_side(position.get("holdSide", position.get("side"))) != _canonical_hold_side(
        expectation.hold_side
    ):
        return ProtectionReport(
            ProtectionState.DEGRADED, observed_quantity, "position-side-mismatch"
        )
    if str(position.get("marginMode", "")).strip().lower() != "isolated":
        return ProtectionReport(
            ProtectionState.DEGRADED, observed_quantity, "margin-mode-not-isolated"
        )
    if str(position.get("posMode", "")).strip().lower() not in {
        "one_way_mode",
        "one-way",
        "one_way",
    }:
        return ProtectionReport(
            ProtectionState.DEGRADED, observed_quantity, "position-mode-not-one-way"
        )
    if observed_quantity != expectation.quantity:
        return ProtectionReport(
            ProtectionState.DEGRADED, observed_quantity, "position-quantity-mismatch"
        )

    expected_sl_id = expectation.stop_loss_provider_order_id
    expected_tp_id = expectation.take_profit_provider_order_id
    observed_sl_id = str(position.get("stopLossId", "")).strip() or None
    observed_tp_id = str(position.get("takeProfitId", "")).strip() or None
    if expectation.stop_loss is not None and observed_sl_id is None:
        return ProtectionReport(ProtectionState.DEGRADED, observed_quantity, "missing-stop-loss")
    if expectation.take_profit is not None and observed_tp_id is None:
        return ProtectionReport(ProtectionState.DEGRADED, observed_quantity, "missing-take-profit")
    if expected_sl_id is not None and observed_sl_id != expected_sl_id:
        return ProtectionReport(
            ProtectionState.DEGRADED, observed_quantity, "stop-loss-id-mismatch"
        )
    if expected_tp_id is not None and observed_tp_id != expected_tp_id:
        return ProtectionReport(
            ProtectionState.DEGRADED, observed_quantity, "take-profit-id-mismatch"
        )

    try:
        raw_plans = await read_pending_plans()
    except Exception as exc:
        if "400172" in str(exc):
            return ProtectionReport(
                ProtectionState.DEGRADED, observed_quantity, "provider-plans-unavailable"
            )
        return ProtectionReport(
            ProtectionState.FAILED, observed_quantity, "provider-plans-read-failed"
        )
    if not isinstance(raw_plans, list) or not all(isinstance(plan, dict) for plan in raw_plans):
        return ProtectionReport(ProtectionState.FAILED, observed_quantity, "provider-plans-invalid")
    plans = [dict(plan) for plan in raw_plans]

    if expectation.stop_loss is not None:
        stop_loss_plan, reason = _find_exact_plan(
            plans,
            expectation=expectation,
            leg="stopLoss",
            provider_order_id=expected_sl_id or observed_sl_id,
        )
        if stop_loss_plan is None:
            return ProtectionReport(ProtectionState.DEGRADED, observed_quantity, reason)
        reason = _verify_exact_leg(
            stop_loss_plan,
            expectation=expectation,
            leg="stopLoss",
            trigger=expectation.stop_loss,
            provider_order_id=expected_sl_id or observed_sl_id,
            client_oid=expectation.stop_loss_client_oid,
            expected_size=expectation.stop_loss_size,
        )
        if reason is not None:
            return ProtectionReport(ProtectionState.DEGRADED, observed_quantity, reason)

    if expectation.take_profit is not None:
        take_profit_plan, reason = _find_exact_plan(
            plans,
            expectation=expectation,
            leg="stopSurplus",
            provider_order_id=expected_tp_id or observed_tp_id,
        )
        if take_profit_plan is None:
            return ProtectionReport(ProtectionState.DEGRADED, observed_quantity, reason)
        reason = _verify_exact_leg(
            take_profit_plan,
            expectation=expectation,
            leg="stopSurplus",
            trigger=expectation.take_profit,
            provider_order_id=expected_tp_id or observed_tp_id,
            client_oid=expectation.take_profit_client_oid,
            expected_size=expectation.take_profit_size,
        )
        if reason is not None:
            return ProtectionReport(ProtectionState.DEGRADED, observed_quantity, reason)

    return ProtectionReport(ProtectionState.VENUE_PROTECTED, observed_quantity)


async def confirm_native_protection(
    read_position: Callable[[], Awaitable[Any]],
    read_pending_plans: Callable[[], Awaitable[Any]],
    *,
    expected_quantity: Decimal | None = None,
    expectation: NativeProtectionExpectation | None = None,
) -> ProtectionReport:
    """Confirm native protection using legacy or strict provider-backed checks.

    ``expectation`` is required by the post-fill path.  The legacy quantity-only form
    remains for the existing monitor/read-only callers until they have a full plan spec.
    """
    if expectation is not None:
        if expected_quantity is not None and expected_quantity != expectation.quantity:
            return ProtectionReport(
                ProtectionState.DEGRADED, expectation.quantity, "protection-quantity-mismatch"
            )
        return await _confirm_exact_native_protection(
            read_position, read_pending_plans, expectation
        )
    if expected_quantity is None:
        raise ValueError("expected quantity or native protection expectation is required")
    return await _confirm_legacy_native_protection(
        read_position, read_pending_plans, expected_quantity
    )


async def _confirm_legacy_native_protection(
    read_position: Callable[[], Awaitable[Any]],
    read_pending_plans: Callable[[], Awaitable[Any]],
    expected_quantity: Decimal,
) -> ProtectionReport:
    """Compatibility verifier for monitor calls that only have a quantity."""
    try:
        raw_position = await read_position()
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

    # Some symbols (e.g. GRASSUSDT) reject orders-plan-pending with 400172.
    # Fall back to position-field verification when plan read fails.
    raw_plans: list[dict[str, Any]] | None = None
    plans_unsupported = False
    plans_verified = False
    try:
        raw_plans = await read_pending_plans()
    except Exception as exc:
        if "400172" in str(exc):
            plans_unsupported = True
        else:
            return ProtectionReport(ProtectionState.FAILED, observed, "provider-plans-invalid")

    if not plans_unsupported and raw_plans is not None:
        if not isinstance(raw_plans, list) or not all(isinstance(plan, dict) for plan in raw_plans):
            return ProtectionReport(ProtectionState.FAILED, observed, "provider-plans-invalid")
        plan_types = {
            str(plan.get("planType", plan.get("type", ""))).lower()
            for plan in raw_plans
            if _plan_matches_quantity(plan, expected_quantity)
        }
        has_stop_loss = any(
            "loss" in plan_type or "stop_loss" in plan_type for plan_type in plan_types
        )
        has_take_profit = any(
            "profit" in plan_type or "surplus" in plan_type or "take_profit" in plan_type
            for plan_type in plan_types
        )
        if not has_stop_loss:
            return ProtectionReport(ProtectionState.DEGRADED, observed, "missing-stop-loss")
        if not has_take_profit:
            return ProtectionReport(ProtectionState.DEGRADED, observed, "missing-take-profit")
        plans_verified = True

    if not plans_verified:
        # The plan endpoint is known to reject some symbols with 400172. In
        # that case the position-level IDs are the only provider-backed proof.
        has_stop_loss_field = bool(position.get("stopLossId"))
        has_take_profit_field = bool(position.get("takeProfitId"))
        if not has_stop_loss_field:
            return ProtectionReport(ProtectionState.DEGRADED, observed, "missing-stop-loss")
        if not has_take_profit_field:
            return ProtectionReport(ProtectionState.DEGRADED, observed, "missing-take-profit")

    report = ProtectionReport(ProtectionState.VENUE_PROTECTED, observed)
    return (
        report
        if protection_is_confirmed(report, expected_quantity)
        else ProtectionReport(ProtectionState.DEGRADED, observed, "position-quantity-mismatch")
    )
