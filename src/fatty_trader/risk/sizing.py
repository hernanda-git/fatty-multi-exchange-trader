from decimal import ROUND_CEILING, Decimal

from fatty_trader.domain.models import InstrumentSpec, SizingPlan, VenueRiskConfig


class SizingError(ValueError):
    """Raised when a venue minimum cannot be met inside immutable risk rails."""


def _ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def minimum_safe_plan(
    *, spec: InstrumentSpec, config: VenueRiskConfig, reference_price: Decimal
) -> SizingPlan:
    if reference_price <= 0:
        raise SizingError("reference price must be positive")

    min_qty_notional = spec.min_qty * reference_price * spec.contract_multiplier
    required_min = max(spec.min_notional, min_qty_notional) * Decimal("1.02")
    leverage_cap = min(spec.max_leverage, config.max_leverage)
    leverage = min(max(config.default_leverage, 1), leverage_cap)
    required_leverage = (required_min / config.base_margin_usdt).to_integral_value(
        rounding=ROUND_CEILING
    )
    leverage = min(max(leverage, int(required_leverage)), leverage_cap)
    margin = config.base_margin_usdt
    if margin * leverage < required_min:
        margin = required_min / Decimal(leverage)

    if margin > config.max_auto_margin_usdt:
        raise SizingError("required margin exceeds auto margin cap")
    if margin > config.free_margin_usdt * config.free_margin_headroom_pct:
        raise SizingError("required margin exceeds safe free-margin headroom")

    notional = margin * leverage
    if notional > config.max_position_notional_usdt:
        raise SizingError("required notional exceeds position cap")

    quantity = _ceil_to_step(
        required_min / (reference_price * spec.contract_multiplier), spec.qty_step
    )
    final_notional = quantity * reference_price * spec.contract_multiplier
    if final_notional > config.max_position_notional_usdt:
        raise SizingError("rounded quantity exceeds position cap")
    return SizingPlan(
        effective_leverage=leverage,
        effective_margin_usdt=margin,
        notional_usdt=final_notional,
        quantity=quantity,
        required_min_notional_usdt=required_min,
    )
