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

import json
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

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
    provider_fills: tuple[dict[str, Any], ...] = ()
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
    provider_fills: tuple[dict[str, Any], ...] = ()
    planned_leverage: int | None = None
    planned_margin_usdt: Decimal | None = None
    planned_notional_usdt: Decimal | None = None
    margin_mode: str | None = None
    balance_snapshot_id: UUID | None = None
    margin_reservation_id: UUID | None = None
    planned_stop_loss: Decimal | None = None
    planned_take_profits: tuple[Decimal, ...] | None = None
    dispatch_id: UUID | None = None
    entry_leg: str | None = None
    order_type: str = "market"
    limit_price: Decimal | None = None
    cancel_requested_by: UUID | None = None
    provider_terminal: bool = False
    cancel_post_claimed: bool = False
    active_stop_loss_price: Decimal | None = None
    stop_mutation_root_oid: str | None = None
    stop_mutation_take_profit_id: str | None = None
    protection_root_oid: str | None = None

    def __post_init__(self) -> None:
        if self.role != "ENTRY":
            return
        evidence = (
            self.planned_leverage,
            self.planned_margin_usdt,
            self.margin_mode,
            self.balance_snapshot_id,
            self.margin_reservation_id,
        )
        if any(value is not None for value in evidence) and any(
            value is None for value in evidence
        ):
            raise ValueError("ENTRY intent admission evidence must be complete")
        if self.planned_leverage is not None and self.planned_leverage < 1:
            raise ValueError("ENTRY planned leverage must be positive")
        if self.planned_margin_usdt is not None and self.planned_margin_usdt <= 0:
            raise ValueError("ENTRY planned margin must be positive")
        if self.margin_mode is not None and self.margin_mode.upper() != "ISOLATED":
            raise ValueError("ENTRY margin mode must be ISOLATED")
        if (self.planned_stop_loss is None) != (self.planned_take_profits is None):
            raise ValueError("ENTRY intent protection plan must be complete")
        if self.planned_stop_loss is not None and (
            not self.planned_take_profits
            or any(
                not price.is_finite() or price <= 0
                for price in (self.planned_stop_loss, *self.planned_take_profits)
            )
        ):
            raise ValueError("ENTRY planned protection prices must be finite positive")
        if self.order_type not in {"market", "limit"}:
            raise ValueError("ENTRY order type must be market or limit")
        if self.order_type == "limit" and (
            self.limit_price is None or not self.limit_price.is_finite() or self.limit_price <= 0
        ):
            raise ValueError("ENTRY limit price must be finite positive")


class LiveIntentStoreProtocol(Protocol):
    def claim(self, record: LiveIntentRecord) -> bool: ...

    def save(self, record: LiveIntentRecord) -> None: ...
    def get(self, client_oid: str) -> LiveIntentRecord | None: ...
    def update(self, record: LiveIntentRecord) -> None: ...
    def record_fills(self, record: LiveIntentRecord, fills: tuple[dict[str, Any], ...]) -> None: ...
    def claim_split(self, market: LiveIntentRecord, limit: LiveIntentRecord) -> bool: ...
    def claim_entry_post(self, client_oid: str) -> bool: ...
    def pending_entries(
        self, symbol: str | None = None, management_id: UUID | None = None
    ) -> tuple[LiveIntentRecord, ...]: ...
    def claim_cancel(self, client_oid: str, management_id: UUID) -> bool: ...
    def claim_cancel_post(self, client_oid: str) -> bool: ...
    def update_active_stop(
        self, symbol: str, root_oid: str, stop: Decimal, management_id: str
    ) -> None: ...
    def remaining_owned_quantity(self, root_oid: str) -> Decimal: ...


