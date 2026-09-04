from __future__ import annotations

from collections.abc import Sequence
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


@dataclass(frozen=True)
class ProtectionConfirmation:
    """Outcome of a live SL/TP placement before venue read-back."""

    sl_order_id: str | None
    tp_order_ids: tuple[str, ...] = ()
    confirmed: bool = False


class LiveProtectionClient(Protocol):
    """Venue surface needed to confirm live SL/TP state (fake-injectable)."""

    def place_protection_orders(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        stop_loss: Decimal,
        take_profits: Sequence[Decimal],
        client_oid: str,
    ) -> ProtectionConfirmation: ...

    def read_protection_state(
        self,
        *,
        symbol: str,
        sl_order_id: str | None,
        tp_order_ids: Sequence[str],
    ) -> ProtectionReport: ...


def ensure_live_protection(
    client: LiveProtectionClient, plan: ProtectionPlan, *, client_oid: str
) -> ProtectionReport:
    """Place live SL/TP for ``plan`` and confirm venue state explicitly.

    Never claims VENUE_PROTECTED without a venue read-back: an
    unconfirmed placement degrades to FAILED so the caller can trigger
    the emergency reduce-only close path.
    """
    side = "SELL" if plan.direction is Direction.LONG else "BUY"
    confirmation = client.place_protection_orders(
        symbol=plan.symbol,
        side=side,
        quantity=plan.quantity,
        stop_loss=plan.stop_loss,
        take_profits=plan.take_profits,
        client_oid=client_oid,
    )
    if not confirmation.confirmed or confirmation.sl_order_id is None:
        return ProtectionReport(ProtectionState.FAILED, Decimal("0"), "live protection unconfirmed")
    report = client.read_protection_state(
        symbol=plan.symbol,
        sl_order_id=confirmation.sl_order_id,
        tp_order_ids=confirmation.tp_order_ids,
    )
    if report.observed_quantity < 0:
        raise ValueError("venue returned a negative protected quantity")
    if report.state is not ProtectionState.VENUE_PROTECTED:
        return ProtectionReport(
            ProtectionState.FAILED,
            report.observed_quantity,
            report.reason or "live protection not venue-confirmed",
        )
    return report


def reconcile_live_protection(
    client: LiveProtectionClient,
    plan: ProtectionPlan,
    *,
    sl_order_id: str | None,
    tp_order_ids: Sequence[str] = (),
) -> ProtectionReport:
    """Re-read live venue protection without placing new orders.

    Read-only: never upgrades a degraded/failed venue answer locally.
    """
    report = client.read_protection_state(
        symbol=plan.symbol, sl_order_id=sl_order_id, tp_order_ids=tp_order_ids
    )
    if report.observed_quantity < 0:
        raise ValueError("venue returned a negative protected quantity")
    return report
