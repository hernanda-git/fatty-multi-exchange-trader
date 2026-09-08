"""Pure, fail-closed routing for signal entries affected by front-running."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from fatty_trader.domain.enums import Direction


class EntryMode(StrEnum):
    FULL_MARKET = "FULL_MARKET"
    SPLIT_MARKET_LIMIT = "SPLIT_MARKET_LIMIT"


@dataclass(frozen=True)
class EntryRoute:
    mode: EntryMode
    market_quantity: Decimal
    limit_quantity: Decimal
    limit_price: Decimal | None


def route_entry(
    *,
    direction: Direction,
    signal_entry: Decimal,
    market_price: Decimal,
    total_quantity: Decimal,
    late_threshold_pct: Decimal,
) -> EntryRoute:
    """Route a late signal into full market or 25% market / 75% Entry limit."""
    if signal_entry <= 0 or market_price <= 0 or total_quantity <= 0:
        raise ValueError("entry, market price, and quantity must be positive")
    if not Decimal("0") < late_threshold_pct < Decimal("1"):
        raise ValueError("late threshold must be between zero and one")
    adverse_move = (
        market_price >= signal_entry * (Decimal("1") + late_threshold_pct)
        if direction is Direction.LONG
        else market_price <= signal_entry * (Decimal("1") - late_threshold_pct)
    )
    if not adverse_move:
        return EntryRoute(EntryMode.FULL_MARKET, total_quantity, Decimal("0"), None)
    market_quantity = total_quantity * Decimal("0.25")
    limit_quantity = total_quantity - market_quantity
    return EntryRoute(EntryMode.SPLIT_MARKET_LIMIT, market_quantity, limit_quantity, signal_entry)
