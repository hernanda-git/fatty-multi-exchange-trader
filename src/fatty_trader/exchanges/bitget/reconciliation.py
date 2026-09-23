"""GET-only reconciliation of ambiguous Bitget intents."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from fatty_trader.exchanges.bitget.client import BitgetApiError
from fatty_trader.exchanges.bitget.live import (
    LiveIntentRecord,
    LiveOrderStatus,
    classify_live_order,
    normalize_fill,
    summarize_fills,
)


@dataclass(frozen=True)
class AmbiguousOrderResult:
    status: LiveOrderStatus
    filled_qty: Decimal
    avg_price: Decimal | None
    fee: Decimal
    provider_order_id: str | None
    provider_fill_ids: tuple[str, ...]
    provider_fills: tuple[dict[str, Any], ...]


def _complete_fills(value: Any) -> tuple[list[dict[str, Any]], bool]:
    if isinstance(value, list):
        return value, all(isinstance(fill, dict) for fill in value)
    if isinstance(value, dict):
        rows = value.get("fillList")
        if isinstance(rows, list) and all(isinstance(fill, dict) for fill in rows):
            return rows, not value.get("endId")
    return [], False


def _flat_position(value: Any, symbol: str) -> bool | None:
    if isinstance(value, dict):
        value = value.get("data", value.get("positionList"))
    if not isinstance(value, list):
        return None
    for row in value:
        if not isinstance(row, dict):
            return None
        if row.get("symbol", symbol) != symbol:
            continue
        raw = next((row.get(key) for key in ("total", "size", "quantity") if key in row), None)
        if raw is None:
            return None
        try:
            if abs(Decimal(str(raw))) > 0:
                return False
        except (ArithmeticError, TypeError, ValueError):
            return None
    return True


def _no_pending_orders(value: Any, symbol: str) -> bool | None:
    if isinstance(value, dict):
        value = value.get("entrustedList", value.get("data"))
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        return None
    return not any(row.get("symbol", symbol) == symbol for row in value)


async def classify_missing_detail(
    intent: LiveIntentRecord,
    *,
    read_fills: Callable[[str], Awaitable[Any]],
    read_position: Callable[[str], Awaitable[Any]],
    read_pending_orders: Callable[[str], Awaitable[Any]],
    provider_order_id: str | None = None,
    missing_order_confirmed: bool = False,
) -> AmbiguousOrderResult:
    """Classify missing detail using only complete, consistent GET readbacks."""
    try:
        fills, complete = _complete_fills(await read_fills(intent.symbol))
        position = await read_position(intent.symbol)
        pending = await read_pending_orders(intent.symbol)
    except Exception:
        fills, complete, position, pending = [], False, None, None
    matching = [
        normalize_fill(fill)
        for fill in fills
        if fill.get("clientOid", fill.get("client_oid")) == intent.client_oid
        or (
            provider_order_id is not None
            and str(fill.get("orderId", fill.get("order_id"))) == provider_order_id
        )
    ]
    filled_qty, avg_price, fee, fill_ids = summarize_fills(matching)
    flat = _flat_position(position, intent.symbol) is True
    no_pending = _no_pending_orders(pending, intent.symbol) is True
    safe = complete and flat and no_pending
    status = (
        LiveOrderStatus.FILLED
        if safe and filled_qty >= intent.requested_qty
        else LiveOrderStatus.PARTIAL
        if safe and filled_qty > 0
        else LiveOrderStatus.REJECTED
        if safe and missing_order_confirmed
        else LiveOrderStatus.UNKNOWN
    )
    return AmbiguousOrderResult(
        status,
        filled_qty,
        avg_price,
        fee,
        provider_order_id,
        fill_ids,
        tuple(matching),
    )


async def reconcile_unknown_intent(
    intent: LiveIntentRecord,
    *,
    read_order_detail: Callable[[str, str], Awaitable[Any]],
    read_fills: Callable[[str], Awaitable[Any]],
    read_position: Callable[[str], Awaitable[Any]],
    read_pending_orders: Callable[[str], Awaitable[Any]],
) -> LiveIntentRecord:
    """Refresh one durable unknown intent using provider GET reads only."""
    detail_not_found = False
    try:
        detail = await read_order_detail(intent.symbol, intent.client_oid)
    except BitgetApiError as exc:
        if exc.code == "40109" or "cannot be found" in str(exc).lower():
            detail = {}
            detail_not_found = True
        else:
            raise
    if not isinstance(detail, dict):
        raise ValueError("provider-order-detail-invalid")
    provider_order_id = detail.get("orderId", detail.get("providerOrderId"))
    if not detail:
        outcome = await classify_missing_detail(
            intent,
            read_fills=read_fills,
            read_position=read_position,
            read_pending_orders=read_pending_orders,
            provider_order_id=(
                str(provider_order_id)
                if provider_order_id is not None
                else intent.provider_order_id
            ),
            missing_order_confirmed=detail_not_found,
        )
        intent.filled_qty = outcome.filled_qty
        intent.avg_price = outcome.avg_price
        intent.fee = outcome.fee
        intent.provider_fill_ids = outcome.provider_fill_ids
        intent.provider_fills = outcome.provider_fills
        intent.provider_order_id = outcome.provider_order_id or intent.provider_order_id
        intent.state = {
            LiveOrderStatus.FILLED: "filled",
            LiveOrderStatus.PARTIAL: "partially_filled",
            LiveOrderStatus.REJECTED: "rejected",
            LiveOrderStatus.UNKNOWN: "unknown",
        }[outcome.status]
        return intent
    fills = await read_fills(intent.symbol)
    if isinstance(fills, dict):
        fills = fills.get("fillList", [])
    if not isinstance(fills, list) or not all(isinstance(fill, dict) for fill in fills):
        raise ValueError("provider-fills-invalid")
    if provider_order_id is not None:
        intent.provider_order_id = str(provider_order_id)
    matching_fills = [
        normalize_fill(fill)
        for fill in fills
        if fill.get("clientOid", fill.get("client_oid")) == intent.client_oid
        or (
            intent.provider_order_id is not None
            and str(fill.get("orderId", fill.get("order_id"))) == intent.provider_order_id
        )
    ]
    filled_qty, avg_price, fee, fill_ids = summarize_fills(matching_fills)
    intent.filled_qty = filled_qty
    intent.avg_price = avg_price
    intent.fee = fee
    intent.provider_fill_ids = fill_ids
    intent.provider_fills = tuple(matching_fills)
    if not detail:
        status = (
            LiveOrderStatus.FILLED
            if filled_qty >= intent.requested_qty
            else LiveOrderStatus.PARTIAL
            if filled_qty > 0
            else LiveOrderStatus.UNKNOWN
        )
    else:
        status = classify_live_order(detail, matching_fills)
        if status is LiveOrderStatus.FILLED and filled_qty <= 0:
            status = LiveOrderStatus.UNKNOWN
    intent.state = {
        LiveOrderStatus.ACCEPTED: "acknowledged",
        LiveOrderStatus.PARTIAL: "partially_filled",
        LiveOrderStatus.FILLED: "filled",
        LiveOrderStatus.REJECTED: "rejected",
        LiveOrderStatus.UNKNOWN: "unknown",
    }[status]
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
