"""Live position sizing policy for the Bitget USDT-FUTURES venue.

Pipeline (fail-closed, deterministic):

1. Position cap: ``active_positions >= risk.max_normal_positions`` -> skip.
2. Margin: ``allocation_pct * available_usdt`` (dynamic allocation).
3. Leverage search ascending in ``[max(20, risk.min_leverage),
   min(50, risk.max_leverage, meta.max_leverage)]``; first leverage whose
   tick/step-rounded quantity meets min-notional wins (lowest safe leverage).
4. Min-notional is enforced AFTER rounding. When no leverage meets it:
   all-in fallback (margin = full balance) ONLY when ``active_positions == 0``
   (``fallback_used=True``); otherwise skip with reason.
5. SL: explicit ``stop_loss`` or ``derive_sl_tp(entry, direction, atr)``;
   neither available -> skip. SL must pass ``check_sl_before_liquidation``
   against the estimated liq price, else skip. Missing MM tiers raises
   (hard failure from the liquidation module).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from fatty_trader.domain.enums import Direction
from fatty_trader.domain.models import BitgetLiveRiskConfig
from fatty_trader.risk.liquidation import (
    check_sl_before_liquidation,
    estimate_liquidation_price,
)
from fatty_trader.risk.sizing import (
    SymbolMetadata,
    derive_sl_tp,
    round_price_to_tick,
    round_qty_to_step,
)

_MIN_LIVE_LEVERAGE = 20
_MAX_LIVE_LEVERAGE = 50


class LiveSizingInput(BaseModel):
    """Inputs for one live sizing decision."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    meta: SymbolMetadata
    risk: BitgetLiveRiskConfig = Field(default_factory=BitgetLiveRiskConfig)
    available_usdt: Decimal = Field(gt=0)
    entry: Decimal = Field(gt=0)
    direction: Direction
    active_positions: int = Field(ge=0)
    stop_loss: Decimal | None = Field(default=None, gt=0)
    atr: Decimal | None = Field(default=None, gt=0)
    taker_fee_rate: Decimal = Field(default=Decimal("0.0006"), ge=0)


class LiveSizingDecision(BaseModel):
    """Outcome of ``plan_live_position`` (accepted plan or skip reason)."""

    model_config = ConfigDict(frozen=True)

    accepted: bool
    reason: str
    leverage: int | None = None
    margin_usdt: Decimal | None = None
    quantity: Decimal | None = None
    rounded_entry: Decimal | None = None
    notional_usdt: Decimal | None = None
    liquidation_price: Decimal | None = None
    stop_loss: Decimal | None = None
    fallback_used: bool = False


def _skip(reason: str, *, fallback_used: bool = False) -> LiveSizingDecision:
    return LiveSizingDecision(accepted=False, reason=reason, fallback_used=fallback_used)


def _leverage_bounds(meta: SymbolMetadata, risk: BitgetLiveRiskConfig) -> tuple[int, int]:
    low = max(_MIN_LIVE_LEVERAGE, risk.min_leverage)
    high = min(_MAX_LIVE_LEVERAGE, risk.max_leverage, meta.max_leverage)
    return low, high


def _required_notional(meta: SymbolMetadata, rounded_entry: Decimal) -> Decimal:
    from_step = meta.min_order_qty * rounded_entry * meta.contract_value
    return max(meta.min_notional, from_step)


def _try_margin(
    *,
    meta: SymbolMetadata,
    risk: BitgetLiveRiskConfig,
    margin: Decimal,
    rounded_entry: Decimal,
    required: Decimal,
    low: int,
    high: int,
) -> tuple[int, Decimal, Decimal] | None:
    """Search leverage ascending; return (lev, qty, notional) or None."""
    for leverage in range(low, high + 1):
        raw_qty = (margin * leverage) / (rounded_entry * meta.contract_value)
        qty = round_qty_to_step(raw_qty, meta.size_step)
        if qty < meta.min_order_qty:
            continue
        notional = qty * rounded_entry * meta.contract_value
        if notional >= required:
            return leverage, qty, notional
    return None


def plan_live_position(data: LiveSizingInput) -> LiveSizingDecision:
    """Plan one live isolated position or skip with a reason (never raises for skips)."""
    if data.active_positions >= data.risk.max_normal_positions:
        return _skip(
            f"position-cap: {data.active_positions} active "
            f"(max {data.risk.max_normal_positions} normal positions)"
        )

    if len(data.meta.mm_tiers) == 0:
        # Hard failure per contract — surface via the liquidation module's error.
        estimate_liquidation_price(
            direction=data.direction,
            entry=data.entry,
            quantity=Decimal("1"),
            leverage=_MIN_LIVE_LEVERAGE,
            margin_usdt=Decimal("1"),
            mm_tiers=(),
        )
        raise AssertionError("unreachable: empty MM tiers must raise")

    low, high = _leverage_bounds(data.meta, data.risk)
    if low > high:
        return _skip(f"no live leverage in [{low}, {high}] for symbol max")

    rounded_entry = round_price_to_tick(data.entry, data.meta.price_tick)
    required = _required_notional(data.meta, rounded_entry)
    margin = data.risk.allocation_pct * data.available_usdt

    stop = data.stop_loss
    if stop is None:
        if data.atr is None:
            return _skip("no stop-loss and no ATR for SL/TP fallback")
        stop, _ = derive_sl_tp(data.entry, data.direction, data.atr)

    found = _try_margin(
        meta=data.meta,
        risk=data.risk,
        margin=margin,
        rounded_entry=rounded_entry,
        required=required,
        low=low,
        high=high,
    )
    fallback_used = False
    if found is None:
        if data.active_positions != 0:
            return _skip("min-notional unmeetable at allocation margin; no all-in fallback")
        fallback_used = True
        found = _try_margin(
            meta=data.meta,
            risk=data.risk,
            margin=data.available_usdt,
            rounded_entry=rounded_entry,
            required=required,
            low=low,
            high=high,
        )
        if found is None:
            return _skip("min-notional unmeetable even all-in", fallback_used=True)
        margin = data.available_usdt

    leverage, qty, notional = found
    liq = estimate_liquidation_price(
        direction=data.direction,
        entry=rounded_entry,
        quantity=qty,
        leverage=leverage,
        margin_usdt=margin,
        mm_tiers=data.meta.mm_tiers,
        taker_fee_rate=data.taker_fee_rate,
        contract_multiplier=data.meta.contract_value,
    )
    if not check_sl_before_liquidation(
        direction=data.direction,
        entry=rounded_entry,
        stop_loss=stop,
        liquidation_price=liq,
        buffer=data.risk.liquidation_buffer,
    ):
        return _skip(
            "sl-guard: stop-loss not safely before liquidation",
            fallback_used=fallback_used,
        )
    return LiveSizingDecision(
        accepted=True,
        reason="ok",
        leverage=leverage,
        margin_usdt=margin,
        quantity=qty,
        rounded_entry=rounded_entry,
        notional_usdt=notional,
        liquidation_price=liq,
        stop_loss=stop,
        fallback_used=fallback_used,
    )