class InMemoryLiveIntentStore:
    """In-memory intent store (tests / wiring seam for a durable backend)."""

    def __init__(self) -> None:
        self._records: dict[str, LiveIntentRecord] = {}
        self.fills: list[tuple[str, dict[str, Any]]] = []
        self._fill_keys: set[tuple[str, str]] = set()
        self.provider_events: list[dict[str, str]] = []
        self._provider_event_keys: set[tuple[str, str]] = set()
        self.close_bindings: dict[str, str] = {}

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

    def claim(self, record: LiveIntentRecord) -> bool:
        """Insert a durable intent and report whether this caller won the claim."""
        if record.client_oid in self._records:
            self.save(record)
            return False
        self._records[record.client_oid] = replace(record)
        return True

    def get(self, client_oid: str) -> LiveIntentRecord | None:
        record = self._records.get(client_oid)
        return replace(record) if record is not None else None

    def claim_split(self, market: LiveIntentRecord, limit: LiveIntentRecord) -> bool:
        if market.client_oid in self._records:
            return False
        if limit.client_oid in self._records:
            raise ValueError("split child exists without root")
        self._records[market.client_oid] = replace(market)
        self._records[limit.client_oid] = replace(limit)
        return True

    def claim_entry_post(self, client_oid: str) -> bool:
        record = self._records[client_oid]
        if record.state != "staged" or record.cancel_requested_by is not None:
            return False
        record.state = "requested"
        return True

    def pending_entries(
        self, symbol: str | None = None, management_id: UUID | None = None
    ) -> tuple[LiveIntentRecord, ...]:
        return tuple(
            replace(record)
            for record in self._records.values()
            if record.exchange == "bitget"
            and record.role == "ENTRY"
            and record.entry_leg == "limit"
            and (
                not record.provider_terminal
                or record.cancel_requested_by == management_id
                and management_id is not None
            )
            and (symbol is None or record.symbol == symbol)
        )

    def claim_cancel(self, client_oid: str, management_id: UUID) -> bool:
        record = self._records[client_oid]
        if record.role != "ENTRY" or record.entry_leg != "limit":
            raise ValueError("cancellation requires owned entry limit")
        if record.cancel_requested_by is not None or record.provider_terminal:
            return False
        record.cancel_requested_by = management_id
        if record.state == "staged":
            record.state = "cancelled"
            record.provider_terminal = True
        return True

    def claim_cancel_post(self, client_oid: str) -> bool:
        record = self._records[client_oid]
        if (
            record.cancel_requested_by is None
            or record.cancel_post_claimed
            or record.provider_terminal
        ):
            return False
        record.cancel_post_claimed = True
        return True

    def claim_protection(self, records: tuple[LiveIntentRecord, ...]) -> bool:
        if any(record.client_oid in self._records for record in records):
            return False
        for record in records:
            self._records[record.client_oid] = replace(record)
        return True

    def protection_intents(self, root_oid: str) -> tuple[LiveIntentRecord, ...]:
        return tuple(
            replace(r)
            for r in self._records.values()
            if r.protection_root_oid == root_oid and r.role in {"SL", "TP"}
        )

    def remaining_owned_quantity(self, root_oid: str) -> Decimal:
        root = self._records[root_oid]
        child = self._records.get(root_oid + "-limit")
        total = root.filled_qty + (child.filled_qty if child is not None else Decimal("0"))
        for close_oid, owner in self.close_bindings.items():
            if owner != root_oid:
                continue
            close = self._records[close_oid]
            if close.filled_qty <= 0:
                continue
            ledger = [normalize_fill(f) for oid, f in self.fills if oid == close_oid]
            quantity, _, _, ids = summarize_fills(ledger)
            if (
                close.symbol != root.symbol
                or close.side == root.side
                or not close.provider_order_id
                or quantity != close.filled_qty
                or set(ids) != set(close.provider_fill_ids)
                or any(fid.startswith("status-derived:") for fid in ids)
            ):
                raise ValueError("bound close lacks exact real provider fill evidence")
            total -= quantity
        if total < 0:
            raise ValueError("bound closes exceed owned entry fills")
        return total

    def update_active_stop(
        self, symbol: str, root_oid: str, stop: Decimal, management_id: str
    ) -> None:
        root = self._records[root_oid]
        management = self._records[management_id]
        if (
            root.symbol != symbol
            or management.role != "SL"
            or management.stop_mutation_root_oid != root_oid
            or management.planned_stop_loss != stop
        ):
            raise ValueError("active stop management ownership mismatch")
        root.active_stop_loss_price = stop
        child = self._records.get(root_oid + "-limit")
        if child is not None:
            child.active_stop_loss_price = stop

    def bind_native_close(
        self, root_oid: str, close_oid: str, plan_oid: str, executed_order_id: str
    ) -> None:
        root, plan, close = (self._records[oid] for oid in (root_oid, plan_oid, close_oid))
        if (
            plan.protection_root_oid != root_oid
            or not plan.provider_order_id
            or close.role != "CLOSE"
            or close.provider_order_id != executed_order_id
            or close.symbol != root.symbol
            or close.side == root.side
        ):
            raise ValueError("native close ownership mismatch")
        self.close_bindings[close_oid] = root_oid

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
        record.cancel_requested_by = existing.cancel_requested_by
        record.provider_terminal = record.provider_terminal or existing.provider_terminal
        record.cancel_post_claimed = existing.cancel_post_claimed
        self._records[record.client_oid] = replace(record)
        if record.provider_fills:
            self.record_fills(record, record.provider_fills)

    def record_fills(self, record: LiveIntentRecord, fills: tuple[dict[str, Any], ...]) -> None:
        for fill in fills:
            provider_fill_id = fill.get("fillId", fill.get("tradeId", fill.get("id")))
            if provider_fill_id is None:
                continue
            key = (record.exchange, str(provider_fill_id))
            if key in self._fill_keys:
                continue
            self._fill_keys.add(key)
            self.fills.append((record.client_oid, dict(fill)))

    def record_provider_event(self, observation: Any, client_oid: str) -> None:
        """Keep one source classification for each provider fill identity."""
        exchange = str(observation.exchange)
        provider_fill_id = str(observation.provider_fill_id)
        key = (exchange, provider_fill_id)
        if key in self._provider_event_keys:
            return
        self._provider_event_keys.add(key)
        self.provider_events.append(
            {
                "exchange": exchange,
                "provider_fill_id": provider_fill_id,
                "source": str(observation.source),
            }
        )


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
    return result if result.is_finite() else None


