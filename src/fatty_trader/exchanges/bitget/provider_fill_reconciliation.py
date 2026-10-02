"""Pure, idempotent reconciliation for provider-only Bitget exits."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from fatty_trader.exchanges.bitget.live import (
    LiveIntentRecord,
    LiveIntentStoreProtocol,
    normalize_fill,
)


class ProviderFillReconciliationError(ValueError):
    """A provider exit cannot be safely converted into a durable ledger record."""


@dataclass(frozen=True)
class ProviderExitObservation:
    exchange: str
    symbol: str
    side: str
    source: str
    provider_order_id: str | None
    provider_fill_id: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    realized_pnl: Decimal
    payload: dict[str, Any]


@dataclass(frozen=True)
class ProviderExitReconciliation:
    source: str
    client_oid: str
    provider_order_id: str | None
    provider_fill_id: str
    record: LiveIntentRecord
    created: bool = False


def _text(payload: Mapping[str, Any], *fields: str) -> str | None:
    for field in fields:
        value = payload.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _decimal(
    payload: Mapping[str, Any], fields: tuple[str, ...], *, positive: bool = False
) -> Decimal:
    raw = next((payload[field] for field in fields if field in payload), None)
    if raw is None or str(raw).strip() == "":
        raise ProviderFillReconciliationError(f"provider fill field is missing: {fields[0]}")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProviderFillReconciliationError(
            f"provider fill field is invalid: {fields[0]}"
        ) from exc
    if not value.is_finite() or (value <= 0 if positive else False):
        qualifier = "positive " if positive else "finite "
        raise ProviderFillReconciliationError(
            f"provider fill field must be {qualifier}decimal: {fields[0]}"
        )
    return value


def _side(payload: Mapping[str, Any]) -> str:
    raw = (_text(payload, "side", "orderSide") or "").upper()
    if raw in {"BUY", "SELL"}:
        return raw
    trade_side = (_text(payload, "tradeSide") or "").lower()
    if "sell" in trade_side:
        return "SELL"
    if "buy" in trade_side:
        return "BUY"
    raise ProviderFillReconciliationError("provider exit side is missing or invalid")


def normalize_provider_exit(
    payload: Mapping[str, Any], *, exchange: str = "bitget"
) -> ProviderExitObservation:
    """Normalize one unmatched provider fill, preserving provider values exactly."""
    normalized = normalize_fill(payload)
    if _text(normalized, "clientOid", "client_oid", "clientOrderId") is not None:
        raise ProviderFillReconciliationError("provider fill already has a client OID")
    symbol = _text(normalized, "symbol", "instId")
    if symbol is None:
        raise ProviderFillReconciliationError("provider fill symbol is missing")
    provider_fill_id = _text(normalized, "fillId", "tradeId", "id")
    if provider_fill_id is None:
        raise ProviderFillReconciliationError("provider fill ID is missing")
    provider_order_id = _text(normalized, "orderId", "ordId", "providerOrderId")
    quantity = abs(
        _decimal(
            normalized,
            ("quantity", "fillSz", "fillQty", "size", "baseVolume"),
            positive=True,
        )
    )
    price = _decimal(
        normalized,
        ("price", "fillPx", "fillPrice", "priceAvg"),
        positive=True,
    )
    fee = abs(_decimal(normalized, ("fee", "fillFee", "feeAmount")))
    realized_pnl = (
        _decimal(
            normalized,
            ("realizedPnl", "profit", "totalProfits", "realizedProfit"),
        )
        if any(
            field in normalized
            for field in ("realizedPnl", "profit", "totalProfits", "realizedProfit")
        )
        else Decimal("0")
    )
    enter_source = (_text(normalized, "enterPointSource", "orderSource") or "").upper()
    trade_side = (_text(normalized, "tradeSide") or "").lower()
    source = (
        "SYSTEM_LIQUIDATION"
        if enter_source == "SYS" or trade_side.startswith("burst_")
        else "PROVIDER_EXIT"
    )
    return ProviderExitObservation(
        exchange=exchange.strip().lower(),
        symbol=symbol.strip().upper(),
        side=_side(normalized),
        source=source,
        provider_order_id=provider_order_id,
        provider_fill_id=provider_fill_id,
        quantity=quantity,
        price=price,
        fee=fee,
        realized_pnl=realized_pnl,
        payload=dict(normalized),
    )


def _client_oid(observation: ProviderExitObservation) -> str:
    material = "|".join(
        (
            observation.exchange,
            observation.symbol,
            observation.provider_order_id or "",
            observation.provider_fill_id,
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"provider-{observation.exchange}-{digest}"


def reconcile_provider_exit(
    store: LiveIntentStoreProtocol,
    payload: Mapping[str, Any],
    *,
    exchange: str = "bitget",
) -> ProviderExitReconciliation:
    """Persist one provider-only exit exactly once and return its durable identity."""
    observation = normalize_provider_exit(payload, exchange=exchange)
    client_oid = _client_oid(observation)
    record = LiveIntentRecord(
        exchange=observation.exchange,
        client_oid=client_oid,
        symbol=observation.symbol,
        side=observation.side,
        role="CLOSE",
        state="filled",
        requested_qty=observation.quantity,
        filled_qty=observation.quantity,
        avg_price=observation.price,
        fee=observation.fee,
        provider_order_id=observation.provider_order_id,
        provider_fill_ids=(observation.provider_fill_id,),
        provider_fills=(observation.payload,),
    )
    claim = getattr(store, "claim", None)
    if not callable(claim):
        raise ProviderFillReconciliationError("durable store lacks atomic claim")
    created = bool(claim(record))
    if created:
        store.update(record)
        persisted = record
    else:
        existing = store.get(client_oid)
        if existing is None:
            raise ProviderFillReconciliationError("provider reconciliation claim has no record")
        if existing.provider_order_id != observation.provider_order_id:
            raise ProviderFillReconciliationError("provider order ID conflicts with reconciliation")
        if observation.provider_fill_id not in existing.provider_fill_ids:
            merged = LiveIntentRecord(
                **{
                    **existing.__dict__,
                    "state": "filled",
                    "filled_qty": observation.quantity,
                    "avg_price": observation.price,
                    "fee": observation.fee,
                    "provider_fill_ids": (observation.provider_fill_id,),
                    "provider_fills": (observation.payload,),
                }
            )
            store.update(merged)
            persisted = merged
        else:
            persisted = existing
    record_event = getattr(store, "record_provider_event", None)
    if callable(record_event):
        record_event(observation, client_oid)
    return ProviderExitReconciliation(
        source=observation.source,
        client_oid=client_oid,
        provider_order_id=observation.provider_order_id,
        provider_fill_id=observation.provider_fill_id,
        record=persisted,
        created=created,
    )
