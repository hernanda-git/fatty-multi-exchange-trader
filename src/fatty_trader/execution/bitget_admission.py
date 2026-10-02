"""Immutable evidence handed from Bitget admission to entry execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


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

    def __post_init__(self) -> None:
        for field in ("quantity", "planned_margin_usdt", "planned_notional_usdt"):
            value = getattr(self, field)
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{field} must be a finite positive Decimal")
        if isinstance(self.effective_leverage, bool) or self.effective_leverage < 1:
            raise ValueError("effective_leverage must be a positive integer")
        normalized_mode = self.margin_mode.upper().strip()
        if normalized_mode != "ISOLATED":
            raise ValueError("Bitget entry margin_mode must be ISOLATED")
        object.__setattr__(self, "margin_mode", normalized_mode)
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
