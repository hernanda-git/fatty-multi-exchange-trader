"""Pure, fail-closed routing for signal entries affected by front-running."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from fatty_trader.domain.enums import Direction


class EntryMode(StrEnum):
    LIMIT_ONLY = "LIMIT_ONLY"
    FULL_MARKET = "FULL_MARKET"
    SPLIT_MARKET_LIMIT = "SPLIT_MARKET_LIMIT"
    NEAR_LIMIT_SPLIT_MARKET_LIMIT = "NEAR_LIMIT_SPLIT_MARKET_LIMIT"


@dataclass(frozen=True)
class LimitEntryContext:
    """Evidence that a provider-acknowledged limit recently moved away from entry."""

    provider_acknowledged: bool
    was_working: bool
    observed_at: datetime
    near_entry_mark: Decimal | None
    prior_mark: Decimal | None
    departure_window_seconds: int = 60

    def is_recent(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        observed = self.observed_at
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        age = (current - observed).total_seconds()
        return 0 <= age <= self.departure_window_seconds


@dataclass(frozen=True)
class EntryRoute:
    mode: EntryMode
    market_quantity: Decimal
    limit_quantity: Decimal
    limit_price: Decimal | None
    reason: str = "legacy-price-route"


def _split(
    total_quantity: Decimal, *, mode: EntryMode, limit_price: Decimal, reason: str
) -> EntryRoute:
    market_quantity = total_quantity * Decimal("0.25")
    limit_quantity = total_quantity - market_quantity
    return EntryRoute(mode, market_quantity, limit_quantity, limit_price, reason)


def route_entry(
    *,
    direction: Direction,
    signal_entry: Decimal,
    market_price: Decimal,
    total_quantity: Decimal,
    late_threshold_pct: Decimal,
    limit_context: LimitEntryContext | None = None,
    near_limit_threshold_pct: Decimal | None = None,
    now: datetime | None = None,
) -> EntryRoute:
    """Route entry while requiring lifecycle evidence for near-limit splitting.

    The legacy price-only behavior is preserved when ``limit_context`` is omitted.
    Production callers should provide context so a normal pullback wait cannot be
    mistaken for a limit that has just departed.
    """
    if signal_entry <= 0 or market_price <= 0 or total_quantity <= 0:
        raise ValueError("entry, market price, and quantity must be positive")
    if not Decimal("0") < late_threshold_pct < Decimal("1"):
        raise ValueError("late threshold must be between zero and one")
    near_threshold = near_limit_threshold_pct or late_threshold_pct
    if not Decimal("0") < near_threshold < Decimal("1"):
        raise ValueError("near-limit threshold must be between zero and one")

    if limit_context is not None:
        if limit_context.departure_window_seconds <= 0:
            raise ValueError("departure window must be positive")
        eligible = (
            limit_context.provider_acknowledged
            and limit_context.was_working
            and limit_context.near_entry_mark is not None
            and limit_context.prior_mark is not None
            and limit_context.is_recent(now=now)
        )
        if eligible:
            prior_mark = limit_context.prior_mark
            near_mark = limit_context.near_entry_mark
            if prior_mark is None or near_mark is None:
                return EntryRoute(
                    EntryMode.LIMIT_ONLY,
                    Decimal("0"),
                    total_quantity,
                    signal_entry,
                    "normal-pullback-or-insufficient-limit-evidence",
                )
            near_entry = signal_entry * (Decimal("1") + near_threshold)
            if direction is Direction.LONG:
                just_departed = (
                    signal_entry * (Decimal("1") - near_threshold) <= near_mark <= near_entry
                    and prior_mark <= near_entry
                    and market_price > signal_entry
                    and market_price <= near_entry
                )
            else:
                near_entry = signal_entry * (Decimal("1") - near_threshold)
                just_departed = (
                    near_entry <= near_mark <= signal_entry
                    and prior_mark >= near_entry
                    and market_price < signal_entry
                    and market_price >= near_entry
                )
            if just_departed:
                return _split(
                    total_quantity,
                    mode=EntryMode.NEAR_LIMIT_SPLIT_MARKET_LIMIT,
                    limit_price=signal_entry,
                    reason="just-departed-acknowledged-limit",
                )
        return EntryRoute(
            EntryMode.LIMIT_ONLY,
            Decimal("0"),
            total_quantity,
            signal_entry,
            "normal-pullback-or-insufficient-limit-evidence",
        )

    adverse_move = (
        market_price >= signal_entry * (Decimal("1") + late_threshold_pct)
        if direction is Direction.LONG
        else market_price <= signal_entry * (Decimal("1") - late_threshold_pct)
    )
    if not adverse_move:
        return EntryRoute(EntryMode.FULL_MARKET, total_quantity, Decimal("0"), None)
    return _split(
        total_quantity,
        mode=EntryMode.SPLIT_MARKET_LIMIT,
        limit_price=signal_entry,
        reason="legacy-price-route",
    )
