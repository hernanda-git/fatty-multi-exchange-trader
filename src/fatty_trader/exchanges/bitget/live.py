"""Bitget live order/protection workflow (sync, protocol-injected, no network).

Orchestrator contract:
1. Pre-entry reads (balance, positions, metadata, price, position/margin/
   leverage mode) through an injected client protocol.
2. Set + read-back isolated margin mode and leverage before entry.
3. Persist the order intent BEFORE submission; entries carry a deterministic
   ``live-{exchange}-{symbol}-{uuidhex16}`` clientOid.
4. Read-back (order detail + fills) classifies accepted/partial/filled/
   rejected/unknown. Unknown results (BitgetUnknownResultError/timeout)
   reconcile by clientOid with GETs only — NEVER a blind retry POST.
5. Conditional SL+TP is installed for the ACTUAL filled qty immediately;
   unconfirmable protection triggers an emergency reduce-only market close
   plus an alert callback.
6. Avg price / fee / fill qty / provider IDs are persisted on the intent.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Protocol

from fatty_trader.domain.enums import Direction, Exchange
from fatty_trader.exchanges.bitget.client import BitgetUnknownResultError
from fatty_trader.execution.protection import (
    LiveProtectionClient,
    ProtectionPlan,
    ProtectionReport,
    ProtectionState,
    ensure_live_protection,
)
from fatty_trader.risk.sizing import SymbolMetadata

_OID_RE = re.compile(r"^[0-9a-f]{16}$")
_FILLED_STATUSES = {"filled", "full-fill", "full_fill"}
_PARTIAL_STATUSES = {"partial", "partially_filled", "partial-fill", "partial_fill"}
_ACCEPTED_STATUSES = {"new", "open", "accepted", "live", "pending", "submitting"}
_REJECTED_STATUSES = {"rejected", "cancelled", "canceled", "failed", "expired"}


class LiveOrderStatus(StrEnum):
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    FILLED = "filled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class LiveSetupError(ValueError):
    """Pre-entry venue setup (margin mode / leverage) could not be confirmed."""


class BitgetLiveClientProtocol(LiveProtectionClient, Protocol):
    """Injected Bitget live venue surface (implemented by fakes in tests)."""

    def get_available_balance(self) -> Decimal: ...
    def get_positions(self, symbol: str) -> list[dict[str, Any]]: ...
    def get_symbol_metadata(self, symbol: str) -> SymbolMetadata: ...
    def get_current_price(self, symbol: str) -> Decimal: ...
    def get_position_mode(self) -> str: ...
    def get_margin_mode(self, symbol: str) -> str: ...
    def get_leverage(self, symbol: str) -> str: ...
    def set_margin_mode(self, symbol: str, mode: str) -> None: ...
    def set_leverage(self, symbol: str, leverage: str) -> None: ...
    def place_entry_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        client_oid: str,
        order_type: str = "market",
    ) -> dict[str, Any]: ...
    def get_order_detail(self, symbol: str, client_oid: str) -> dict[str, Any]: ...
    def get_fills(self, symbol: str, client_oid: str) -> list[dict[str, Any]]: ...
    def place_market_close(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        client_oid: str,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class LiveEntryRequest:
    exchange: str = "bitget"
    symbol: str = "BTCUSDT"
    side: str = "BUY"
    quantity: Decimal = Decimal("0.01")
    leverage: int = 20
    stop_loss: Decimal = Decimal("1")
    take_profits: tuple[Decimal, ...] = ()
    client_oid: str | None = None
    oid_token: str | None = None

    def __post_init__(self) -> None:
        if self.side not in ("BUY", "SELL"):
            raise ValueError("live entry side must be BUY or SELL")
        if self.quantity <= 0:
            raise ValueError("live entry quantity must be positive")
        if self.leverage < 1:
            raise ValueError("live entry leverage must be positive")


@dataclass(frozen=True)
class LiveEntryResult:
    client_oid: str
    status: LiveOrderStatus
    filled_qty: Decimal = Decimal("0")
    avg_price: Decimal | None = None
    fee: Decimal = Decimal("0")
    provider_order_id: str | None = None
    provider_fill_ids: tuple[str, ...] = ()
    protection: ProtectionReport | None = None
    emergency_closed: bool = False


@dataclass
class LiveIntentRecord:
    exchange: str
    client_oid: str
    symbol: str
    side: str
    role: str = "ENTRY"
    state: str = "requested"
    requested_qty: Decimal = Decimal("0")
    filled_qty: Decimal = Decimal("0")
    avg_price: Decimal | None = None
    fee: Decimal = Decimal("0")
    provider_order_id: str | None = None
    provider_fill_ids: tuple[str, ...] = ()


class LiveIntentStoreProtocol(Protocol):
    def save(self, record: LiveIntentRecord) -> None: ...
    def get(self, client_oid: str) -> LiveIntentRecord | None: ...
    def update(self, record: LiveIntentRecord) -> None: ...


class InMemoryLiveIntentStore:
    """In-memory intent store (tests / wiring seam for a durable backend)."""

    def __init__(self) -> None:
        self._records: dict[str, LiveIntentRecord] = {}

    def save(self, record: LiveIntentRecord) -> None:
        existing = self._records.get(record.client_oid)
        if existing is not None:
            if existing.exchange != record.exchange:
                raise ValueError("live intent exchange conflict")
            if (
                existing.provider_order_id is not None
                and record.provider_order_id is not None
                and existing.provider_order_id != record.provider_order_id
            ):
                raise ValueError("live intent provider order id conflict")
            return
        self._records[record.client_oid] = replace(record)

    def get(self, client_oid: str) -> LiveIntentRecord | None:
        record = self._records.get(client_oid)
        return replace(record) if record is not None else None

    def update(self, record: LiveIntentRecord) -> None:
        existing = self._records.get(record.client_oid)
        if existing is None:
            raise LookupError(f"unknown live intent: {record.client_oid}")
        if existing.exchange != record.exchange:
            raise ValueError("live intent exchange conflict")
        if (
            existing.provider_order_id is not None
            and record.provider_order_id is not None
            and existing.provider_order_id != record.provider_order_id
        ):
            raise ValueError("live intent provider order id conflict")
        self._records[record.client_oid] = replace(record)


@dataclass(frozen=True)
class PreEntrySnapshot:
    available_balance: Decimal
    positions: tuple[dict[str, Any], ...] = field(default=())
    metadata: SymbolMetadata | None = None
    current_price: Decimal = Decimal("0")
    position_mode: str = ""
    margin_mode: str = ""
    leverage: str = ""


def build_live_client_oid(exchange: str, symbol: str, token_hex: str | None = None) -> str:
    """Build ``live-{exchange}-{symbol}-{uuidhex16}`` deterministically."""
    token = token_hex if token_hex is not None else uuid.uuid4().hex[:16]
    if not _OID_RE.match(token):
        raise ValueError("clientOid token must be 16 lowercase hex chars")
    return f"live-{exchange}-{symbol}-{token}"


def read_pre_entry_state(client: BitgetLiveClientProtocol, symbol: str) -> PreEntrySnapshot:
    """Perform every pre-entry read (balance, positions, metadata, price, modes)."""
    metadata = client.get_symbol_metadata(symbol)
    return PreEntrySnapshot(
        available_balance=client.get_available_balance(),
        positions=tuple(client.get_positions(symbol)),
        metadata=metadata,
        current_price=client.get_current_price(symbol),
        position_mode=client.get_position_mode(),
        margin_mode=client.get_margin_mode(symbol),
        leverage=client.get_leverage(symbol),
    )


def ensure_isolated_margin_and_leverage(
    client: BitgetLiveClientProtocol, symbol: str, leverage: int
) -> tuple[str, str]:
    """Set isolated margin mode + leverage, verifying each with a read-back."""
    client.set_margin_mode(symbol, "isolated")
    margin_mode = client.get_margin_mode(symbol)
    if margin_mode.lower() != "isolated":
        raise LiveSetupError(f"isolated margin mode not confirmed: {margin_mode!r}")
    client.set_leverage(symbol, str(leverage))
    confirmed = client.get_leverage(symbol)
    if confirmed != str(leverage):
        raise LiveSetupError(f"leverage {leverage} not confirmed: {confirmed!r}")
    return margin_mode, confirmed


def _to_decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result


def summarize_fills(
    fills: Sequence[Mapping[str, Any]],
) -> tuple[Decimal, Decimal | None, Decimal, tuple[str, ...]]:
    """Return (fill qty, weighted avg price, total fee, provider fill ids)."""
    total_qty = Decimal("0")
    notional = Decimal("0")
    total_fee = Decimal("0")
    ids: list[str] = []
    for fill in fills:
        qty = _to_decimal(fill.get("quantity", fill.get("size", 0)))
        price = _to_decimal(fill.get("price", 0))
        if qty is None or qty <= 0 or price is None or price <= 0:
            continue
        total_qty += qty
        notional += qty * price
        fee = _to_decimal(fill.get("fee", 0)) or Decimal("0")
        total_fee += fee
        raw_id = fill.get("fillId", fill.get("tradeId", fill.get("id")))
        if raw_id is not None:
            ids.append(str(raw_id))
    avg = (notional / total_qty) if total_qty > 0 else None
    return total_qty, avg, total_fee, tuple(ids)


def classify_live_order(
    detail: Mapping[str, Any], fills: Sequence[Mapping[str, Any]]
) -> LiveOrderStatus:
    """Classify an entry from its read-back detail + fills."""
    raw_status = str(detail.get("status", "")).strip().lower()
    if raw_status in _REJECTED_STATUSES:
        return LiveOrderStatus.REJECTED
    requested = _to_decimal(detail.get("requestedQty", detail.get("size", 0)))
    filled_qty, _, _, _ = summarize_fills(fills)
    if raw_status in _FILLED_STATUSES:
        return LiveOrderStatus.FILLED
    if raw_status in _PARTIAL_STATUSES:
        if requested is not None and requested > 0 and filled_qty >= requested:
            return LiveOrderStatus.FILLED
        return LiveOrderStatus.PARTIAL if filled_qty > 0 else LiveOrderStatus.ACCEPTED
    if requested is not None and requested > 0 and filled_qty >= requested:
        return LiveOrderStatus.FILLED
    if filled_qty > 0:
        return LiveOrderStatus.PARTIAL
    if raw_status in _ACCEPTED_STATUSES or raw_status == "":
        return LiveOrderStatus.ACCEPTED
    return LiveOrderStatus.UNKNOWN


def _persist_readback(
    store: LiveIntentStoreProtocol,
    record: LiveIntentRecord,
    detail: Mapping[str, Any],
    fills: Sequence[Mapping[str, Any]],
) -> LiveOrderStatus:
    status = classify_live_order(detail, fills)
    filled_qty, avg_price, fee, fill_ids = summarize_fills(fills)
    provider_order_id = detail.get("orderId", detail.get("providerOrderId"))
    # Preserve the submitted provider id: read-back details may omit it.
    if provider_order_id is not None:
        record.provider_order_id = str(provider_order_id)
    record.provider_fill_ids = fill_ids
    record.filled_qty = filled_qty
    record.avg_price = avg_price
    record.fee = fee
    record.state = {
        LiveOrderStatus.FILLED: "filled",
        LiveOrderStatus.PARTIAL: "acknowledged",
        LiveOrderStatus.ACCEPTED: "acknowledged",
        LiveOrderStatus.REJECTED: "rejected",
        LiveOrderStatus.UNKNOWN: "unknown",
    }[status]
    store.update(record)
    return status


def _install_protection(
    client: BitgetLiveClientProtocol,
    request: LiveEntryRequest,
    client_oid: str,
    filled_qty: Decimal,
    alert: Callable[[str], None] | None,
) -> tuple[ProtectionReport | None, bool]:
    """Install SL/TP for the actual filled qty; emergency-close when unconfirmable."""
    direction = Direction.LONG if request.side == "BUY" else Direction.SHORT
    plan = ProtectionPlan(
        exchange=Exchange.BITGET,
        symbol=request.symbol,
        direction=direction,
        quantity=filled_qty,
        stop_loss=request.stop_loss,
        take_profits=request.take_profits,
    )
    report = ensure_live_protection(client, plan, client_oid=client_oid)
    if report.state is ProtectionState.VENUE_PROTECTED:
        return report, False
    close_side = "SELL" if request.side == "BUY" else "BUY"
    client.place_market_close(
        symbol=request.symbol,
        side=close_side,
        quantity=filled_qty,
        client_oid=f"{client_oid}-emergency",
    )
    if alert is not None:
        alert(f"live protection unconfirmed for {client_oid}; emergency close sent")
    return report, True


def reconcile_by_client_oid(
    client: BitgetLiveClientProtocol,
    store: LiveIntentStoreProtocol,
    *,
    exchange: str,
    client_oid: str,
) -> LiveEntryResult:
    """GET-only reconcile of a submitted intent (never POSTs)."""
    record = store.get(client_oid)
    if record is None:
        raise LookupError(f"unknown live intent: {client_oid}")
    detail = client.get_order_detail(record.symbol, client_oid)
    fills = client.get_fills(record.symbol, client_oid)
    status = _persist_readback(store, record, detail, fills)
    return LiveEntryResult(
        client_oid=client_oid,
        status=status,
        filled_qty=record.filled_qty,
        avg_price=record.avg_price,
        fee=record.fee,
        provider_order_id=record.provider_order_id,
        provider_fill_ids=record.provider_fill_ids,
    )


def enter_live_position(
    client: BitgetLiveClientProtocol,
    store: LiveIntentStoreProtocol,
    request: LiveEntryRequest,
    *,
    alert: Callable[[str], None] | None = None,
) -> LiveEntryResult:
    """Run the live entry workflow: setup, intent-first POST, read-back, protect."""
    read_pre_entry_state(client, request.symbol)
    ensure_isolated_margin_and_leverage(client, request.symbol, request.leverage)

    client_oid = request.client_oid or build_live_client_oid(
        request.exchange, request.symbol, request.oid_token
    )
    existing = store.get(client_oid)
    # Any existing durable intent may have reached Bitget before a crash. Reconcile
    # via GET only; never submit a second POST for the same client OID.
    if existing is not None:
        result = reconcile_by_client_oid(
            client, store, exchange=request.exchange, client_oid=client_oid
        )
        return _maybe_protect(client, store, request, client_oid, result, alert)

    record = existing or LiveIntentRecord(
        exchange=request.exchange,
        client_oid=client_oid,
        symbol=request.symbol,
        side=request.side,
        requested_qty=request.quantity,
    )
    if existing is None:
        store.save(record)  # persist intent BEFORE submission

    try:
        submitted = client.place_entry_order(
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            client_oid=client_oid,
        )
    except (BitgetUnknownResultError, TimeoutError):
        record.state = "unknown"
        store.update(record)
        result = reconcile_by_client_oid(
            client, store, exchange=request.exchange, client_oid=client_oid
        )
        return _maybe_protect(client, store, request, client_oid, result, alert)
    provider_order_id = submitted.get("orderId")
    record.provider_order_id = str(provider_order_id) if provider_order_id is not None else None
    store.update(record)

    detail = client.get_order_detail(record.symbol, client_oid)
    fills = client.get_fills(record.symbol, client_oid)
    status = _persist_readback(store, record, detail, fills)
    result = LiveEntryResult(
        client_oid=client_oid,
        status=status,
        filled_qty=record.filled_qty,
        avg_price=record.avg_price,
        fee=record.fee,
        provider_order_id=record.provider_order_id,
        provider_fill_ids=record.provider_fill_ids,
    )
    return _maybe_protect(client, store, request, client_oid, result, alert)


def _maybe_protect(
    client: BitgetLiveClientProtocol,
    store: LiveIntentStoreProtocol,
    request: LiveEntryRequest,
    client_oid: str,
    result: LiveEntryResult,
    alert: Callable[[str], None] | None,
) -> LiveEntryResult:
    if result.filled_qty <= 0 or result.status not in (
        LiveOrderStatus.FILLED,
        LiveOrderStatus.PARTIAL,
    ):
        return result
    report, emergency_closed = _install_protection(
        client, request, client_oid, result.filled_qty, alert
    )
    record = store.get(client_oid)
    if record is not None:
        record.state = "reconciled" if emergency_closed else record.state
        store.update(record)
    return LiveEntryResult(
        client_oid=result.client_oid,
        status=result.status,
        filled_qty=result.filled_qty,
        avg_price=result.avg_price,
        fee=result.fee,
        provider_order_id=result.provider_order_id,
        provider_fill_ids=result.provider_fill_ids,
        protection=report,
        emergency_closed=emergency_closed,
    )
