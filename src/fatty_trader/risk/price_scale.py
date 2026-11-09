"""Automatic order-of-magnitude price scaling for meme coins / multi-exchange denomination differences."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Sequence


def align_price_scale(
    *,
    entry_price: Decimal,
    stop_loss: Decimal,
    take_profits: Sequence[Decimal],
    market_price: Decimal,
    direction: str,
    max_relative_error: Decimal = Decimal("0.35"),
) -> tuple[Decimal, Decimal, tuple[Decimal, ...], Decimal]:
    """Align signal prices (entry, SL, TP) when quoted in a different power-of-10 unit.

    For example, a channel quoting PEPE in 100PEPE units (e.g. entry 0.000502, SL 0.0004902)
    when Bitget quotes in single PEPE units (e.g. market 0.00000518) will have a 100x ratio.
    This function detects the power-of-10 mismatch, validates that the scaled entry is within
    market tolerance, checks that the stop-loss / take-profit geometry remains intact, and returns
    the scaled prices along with the applied scale factor (10^k).

    Returns:
        (scaled_entry, scaled_stop_loss, scaled_take_profits, applied_scale)
        If no scaling is needed or validation fails, applied_scale is Decimal("1").
    """
    if entry_price <= 0 or market_price <= 0:
        return entry_price, stop_loss, tuple(take_profits), Decimal("1")

    ratio = float(entry_price / market_price)
    if ratio <= 0:
        return entry_price, stop_loss, tuple(take_profits), Decimal("1")

    k = round(math.log10(ratio))
    if k == 0 or abs(k) > 8:
        return entry_price, stop_loss, tuple(take_profits), Decimal("1")

    scale = Decimal(10) ** k
    scaled_entry = entry_price / scale
    rel_error = abs(scaled_entry - market_price) / market_price

    if rel_error > max_relative_error:
        return entry_price, stop_loss, tuple(take_profits), Decimal("1")

    scaled_sl = stop_loss / scale
    scaled_tps_list: list[Decimal] = []
    for tp in take_profits:
        if tp <= 0:
            continue
        tp_ratio = float(tp / market_price)
        candidate = tp if (0.1 <= tp_ratio <= 10.0 and abs(ratio) >= 10) else (tp / scale)
        scaled_tps_list.append(candidate)
    scaled_tps = tuple(scaled_tps_list)
    # Geometry verification
    dir_upper = direction.upper()
    if dir_upper == "LONG":
        if scaled_sl >= scaled_entry:
            return entry_price, stop_loss, tuple(take_profits), Decimal("1")
        if any(tp <= scaled_entry for tp in scaled_tps):
            return entry_price, stop_loss, tuple(take_profits), Decimal("1")
    else:
        if scaled_sl <= scaled_entry:
            return entry_price, stop_loss, tuple(take_profits), Decimal("1")
        if any(tp >= scaled_entry for tp in scaled_tps):
            return entry_price, stop_loss, tuple(take_profits), Decimal("1")

    return scaled_entry, scaled_sl, scaled_tps, scale
