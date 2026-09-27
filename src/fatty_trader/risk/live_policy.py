"""Live position sizing policy for the Bitget USDT-FUTURES venue.

Pipeline (fail-closed, deterministic):

1. Position cap: ``active_positions >= risk.max_normal_positions`` -> skip.
2. Margin: ``allocation_pct * available_usdt`` (dynamic allocation), then capped
   down by ``risk.max_margin_per_trade_usdt`` when one is configured. The cap is
   enforced twice: on the requested margin, and again on the step-rounded quantity,
   since rounding can push realized margin back above the ceiling.
3. Leverage: candidates are walked from ``min(_MAX_LIVE_LEVERAGE, risk.max_leverage,
   meta.max_leverage)`` DOWN to ``max(_MIN_LIVE_LEVERAGE, risk.min_leverage)``. Each
   candidate must be exchange-legal (step-rounded quantity at or above min-notional
   and within the margin cap) and must clear ``check_sl_before_liquidation``. The
   highest such leverage wins, so an ordinary signal trades at the pinned ceiling
   (20x) and a wide-stop signal backs off only as far as its own stop geometry
   requires. The ceiling is never exceeded.
4. Min-notional is enforced AFTER rounding. When no leverage meets it:
   all-in fallback (margin = full balance, still capped) ONLY when
   ``active_positions == 0`` (``fallback_used=True``); otherwise skip with reason.
   Margin size cannot rescue a liquidation-guard failure (the margin ratio is
   ``1/leverage``), so that case is terminal.
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

_MIN_LIVE_LEVERAGE = 5
# Ceiling pinned to 20: a live trade is never above 20x. The floor above is only the
# absolute lower bound of the downward search; the configured ``min_leverage``
# (BITGET_MIN_LEVERAGE, default 20) is what actually permits backing off.
_MAX_LIVE_LEVERAGE = 20

# Failure tags for one leverage candidate, used to pick the skip reason.
_MIN_NOTIONAL_FAILURE = "min-notional"
_CAP_FAILURE = "margin-cap"
_GUARD_FAILURE = "sl-guard"

# One accepted plan: (leverage, quantity, margin, notional, liquidation price).
_Plan = tuple[int, Decimal, Decimal, Decimal, Decimal]


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


def _plan_at_leverage(
    *,
    meta: SymbolMetadata,
    risk: BitgetLiveRiskConfig,
    leverage: int,
    margin: Decimal,
    rounded_entry: Decimal,
    required: Decimal,
    cap: Decimal | None,
    direction: Direction,
    stop: Decimal,
    taker_fee_rate: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal] | str:
    """Plan one leverage: ``(qty, margin, notional, liquidation_price)`` or a tag.

    The liquidation guard's headroom depends on the leverage, the entry, the stop and
    the maintenance-margin tiers — but *not* on the committed margin, because the
    margin ratio is ``1 / leverage`` by construction. A stop that is unsafe at a
    given leverage is therefore unsafe at every margin size, so the only cure is a
    lower leverage: the caller walks leverage downwards rather than sizing up.
    """
    per_qty = rounded_entry * meta.contract_value
    if per_qty <= 0:
        return _MIN_NOTIONAL_FAILURE
    raw_qty = (margin * leverage) / per_qty
    qty = round_qty_to_step(raw_qty, meta.size_step)
    # Step rounding can round up past the cap, so re-derive size and margin from the
    # capped quantity before the guard sees them.
    capped = _enforce_margin_cap(
        margin=margin,
        leverage=leverage,
        qty=qty,
        entry=rounded_entry,
        meta=meta,
        cap=cap,
    )
    if capped is None:
        return _CAP_FAILURE
    actual_margin, qty = capped
    notional = qty * per_qty
    if notional < required:
        return _MIN_NOTIONAL_FAILURE
    liq = estimate_liquidation_price(
        direction=direction,
        entry=rounded_entry,
        quantity=qty,
        leverage=leverage,
        margin_usdt=actual_margin,
        mm_tiers=meta.mm_tiers,
        taker_fee_rate=taker_fee_rate,
        contract_multiplier=meta.contract_value,
    )
    if not check_sl_before_liquidation(
        direction=direction,
        entry=rounded_entry,
        stop_loss=stop,
        liquidation_price=liq,
        buffer=risk.liquidation_buffer,
        minimum_gap_pct=risk.minimum_liquidation_gap_pct,
        minimum_ticks=risk.minimum_liquidation_ticks,
        price_tick=meta.price_tick,
        latency_slippage_allowance=risk.latency_slippage_allowance,
    ):
        return _GUARD_FAILURE
    return qty, actual_margin, notional, liq


def _select_plan(
    *,
    meta: SymbolMetadata,
    risk: BitgetLiveRiskConfig,
    margin: Decimal,
    rounded_entry: Decimal,
    required: Decimal,
    cap: Decimal | None,
    direction: Direction,
    stop: Decimal,
    taker_fee_rate: Decimal,
    low: int,
    high: int,
) -> tuple[_Plan | None, list[str]]:
    """Pick the highest leverage that is legal and safe; return ``(plan, tags)``.

    Highest first so an ordinary signal keeps trading at the pinned 20x, and a
    wide-stop signal backs off only as far as its own stop geometry requires.
    """
    tags: list[str] = []
    for leverage in range(high, low - 1, -1):
        attempt = _plan_at_leverage(
            meta=meta,
            risk=risk,
            leverage=leverage,
            margin=margin,
            rounded_entry=rounded_entry,
            required=required,
            cap=cap,
            direction=direction,
            stop=stop,
            taker_fee_rate=taker_fee_rate,
        )
        if isinstance(attempt, str):
            tags.append(attempt)
            continue
        qty, actual_margin, notional, liq = attempt
        return (leverage, qty, actual_margin, notional, liq), tags
    return None, tags


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

    def _attempt(search_margin: Decimal) -> tuple[_Plan | None, list[str]]:
        return _select_plan(
            meta=data.meta,
            risk=data.risk,
            margin=search_margin,
            rounded_entry=rounded_entry,
            required=required,
            cap=cap,
            direction=data.direction,
            stop=stop,
            taker_fee_rate=data.taker_fee_rate,
            low=low,
            high=high,
        )

    plan, tags = _attempt(margin)
    fallback_used = False
    if plan is None:
        if _GUARD_FAILURE in tags:
            # Every leverage that can meet min-notional leaves this stop beyond the
            # liquidation price, and a larger margin cannot help because the margin
            # ratio is 1/leverage. Terminal, and loud: the signal itself is what does
            # not fit the configured leverage floor.
            return _skip("sl-guard: stop-loss not safely before liquidation")
        if _CAP_FAILURE in tags and _MIN_NOTIONAL_FAILURE not in tags:
            return _skip("margin-cap: no exchange-legal size fits the margin cap")
        if data.active_positions != 0:
            return _skip("min-notional unmeetable at allocation margin; no all-in fallback")
        fallback_used = True
        # All-in must not bypass the cap: it is still one trade's margin.
        all_in_margin = data.available_usdt if cap is None else min(data.available_usdt, cap)
        plan, tags = _attempt(all_in_margin)
        if plan is None:
            if _GUARD_FAILURE in tags:
                return _skip(
                    "sl-guard: stop-loss not safely before liquidation",
                    fallback_used=True,
                )
            return _skip("min-notional unmeetable even all-in", fallback_used=True)

    leverage, qty, margin, notional, liq = plan
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