def normalize_fill(fill: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize Bitget fill shapes, including nested/list-valued feeDetail."""
    normalized = dict(fill)
    raw_detail = fill.get("feeDetail")
    if isinstance(raw_detail, str):
        try:
            raw_detail = json.loads(raw_detail)
        except (TypeError, ValueError):
            raw_detail = None
    if isinstance(raw_detail, Mapping):
        fee_details: Sequence[Mapping[str, Any]] = (raw_detail,)
    elif isinstance(raw_detail, list):
        fee_details = tuple(item for item in raw_detail if isinstance(item, Mapping))
    else:
        fee_details = ()
    if fee_details:
        fee = sum(
            (
                abs(
                    _to_decimal(
                        detail.get("totalFee", detail.get("fee", detail.get("feeAmount", "0")))
                    )
                    or Decimal("0")
                )
                for detail in fee_details
            ),
            Decimal("0"),
        )
        fee_coin = next(
            (
                detail.get("feeCoin", detail.get("feeCcy", detail.get("feeCurrency")))
                for detail in fee_details
                if detail.get("feeCoin", detail.get("feeCcy", detail.get("feeCurrency")))
            ),
            None,
        )
        normalized["fee"] = fee
        if fee_coin is not None:
            normalized["feeCcy"] = str(fee_coin)
    else:
        normalized["fee"] = abs(
            _to_decimal(fill.get("fee", fill.get("fillFee", fill.get("feeAmount", "0"))))
            or Decimal("0")
        )
    return normalized


def summarize_fills(
    fills: Sequence[Mapping[str, Any]],
) -> tuple[Decimal, Decimal | None, Decimal, tuple[str, ...]]:
    """Return (fill qty, weighted avg price, total fee, provider fill ids)."""
    total_qty = Decimal("0")
    notional = Decimal("0")
    total_fee = Decimal("0")
    ids: list[str] = []
    for raw_fill in fills:
        fill = normalize_fill(raw_fill)
        qty = _to_decimal(
            fill.get("quantity", fill.get("size", fill.get("fillQty", fill.get("baseVolume", 0))))
        )
        price = _to_decimal(fill.get("price", fill.get("fillPrice", fill.get("priceAvg", 0))))
        if qty is None or qty <= 0 or price is None or price <= 0:
            continue
        total_qty += qty
        notional += qty * price
        fee = _to_decimal(fill.get("fee", 0)) or Decimal("0")
        total_fee += abs(fee)
        raw_id = fill.get("fillId", fill.get("tradeId", fill.get("id")))
        if raw_id is not None and str(raw_id) not in ids:
            ids.append(str(raw_id))
    avg = (notional / total_qty) if total_qty > 0 else None
    return total_qty, avg, total_fee, tuple(ids)


def classify_live_order(
    detail: Mapping[str, Any], fills: Sequence[Mapping[str, Any]]
) -> LiveOrderStatus:
    """Classify an entry from its read-back detail + fills."""
    raw_status = str(detail.get("state", detail.get("status", ""))).strip().lower()
    requested = _to_decimal(detail.get("requestedQty", detail.get("size", 0)))
    filled_qty, _, _, _ = summarize_fills(fills)
    detail_filled = _to_decimal(
        detail.get("filledQty", detail.get("filledSize", detail.get("baseVolume", 0)))
    )
    filled_qty = max(filled_qty, detail_filled or Decimal("0"))
    if raw_status in _FILLED_STATUSES:
        return (
            LiveOrderStatus.FILLED
            if filled_qty > 0 or (detail_filled is not None and detail_filled > 0)
            else LiveOrderStatus.UNKNOWN
        )
    if raw_status in _PARTIAL_STATUSES:
        if requested is not None and requested > 0 and filled_qty >= requested:
            return LiveOrderStatus.FILLED
        return LiveOrderStatus.PARTIAL if filled_qty > 0 else LiveOrderStatus.ACCEPTED
    if requested is not None and requested > 0 and filled_qty >= requested:
        return LiveOrderStatus.FILLED
    if filled_qty > 0:
        return LiveOrderStatus.PARTIAL
    # Canceling an unfilled remainder does not erase a partial execution. It
    # still requires protection and must retain the durable margin reservation.
    if raw_status in _REJECTED_STATUSES:
        return LiveOrderStatus.REJECTED
    if not raw_status and not fills:
        return LiveOrderStatus.UNKNOWN
    if raw_status in _ACCEPTED_STATUSES or raw_status == "":
        return LiveOrderStatus.ACCEPTED
    return LiveOrderStatus.UNKNOWN


def _persist_readback(
    store: LiveIntentStoreProtocol,
    record: LiveIntentRecord,
    detail: Mapping[str, Any],
    fills: Sequence[Mapping[str, Any]],
) -> LiveOrderStatus:
    normalized_fills = tuple(normalize_fill(fill) for fill in fills)
    status = classify_live_order(detail, normalized_fills)
    filled_qty, avg_price, fee, fill_ids = summarize_fills(normalized_fills)
    detail_qty = _to_decimal(
        detail.get("filledQty", detail.get("filledSize", detail.get("baseVolume", 0)))
    )
    if detail_qty is not None and detail_qty > filled_qty:
        filled_qty = detail_qty
        avg_price = _to_decimal(detail.get("priceAvg", detail.get("avgPrice")))
    provider_order_id = detail.get("orderId", detail.get("providerOrderId"))
    # Preserve the submitted provider id: read-back details may omit it.
    if provider_order_id is not None:
        record.provider_order_id = str(provider_order_id)
    record.provider_fill_ids = fill_ids
    record.provider_fills = tuple(dict(fill) for fill in normalized_fills)
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

    read_pre_entry_state(client, request.symbol)
    ensure_isolated_margin_and_leverage(client, request.symbol, request.leverage)

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
