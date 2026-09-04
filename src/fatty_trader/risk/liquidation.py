"""Isolated-margin liquidation-price estimate + SL-before-liquidation guard.

Estimate (documented, deterministic, no network):

    notional      = entry * qty * contract_multiplier
    mmr           = MMR of the tier with the smallest upper bound >= notional
                    (last tier wins when notional exceeds every bound)
    margin_ratio  = margin_usdt / notional
    LONG:  liq = entry * (1 - margin_ratio + mmr + taker_fee_rate)
    SHORT: liq = entry * (1 + margin_ratio - mmr - taker_fee_rate)

Rationale: in isolated margin the position dies when the adverse move consumes
the posted margin, minus the maintenance-margin + taker-fee allowance the
venue reserves before closing. Higher leverage (smaller margin_ratio), higher
MMR, or higher fees all push liq toward entry. This is an estimate for
pre-trade guarding, not the venue's exact engine value.

SL guard with buffer ``b`` (default 0.10): SL must sit strictly between entry
and liq, at least ``b`` of the way from liq toward entry:

    LONG:  liq < SL < entry  and  (SL - liq) / (entry - liq) >= b
    SHORT: entry < SL < liq  and  (liq - SL) / (liq - entry) >= b

Strict inequalities: equality on any bound fails. Missing/empty MM tiers is a
hard failure (raises) — never silently guard with an unknown liq price.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fatty_trader.domain.enums import Direction


class LiquidationGuardError(ValueError):
    """Raised when liquidation inputs are unusable (e.g. missing MM tiers)."""


class MMTier(BaseModel):
    """One maintenance-margin tier: MMR applies while notional <= upper bound.

    ``upper_bound_notional=None`` marks the final catch-all tier.
    """

    model_config = ConfigDict(frozen=True)

    upper_bound_notional: Decimal | None = Field(default=None, ge=0)
    mmr: Decimal = Field(ge=0, le=1)


def select_mmr(notional: Decimal, mm_tiers: tuple[MMTier, ...] | list[MMTier]) -> Decimal:
    """Return the MMR for ``notional``; raise when tiers are missing."""
    if len(mm_tiers) == 0:
        raise LiquidationGuardError("maintenance-margin tiers are required")
    for tier in mm_tiers:
        if tier.upper_bound_notional is None or notional <= tier.upper_bound_notional:
            return tier.mmr
    return mm_tiers[-1].mmr


def estimate_liquidation_price(
    *,
    direction: Direction | Literal["LONG", "SHORT"],
    entry: Decimal,
    quantity: Decimal,
    leverage: int,
    margin_usdt: Decimal,
    mm_tiers: tuple[MMTier, ...] | list[MMTier],
    taker_fee_rate: Decimal = Decimal("0.0006"),
    contract_multiplier: Decimal = Decimal("1"),
) -> Decimal:
    """Estimate the isolated-margin liquidation price (see module docstring)."""
    side = Direction(direction)
    if entry <= 0 or quantity <= 0 or leverage <= 0 or margin_usdt <= 0:
        raise LiquidationGuardError("entry/quantity/leverage/margin must be positive")
    if contract_multiplier <= 0:
        raise LiquidationGuardError("contract multiplier must be positive")
    if taker_fee_rate < 0:
        raise LiquidationGuardError("taker fee rate must be non-negative")
    notional = entry * quantity * contract_multiplier
    if notional <= 0:
        raise LiquidationGuardError("notional must be positive")
    mmr = select_mmr(notional, mm_tiers)
    margin_ratio = margin_usdt / notional
    if side is Direction.LONG:
        liq = entry * (Decimal("1") - margin_ratio + mmr + taker_fee_rate)
    else:
        liq = entry * (Decimal("1") + margin_ratio - mmr - taker_fee_rate)
    if liq <= 0:
        raise LiquidationGuardError("estimated liquidation price is non-positive")
    return liq


def check_sl_before_liquidation(
    *,
    direction: Direction | Literal["LONG", "SHORT"],
    entry: Decimal,
    stop_loss: Decimal,
    liquidation_price: Decimal,
    buffer: Decimal = Decimal("0.10"),
) -> bool:
    """Return True iff SL sits strictly between entry and liq with buffer gap.

    LONG:  liq < SL < entry  and (SL - liq) / (entry - liq) >= buffer.
    SHORT: entry < SL < liq  and (liq - SL) / (liq - entry) >= buffer.
    """
    side = Direction(direction)
    if entry <= 0 or stop_loss <= 0 or liquidation_price <= 0:
        return False
    gap: Decimal
    if side is Direction.LONG:
        if not (liquidation_price < stop_loss < entry):
            return False
        span = entry - liquidation_price
        gap = (stop_loss - liquidation_price) / span
    else:
        if not (entry < stop_loss < liquidation_price):
            return False
        span = liquidation_price - entry
        gap = (liquidation_price - stop_loss) / span
    return gap >= buffer
