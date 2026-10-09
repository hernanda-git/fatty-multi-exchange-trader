"""Immutable evidence handed from Bitget admission to entry execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fatty_trader.domain.enums import Direction
from fatty_trader.execution.entry_routing import EntryRoute
from fatty_trader.risk.sizing import round_price_to_tick


def normalized_protection_prices(
    *,
    direction: Direction,
    entry: Decimal,
    canonical_entry: Decimal,
    stop_loss: Decimal,
    take_profits: tuple[Decimal, ...],
    price_tick: Decimal,
) -> tuple[Decimal, tuple[Decimal, ...]]:
    """Validate the executable tick prices, without changing the source signal."""
    prices = (entry, canonical_entry, stop_loss, *take_profits, price_tick)
    if not take_profits or any(not price.is_finite() or price <= 0 for price in prices):
        raise ValueError("protection prices must be finite positive with take profits")
    rounded_entries = (
        round_price_to_tick(entry, price_tick),
        round_price_to_tick(canonical_entry, price_tick),
    )
    stop = round_price_to_tick(stop_loss, price_tick)
    targets = tuple(round_price_to_tick(target, price_tick) for target in take_profits)
    sign = Decimal("1") if direction is Direction.LONG else Decimal("-1")
    for rounded_entry in rounded_entries:
        if (rounded_entry - stop) * sign < price_tick:
            raise ValueError("rounded stop loss must clear entry by at least one tick")
        if any((target - rounded_entry) * sign < price_tick for target in targets):
            raise ValueError("rounded take profits must clear entry by at least one tick")
    if len(set(targets)) != len(targets):
        raise ValueError("rounded take profits must remain separated by at least one tick")
    return stop, targets


@dataclass(frozen=True)
class BitgetEntrySubmission:
    """One admitted entry; values cannot be changed between sizing and POST."""

    quantity: Decimal
    effective_leverage: int
    planned_margin_usdt: Decimal
    planned_notional_usdt: Decimal
    margin_mode: str
    balance_snapshot_id: UUID
    margin_reservation_id: UUID
    observed_at: datetime
    planned_stop_loss: Decimal
    planned_take_profits: tuple[Decimal, ...]
    entry_route: EntryRoute | None = None

    def __post_init__(self) -> None:
        for field in ("quantity", "planned_margin_usdt", "planned_notional_usdt"):
            value = getattr(self, field)
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{field} must be a finite positive Decimal")
        if not self.planned_take_profits or any(
            not price.is_finite() or price <= 0
            for price in (self.planned_stop_loss, *self.planned_take_profits)
        ):
            raise ValueError("planned protection prices must be finite positive with take profits")
        if isinstance(self.effective_leverage, bool) or self.effective_leverage < 1:
            raise ValueError("effective_leverage must be a positive integer")
        normalized_mode = self.margin_mode.upper().strip()
        if normalized_mode != "ISOLATED":
            raise ValueError("Bitget entry margin_mode must be ISOLATED")
        object.__setattr__(self, "margin_mode", normalized_mode)
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
