"""Live position sizing policy for the Bitget USDT-FUTURES venue.

Pipeline (fail-closed, deterministic):

1. Position cap: ``active_positions >= risk.max_normal_positions`` -> skip.
2. Margin: ``allocation_pct * available_usdt`` (dynamic allocation), then capped
   down by ``risk.max_margin_per_trade_usdt`` when one is configured. The cap is
   enforced twice: on the requested margin, and again on the step-rounded quantity,
   since rounding can push realized margin back above the ceiling.
3. Leverage search ascending in ``[max(20, risk.min_leverage),
   min(_MAX_LIVE_LEVERAGE, risk.max_leverage, meta.max_leverage)]``; first leverage
   whose tick/step-rounded quantity meets min-notional wins (lowest safe leverage).
   ``_MAX_LIVE_LEVERAGE`` is pinned to 20 so ``meta.max_leverage`` (the venue's
   advertised ceiling, up to 150) can never widen the executed leverage.
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
# Pinned to 20: a live trade is always 20x. Keeping this ceiling at 50 would
# let a drifted config widen leverage past the intended policy.
_MAX_LIVE_LEVERAGE = 20


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


def _margin_cap(risk: BitgetLiveRiskConfig) -> Decimal | None:
    """Return the hard per-trade margin ceiling, or None when no cap is configured."""
    cap = risk.max_margin_per_trade_usdt
    if cap is None:
        return None
    if cap <= 0:
        raise ValueError("max_margin_per_trade_usdt must be positive when set")
    return cap


def _enforce_margin_cap(
    *,
    margin: Decimal,
    leverage: int,
    qty: Decimal,
    entry: Decimal,
    meta: SymbolMetadata,
    cap: Decimal | None,
) -> tuple[Decimal, Decimal] | None:
    """Shrink the quantity until the *actual* margin (notional / leverage) fits the cap.

    Sizing rounds quantity to the exchange step, so a capped request can round *up*
    past the ceiling. Recomputing margin from the final quantity and flooring the
    quantity keeps realized margin at or below the cap. Returns None if no size
    survives.
    """
    if cap is None:
        return margin, qty
    if qty <= 0:
        return None
    per_qty = entry * meta.contract_value
    if per_qty <= 0:
        return None
    max_qty = (cap * Decimal(leverage)) / per_qty
    capped_qty = round_qty_to_step(min(qty, max_qty), meta.size_step)
    if capped_qty < meta.min_order_qty:
        return None
    actual_margin = (capped_qty * per_qty) / Decimal(leverage)
    if actual_margin > cap:
        return None
    return actual_margin, capped_qty


def plan_live_position(data: LiveSizingInput) -> LiveSizingDecision:
    """Plan one live isolated position or skip with a reason (never raises for skips)."""
    cap = _margin_cap(data.risk)
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
    # The cap is a ceiling on the allocation, not a substitute for it: taking the
    # smaller here means the leverage search below already plans within the cap.
    if cap is not None:
        margin = min(margin, cap)

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
        # All-in must not bypass the cap: it is still one trade's margin.
        margin = data.available_usdt if cap is None else min(data.available_usdt, cap)

    leverage, qty, notional = found
    # Quantity was rounded to the exchange step, which can round up past the cap, so
    # re-derive both size and margin from the capped quantity before the SL guard.
    capped = _enforce_margin_cap(
        margin=margin,
        leverage=leverage,
        qty=qty,
        entry=rounded_entry,
        meta=data.meta,
        cap=cap,
    )
    if capped is None:
        return _skip(
            "margin-cap: no exchange-legal size fits the margin cap",
            fallback_used=fallback_used,
        )
    margin, qty = capped
    notional = qty * rounded_entry * data.meta.contract_value
    if notional < required:
        # Shrinking to fit the cap can drop the order under the venue's min-notional,
        # which the exchange would reject. Skip rather than send an invalid order.
        return _skip(
            f"margin-cap: {qty} at {leverage}x is under min-notional {required}",
            fallback_used=fallback_used,
        )
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
        minimum_gap_pct=data.risk.minimum_liquidation_gap_pct,
        minimum_ticks=data.risk.minimum_liquidation_ticks,
        price_tick=data.meta.price_tick,
        latency_slippage_allowance=data.risk.latency_slippage_allowance,
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
