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
        if not self.quantity.is_finite() or self.quantity <= 0:
            raise ValueError("native protection quantity must be finite and positive")
        if self.stop_loss is None and self.take_profit is None:
            raise ValueError("native protection requires a stop loss or take profit")
        for value, name in (
            (self.stop_loss, "stop loss"),
            (self.take_profit, "take profit"),
            (self.stop_loss_size, "stop loss size"),
            (self.take_profit_size, "take profit size"),
        ):
            if value is not None and (not value.is_finite() or value <= 0):
                raise ValueError(f"native protection {name} must be finite and positive")
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
        return value if value.is_finite() and value >= 0 else None
    return None


def _canonical_hold_side(value: Any) -> str | None:
    normalized = str(value).strip().lower()
    if normalized in {"long", "buy"}:
        return "buy"
    if normalized in {"short", "sell"}:
        return "sell"
    return None


def _plan_hold_side(plan: dict[str, Any]) -> str | None:
    """Pending plans expose position direction separately from execution direction."""
    if plan.get("holdSide") is not None:
        return _canonical_hold_side(plan["holdSide"])
    pos_side = str(plan.get("posSide") or "").strip().lower()
    if pos_side in {"long", "short"}:
        return _canonical_hold_side(pos_side)
    if pos_side == "net" and str(plan.get("posMode") or "").strip().lower() == "one_way_mode":
        # TP/SL are exits: selling closes a long; buying closes a short.
        side = str(plan.get("side") or "").strip().lower()
        return {"sell": "buy", "buy": "sell"}.get(side)
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
        "not_trigger",
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
    leg_size = expectation.stop_loss_size if leg == "stopLoss" else expectation.take_profit_size
    position_level = expectation.position_level and leg_size is None
    expected_type = (
        "pos_loss"
        if leg == "stopLoss" and position_level
        else "pos_profit"
        if leg == "stopSurplus" and position_level
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
    hold_side = _plan_hold_side(plan)
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
    execute_price = _parse_decimal(
        plan, f"{leg}ExecutePrice", "executePrice", "price", zero_if_empty=True
    )
    if execute_price != Decimal("0"):
        return f"{leg_name}-execute-mode-mismatch"
    if plan.get("orderType") is not None and str(plan["orderType"]).strip().lower() != "market":
        return f"{leg_name}-execute-mode-mismatch"
    if plan.get("posMode") is not None and str(plan["posMode"]).strip().lower() != "one_way_mode":
        return f"{leg_name}-position-mode-mismatch"
    if plan.get("marginMode") is not None and str(plan["marginMode"]).strip().lower() != "isolated":
        return f"{leg_name}-margin-mode-mismatch"

    if expectation.position_level and expected_size is None:
        observed_size = _parse_decimal(plan, "size", zero_if_empty=True)
        if observed_size != Decimal("0"):
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
    if not isinstance(raw_position, list) or not all(
        isinstance(row, dict) and _positive_decimal(row, "total", "size", "quantity") is not None
        for row in raw_position
    ):
        return ProtectionReport(ProtectionState.FAILED, Decimal("0"), "provider-position-invalid")
    positions: list[dict[str, Any]] = raw_position
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

    if expectation.stop_loss_size is not None and expectation.stop_loss_size < observed_quantity:
        return ProtectionReport(
            ProtectionState.DEGRADED, observed_quantity, "stop-loss-size-does-not-cover-position"
        )
    expected_sl_id = expectation.stop_loss_provider_order_id
    expected_tp_id = expectation.take_profit_provider_order_id
    sl_position_level = expectation.position_level and expectation.stop_loss_size is None
    tp_position_level = expectation.position_level and expectation.take_profit_size is None
    observed_sl_id = str(position.get("stopLossId") or "").strip() or None
    observed_tp_id = str(position.get("takeProfitId") or "").strip() or None
    if sl_position_level and expectation.stop_loss is not None and observed_sl_id is None:
        return ProtectionReport(ProtectionState.DEGRADED, observed_quantity, "missing-stop-loss")
    if tp_position_level and expectation.take_profit is not None and observed_tp_id is None:
        return ProtectionReport(ProtectionState.DEGRADED, observed_quantity, "missing-take-profit")
    if sl_position_level and expected_sl_id is not None and observed_sl_id != expected_sl_id:
        return ProtectionReport(
            ProtectionState.DEGRADED, observed_quantity, "stop-loss-id-mismatch"
        )
    if tp_position_level and expected_tp_id is not None and observed_tp_id != expected_tp_id:
        return ProtectionReport(
            ProtectionState.DEGRADED, observed_quantity, "take-profit-id-mismatch"
        )
    sl_plan_id = expected_sl_id or (observed_sl_id if sl_position_level else None)
    tp_plan_id = expected_tp_id or (observed_tp_id if tp_position_level else None)

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
            provider_order_id=sl_plan_id,
        )
        if stop_loss_plan is None:
            return ProtectionReport(ProtectionState.DEGRADED, observed_quantity, reason)
        reason = _verify_exact_leg(
            stop_loss_plan,
            expectation=expectation,
            leg="stopLoss",
            trigger=expectation.stop_loss,
            provider_order_id=sl_plan_id,
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
            provider_order_id=tp_plan_id,
        )
        if take_profit_plan is None:
            return ProtectionReport(ProtectionState.DEGRADED, observed_quantity, reason)
        reason = _verify_exact_leg(
            take_profit_plan,
            expectation=expectation,
            leg="stopSurplus",
            trigger=expectation.take_profit,
            provider_order_id=tp_plan_id,
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
    """Check the live position's own active market/mark plans, never unrelated sizes.

    A quantity-only monitor cannot prove the source's intended trigger. It can still
    prove both provider IDs, position identity, trigger mode and full-position coverage.
    Failure to read the plans is not evidence of protection.
    """
    try:
        raw_position = await read_position()
    except Exception:
        return ProtectionReport(ProtectionState.FAILED, Decimal("0"), "provider-read-failed")
    if not isinstance(raw_position, list) or not all(
        isinstance(row, dict) and _positive_decimal(row, "total", "size", "quantity") is not None
        for row in raw_position
    ):
        return ProtectionReport(ProtectionState.FAILED, Decimal("0"), "provider-position-invalid")
    open_positions = [
        row
        for row in raw_position
        if (_positive_decimal(row, "total", "size", "quantity") or Decimal("0")) > 0
    ]
    if len(open_positions) != 1:
        return ProtectionReport(ProtectionState.FAILED, Decimal("0"), "position-not-open")
    position = open_positions[0]
    observed = _positive_decimal(position, "total", "size", "quantity")
    assert observed is not None
    if str(position.get("marginMode", "")).strip().lower() != "isolated":
        return ProtectionReport(ProtectionState.DEGRADED, observed, "margin-mode-not-isolated")
    if observed != expected_quantity:
        return ProtectionReport(ProtectionState.DEGRADED, observed, "position-quantity-mismatch")
    sl_id = str(position.get("stopLossId") or "").strip()
    tp_id = str(position.get("takeProfitId") or "").strip()
    if not sl_id:
        return ProtectionReport(ProtectionState.DEGRADED, observed, "missing-stop-loss")
    if not tp_id:
        return ProtectionReport(ProtectionState.DEGRADED, observed, "missing-take-profit")
    try:
        plans = await read_pending_plans()
    except Exception as exc:
        return ProtectionReport(
            ProtectionState.DEGRADED if "400172" in str(exc) else ProtectionState.FAILED,
            observed,
            "provider-plans-unavailable" if "400172" in str(exc) else "provider-plans-read-failed",
        )
    if not isinstance(plans, list) or not all(isinstance(plan, dict) for plan in plans):
        return ProtectionReport(ProtectionState.FAILED, observed, "provider-plans-invalid")
    triggers: dict[str, Decimal] = {}
    for leg, plan_type, order_id in (
        ("stopLoss", "pos_loss", sl_id),
        ("stopSurplus", "pos_profit", tp_id),
    ):
        matches = [
            plan
            for plan in plans
            if _plan_type(plan) == plan_type and _plan_order_id(plan) == order_id
        ]
        leg_name = "stop-loss" if leg == "stopLoss" else "take-profit"
        if len(matches) != 1:
            return ProtectionReport(
                ProtectionState.DEGRADED, observed, f"{leg_name}-plan-not-unique"
            )
        trigger = _parse_decimal(matches[0], f"{leg}TriggerPrice", "triggerPrice")
        if trigger is None or trigger <= 0:
            return ProtectionReport(
                ProtectionState.DEGRADED, observed, f"{leg_name}-trigger-mismatch"
            )
        triggers[leg] = trigger
    try:
        expectation = NativeProtectionExpectation(
            symbol=str(position.get("symbol") or ""),
            hold_side=str(position.get("holdSide") or ""),
            quantity=expected_quantity,
            stop_loss=triggers["stopLoss"],
            take_profit=triggers["stopSurplus"],
            stop_loss_provider_order_id=sl_id,
            take_profit_provider_order_id=tp_id,
        )
    except ValueError:
        return ProtectionReport(ProtectionState.FAILED, observed, "provider-position-invalid")

    async def position_snapshot() -> Any:
        return raw_position

    async def plan_snapshot() -> Any:
        return plans

    return await _confirm_exact_native_protection(position_snapshot, plan_snapshot, expectation)
