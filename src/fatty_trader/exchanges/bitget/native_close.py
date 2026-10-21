"""Resolve and bind a venue-executed native SL/TP plan close to its entry lifecycle.

The venue executes a position TP/SL plan as its own order.  That order carries the
plan's provider order id as *its* client order id, so the close is provably ours, but
it never carries the intent-derived client order id the ledger indexes on.  Without a
binding step the close fill stays invisible to the ledger: the entry lifecycle never
settles, the dispatch spins in recovery for days, realized P&L is understated, and an
operator has to approve a baseline by hand to clear the lane.

The binding key is already durable: the protection intent stores the provider order id
the venue returned for its plan (``live_order_intents.provider_order_id`` for the
``-sl``/``-tp`` legs).  The venue's executed order reports that same id as its client
order id, so one exact equality proves ownership end to end.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any

from fatty_trader.exchanges.bitget.live import (
    LiveIntentRecord,
    LiveIntentStoreProtocol,
    normalize_fill,
)

__all__ = [
    "NativeCloseResolutionError",
    "NativePlanClose",
    "normalize_plan_close_fill",
    "reconcile_native_plan_close",
    "resolve_native_plan_close",
]


class NativeCloseResolutionError(ValueError):
    """A venue close cannot be safely attributed to an owned entry lifecycle."""


@dataclass(frozen=True)
class NativePlanClose:
    """One venue-executed close that provably belongs to one owned entry."""

    exchange: str
    symbol: str
    side: str
    root_oid: str
    plan_oid: str
    close_oid: str
    executed_order_id: str
    provider_fill_id: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    realized_pnl: Decimal
    payload: dict[str, Any]


def _text(payload: Any, *fields: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    for field in fields:
        value = payload.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _decimal(payload: Any, fields: tuple[str, ...], *, positive: bool = False) -> Decimal:
    if not isinstance(payload, dict):
        raise NativeCloseResolutionError("venue fill payload is invalid")
    raw = next((payload[field] for field in fields if field in payload), None)
    if raw is None or str(raw).strip() == "":
        raise NativeCloseResolutionError(f"venue fill field is missing: {fields[0]}")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise NativeCloseResolutionError(f"venue fill field is invalid: {fields[0]}") from exc
    if not value.is_finite() or (positive and value <= 0):
        raise NativeCloseResolutionError(f"venue fill field is not usable: {fields[0]}")
    return value


def _side(payload: Any) -> str:
    raw = (_text(payload, "side", "orderSide") or "").upper()
    if raw in {"BUY", "SELL"}:
        return raw
    trade_side = (_text(payload, "tradeSide") or "").lower()
    if "sell" in trade_side:
        return "SELL"
    if "buy" in trade_side:
        return "BUY"
    raise NativeCloseResolutionError("venue fill side is missing or invalid")


def normalize_plan_close_fill(payload: Any) -> dict[str, Any]:
    """Normalize one venue fill into the identity fields a close binding needs."""
    normalized = normalize_fill(payload)
    provider_fill_id = _text(normalized, "fillId", "tradeId", "id")
    if provider_fill_id is None:
        raise NativeCloseResolutionError("venue fill ID is missing")
    provider_order_id = _text(normalized, "orderId", "ordId", "providerOrderId")
    if provider_order_id is None:
        raise NativeCloseResolutionError("venue fill order ID is missing")
    symbol = _text(normalized, "symbol", "instId")
    if symbol is None:
        raise NativeCloseResolutionError("venue fill symbol is missing")
    fee = _decimal(normalized, ("fee", "fillFee", "feeAmount"))
    realized_pnl = (
        _decimal(normalized, ("realizedPnl", "profit", "totalProfits", "realizedProfit"))
        if any(
            field in normalized
            for field in ("realizedPnl", "profit", "totalProfits", "realizedProfit")
        )
        else Decimal("0")
    )
    return {
        "symbol": symbol.strip().upper(),
        "side": _side(normalized),
        "provider_fill_id": provider_fill_id,
        "provider_order_id": provider_order_id,
        "quantity": abs(
            _decimal(
                normalized,
                ("quantity", "fillSz", "fillQty", "size", "baseVolume"),
                positive=True,
            )
        ),
        "price": _decimal(
            normalized,
            ("price", "fillPx", "fillPrice", "priceAvg"),
            positive=True,
        ),
        "fee": fee,
        "realized_pnl": realized_pnl,
        "payload": dict(normalized),
    }


def _order_client_oid(detail: Any) -> str | None:
    """Read the client order id the venue reported for an executed order."""
    rows: Any = detail
    if isinstance(detail, dict):
        rows = detail.get("data", detail.get("orderList", detail))
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    return _text(rows[0], "clientOid", "client_oid", "clientOrderId")


async def resolve_native_plan_close(
    *,
    intent: LiveIntentRecord,
    read_fills: Callable[[str], Awaitable[Any]],
    read_order_detail: Callable[[str, str], Awaitable[Any]],
    plan_owner: Callable[[str], LiveIntentRecord | None],
) -> NativePlanClose | None:
    """Find the venue fill that executed one of this entry's own protection plans.

    Only a close whose executed order reports one of our own plan ids as its client
    order id is accepted; every other venue fill is left untouched for the provider
    exit reconciler.
    """
    if intent.role != "ENTRY" or intent.filled_qty <= 0:
        return None
    try:
        raw_fills = await read_fills(intent.symbol)
    except Exception:
        return None
    if isinstance(raw_fills, dict):
        raw_fills = raw_fills.get("fillList", raw_fills.get("entrustedList"))
    if not isinstance(raw_fills, list):
        return None
    close_side = "SELL" if intent.side == "BUY" else "BUY"
    entry_fill_ids = set(intent.provider_fill_ids)
    candidates: list[dict[str, Any]] = []
    for raw in raw_fills:
        if not isinstance(raw, dict):
            continue
        try:
            fill = normalize_plan_close_fill(raw)
        except NativeCloseResolutionError:
            continue
        if fill["symbol"] != intent.symbol.strip().upper():
            continue
        if fill["side"] != close_side:
            continue
        if fill["provider_fill_id"] in entry_fill_ids:
            continue
        candidates.append(fill)
    candidates.sort(key=lambda fill: str(fill["payload"].get("cTime", "")), reverse=True)
    for fill in candidates:
        try:
            detail = await read_order_detail(intent.symbol, fill["provider_order_id"])
        except Exception:
            continue
        client_oid = _order_client_oid(detail)
        if client_oid is None:
            continue
        plan = plan_owner(client_oid)
        if plan is None:
            continue
        if plan.role not in {"SL", "TP"}:
            continue
        if plan.protection_root_oid != intent.client_oid:
            continue
        if plan.provider_order_id != client_oid:
            continue
        if plan.symbol.strip().upper() != intent.symbol.strip().upper():
            continue
        return NativePlanClose(
            exchange=intent.exchange,
            symbol=intent.symbol.strip().upper(),
            side=fill["side"],
            root_oid=intent.client_oid,
            plan_oid=plan.client_oid,
            close_oid=f"{intent.client_oid}-native-close",
            executed_order_id=fill["provider_order_id"],
            provider_fill_id=fill["provider_fill_id"],
            quantity=fill["quantity"],
            price=fill["price"],
            fee=fill["fee"],
            realized_pnl=fill["realized_pnl"],
            payload=fill["payload"],
        )
    return None


def reconcile_native_plan_close(
    store: LiveIntentStoreProtocol,
    native: NativePlanClose,
) -> bool:
    """Record the venue-executed plan close as an owned close and bind it.

    The close is written as a ``CLOSE`` intent under the entry root, which is the
    same identity the lifecycle settlement reads, and it is bound to the reservation
    so the owned-quantity arithmetic subtracts it.
    """
    record = LiveIntentRecord(
        exchange=native.exchange,
        client_oid=native.close_oid,
        symbol=native.symbol,
        side=native.side,
        role="CLOSE",
        state="filled",
        requested_qty=native.quantity,
        filled_qty=native.quantity,
        avg_price=native.price,
        fee=native.fee,
        provider_order_id=native.executed_order_id,
        provider_fill_ids=(native.provider_fill_id,),
        provider_fills=(native.payload,),
    )
    claim = getattr(store, "claim", None)
    if not callable(claim):
        raise NativeCloseResolutionError("durable store lacks atomic claim")
    try:
        created = bool(claim(record))
    except ValueError as exc:
        raise NativeCloseResolutionError(f"native close claim conflicts: {exc}") from exc
    if created:
        store.update(record)
    else:
        existing = store.get(native.close_oid)
        if existing is None:
            raise NativeCloseResolutionError("native close claim has no durable record")
        if existing.provider_order_id != native.executed_order_id:
            raise NativeCloseResolutionError("native close provider order ID conflicts")
        if native.provider_fill_id not in existing.provider_fill_ids:
            merged = replace(
                existing,
                state="filled",
                filled_qty=existing.filled_qty + native.quantity,
                provider_fill_ids=(*existing.provider_fill_ids, native.provider_fill_id),
                provider_fills=(*existing.provider_fills, native.payload),
            )
            store.update(merged)
    bind = getattr(store, "bind_native_close", None)
    if not callable(bind):
        raise NativeCloseResolutionError("durable store lacks native close binding")
    bind(native.root_oid, native.close_oid, native.plan_oid, native.executed_order_id)
    return created
