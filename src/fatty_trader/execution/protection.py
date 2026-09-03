from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from fatty_trader.domain.enums import Direction, Exchange


class ProtectionState(StrEnum):
    PENDING = "PENDING"
    VENUE_PROTECTED = "VENUE_PROTECTED"
    BOT_FALLBACK = "BOT_FALLBACK"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ProtectionPlan:
    exchange: Exchange
    symbol: str
    direction: Direction
    quantity: Decimal
    stop_loss: Decimal
    take_profits: tuple[Decimal, ...] = ()

    def __post_init__(self) -> None:
        if self.quantity <= 0 or self.stop_loss <= 0:
            raise ValueError("protection quantity and stop loss must be positive")
        if not self.symbol:
            raise ValueError("protection symbol is required")


@dataclass(frozen=True)
class ProtectionReport:
    state: ProtectionState
    observed_quantity: Decimal
    reason: str | None = None


class ProtectionAdapter(Protocol):
    def ensure_protection(self, plan: ProtectionPlan) -> ProtectionReport: ...
    def reconcile_protection(self, plan: ProtectionPlan) -> ProtectionReport: ...


def reconcile_protection(adapter: ProtectionAdapter, plan: ProtectionPlan) -> ProtectionReport:
    """Re-read venue protection and never upgrade a degraded/failed result locally."""
    report = adapter.reconcile_protection(plan)
    if report.observed_quantity < 0:
        raise ValueError("venue returned a negative protected quantity")
    return report
