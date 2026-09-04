from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol


class ReconcilerStatus(StrEnum):
    STARTING = "starting"
    OK = "ok"
    DEGRADED = "degraded"
    KILLED = "killed"


class KillSwitchTrigger(Exception):
    STALE_RECONCILIATION = "stale_reconciliation"
    MISSING_PROTECTION = "missing_protection"
    WRONG_MARGIN_MODE = "wrong_margin_mode"
    UNEXPECTED_ERROR = "unexpected_error"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"kill switch: {reason}")


@dataclass(frozen=True)
class PnL:
    realized_profit: Decimal = Decimal("0")
    realized_loss: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")

    @property
    def net_pnl(self) -> Decimal:
        return self.realized_profit - self.realized_loss - self.fees + self.unrealized_pnl


@dataclass
class ReconcilerConfig:
    stale_threshold_seconds: float = 300.0
    kill_on_missing_protection: bool = True
    expected_margin_mode: str = "isolated"
    symbol: str = "BTCUSDT"


class ReconcilerClient(Protocol):
    def get_available_balance(self) -> Decimal: ...
    def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]: ...
    def get_orders(self, symbol: str | None = None) -> list[dict[str, Any]]: ...
    def get_fills(self, symbol: str | None = None) -> list[dict[str, Any]]: ...
    def get_current_price(self, symbol: str) -> Decimal: ...
    def get_margin_mode(self, symbol: str) -> str: ...
    def get_leverage(self, symbol: str) -> str: ...


@dataclass
class Reconciler:
    """Reconcile local state against Bitget; fire kill switch on anomalies."""

    client: ReconcilerClient
    config: ReconcilerConfig = field(default_factory=ReconcilerConfig)
    status: ReconcilerStatus = ReconcilerStatus.STARTING
    last_mismatches: list[str] = field(default_factory=list)
    last_mismatch_count: int = 0
    _last_tick_seconds: float = 0.0
    _status_callbacks: list[Callable[[ReconcilerStatus], None]] = field(default_factory=list)
    _known_order_ids: set[str] = field(default_factory=set)

    def on_status_change(self, callback: Callable[[ReconcilerStatus], None]) -> None:
        self._status_callbacks.append(callback)

    def register_known_order(self, order_id: str) -> None:
        self._known_order_ids.add(order_id)

    def _set_status(self, new: ReconcilerStatus) -> None:
        if new is not self.status:
            self.status = new
            for cb in self._status_callbacks:
                cb(new)

    def tick(self) -> None:
        """Run one reconciliation pass. May raise KillSwitchTrigger."""
        now = time.time()
        stale = now - self._last_tick_seconds
        if self._last_tick_seconds > 0 and stale > self.config.stale_threshold_seconds:
            self._set_status(ReconcilerStatus.KILLED)
            raise KillSwitchTrigger(KillSwitchTrigger.STALE_RECONCILIATION)

        try:
            mismatches = self._reconcile()
        except KillSwitchTrigger:
            self._set_status(ReconcilerStatus.KILLED)
            raise
        except Exception:
            self._set_status(ReconcilerStatus.DEGRADED)
            self.last_mismatches = ["reconciliation failed"]
            self.last_mismatch_count = 1
            self._last_tick_seconds = now
            return

        self.last_mismatches = mismatches
        self.last_mismatch_count = len(mismatches)
        if mismatches:
            self._set_status(ReconcilerStatus.DEGRADED)
        else:
            self._set_status(ReconcilerStatus.OK)
        self._last_tick_seconds = now

    def _reconcile(self) -> list[str]:
        mismatches: list[str] = []

        margin_mode = self.client.get_margin_mode(self.config.symbol)
        if margin_mode.lower() != self.config.expected_margin_mode:
            reason = f"wrong margin mode: {margin_mode}"
            if self.config.kill_on_missing_protection:
                raise KillSwitchTrigger(reason)
            mismatches.append(reason)

        positions = self.client.get_positions()
        orders = self.client.get_orders()
        fills = self.client.get_fills()
        known_order_ids = {
            o.get("order_id") for o in orders if o.get("order_id")
        } | self._known_order_ids

        for order in orders:
            oid = order.get("order_id")
            if oid and oid not in self._known_order_ids:
                mismatches.append(f"unknown order {oid}")

        for pos in positions:
            pos_id = pos.get("position_id") or pos.get("symbol")
            has_sl = any(
                o.get("role") == "SL" and o.get("symbol") == pos.get("symbol") for o in orders
            )
            has_tp = any(
                o.get("role") == "TP" and o.get("symbol") == pos.get("symbol") for o in orders
            )
            if not has_sl or not has_tp:
                mismatches.append(f"protection missing for position {pos_id}")

        self._check_protection_or_kill(mismatches)

        for fill in fills:
            fid = fill.get("order_id") or fill.get("orderId")
            if fid and fid not in known_order_ids:
                mismatches.append(f"unknown order {fid} in fills")

        return mismatches

    def _check_protection_or_kill(self, mismatches: list[str]) -> None:
        if self.config.kill_on_missing_protection and any(
            "protection" in m.lower() for m in mismatches
        ):
            raise KillSwitchTrigger(KillSwitchTrigger.MISSING_PROTECTION)
