"""GET-only reconciliation of ambiguous Bitget intents."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from fatty_trader.exchanges.bitget.live import (
    LiveIntentRecord,
    classify_live_order,
    summarize_fills,
)


async def reconcile_unknown_intent(
    intent: LiveIntentRecord,
    *,
    read_order_detail: Callable[[str, str], Awaitable[Any]],
    read_fills: Callable[[str], Awaitable[Any]],
) -> LiveIntentRecord:
    """Refresh one durable unknown intent using provider GET reads only."""
    detail = await read_order_detail(intent.symbol, intent.client_oid)
    fills = await read_fills(intent.symbol)
    if not isinstance(detail, dict):
        raise ValueError("provider-order-detail-invalid")
    if not isinstance(fills, list) or not all(isinstance(fill, dict) for fill in fills):
        raise ValueError("provider-fills-invalid")
    filled_qty, avg_price, fee, fill_ids = summarize_fills(fills)
    intent.provider_order_id = (
        str(detail["orderId"]) if detail.get("orderId") is not None else intent.provider_order_id
    )
    intent.filled_qty = filled_qty
    intent.avg_price = avg_price
    intent.fee = fee
    intent.provider_fill_ids = fill_ids
    intent.state = {
        "accepted": "acknowledged",
        "partial": "acknowledged",
        "filled": "filled",
        "rejected": "rejected",
        "unknown": "unknown",
    }[classify_live_order(detail, fills).value]
    return intent


class ReconcilerStatus(StrEnum):
    STARTING = "starting"
    OK = "ok"
    DEGRADED = "degraded"
    KILLED = "killed"


class KillSwitchTrigger(Exception):
    STALE_RECONCILIATION = "stale_reconciliation"
    MISSING_PROTECTION = "missing_protection"
    WRONG_MARGIN_MODE = "wrong_margin_mode"

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
    def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]: ...
    def get_orders(self, symbol: str | None = None) -> list[dict[str, Any]]: ...
    def get_fills(self, symbol: str | None = None) -> list[dict[str, Any]]: ...
    def get_margin_mode(self, symbol: str) -> str: ...


@dataclass
class Reconciler:
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

    def _set_status(self, state: ReconcilerStatus) -> None:
        if state is not self.status:
            self.status = state
            for callback in self._status_callbacks:
                callback(state)

    def tick(self) -> None:
        now = time.time()
        if (
            self._last_tick_seconds
            and now - self._last_tick_seconds > self.config.stale_threshold_seconds
        ):
            self._set_status(ReconcilerStatus.KILLED)
            raise KillSwitchTrigger(KillSwitchTrigger.STALE_RECONCILIATION)
        try:
            mismatches = self._reconcile()
        except KillSwitchTrigger:
            self._set_status(ReconcilerStatus.KILLED)
            raise
        except Exception:
            self._set_status(ReconcilerStatus.DEGRADED)
            self.last_mismatches, self.last_mismatch_count = ["reconciliation failed"], 1
            self._last_tick_seconds = now
            return
        self.last_mismatches, self.last_mismatch_count = mismatches, len(mismatches)
        self._set_status(ReconcilerStatus.DEGRADED if mismatches else ReconcilerStatus.OK)
        self._last_tick_seconds = now

    def _reconcile(self) -> list[str]:
        mismatches: list[str] = []
        margin_mode = self.client.get_margin_mode(self.config.symbol)
        if margin_mode.lower() != self.config.expected_margin_mode:
            reason = f"wrong margin mode: {margin_mode}"
            if self.config.kill_on_missing_protection:
                raise KillSwitchTrigger(reason)
            mismatches.append(reason)
        positions, orders, fills = (
            self.client.get_positions(),
            self.client.get_orders(),
            self.client.get_fills(),
        )
        for order in orders:
            oid = order.get("order_id")
            if oid and oid not in self._known_order_ids:
                mismatches.append(f"unknown order {oid}")
        for position in positions:
            symbol = position.get("symbol")
            roles = {order.get("role") for order in orders if order.get("symbol") == symbol}
            if not {"SL", "TP"}.issubset(roles):
                mismatches.append(f"protection missing for position {symbol}")
        self._check_protection_or_kill(mismatches)
        known = {order.get("order_id") for order in orders} | self._known_order_ids
        for fill in fills:
            oid = fill.get("order_id") or fill.get("orderId")
            if oid and oid not in known:
                mismatches.append(f"unknown order {oid} in fills")
        return mismatches

    def _check_protection_or_kill(self, mismatches: list[str]) -> None:
        if self.config.kill_on_missing_protection and any(
            "protection" in item for item in mismatches
        ):
            raise KillSwitchTrigger(KillSwitchTrigger.MISSING_PROTECTION)
