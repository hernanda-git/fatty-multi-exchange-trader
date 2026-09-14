"""Bitget Classic V2 native position-protection contracts.

This module contains no network or database code.  Keeping request construction and
response normalization pure makes the provider contract executable in offline tests and
prevents an undocumented field from being added accidentally at the HTTP boundary.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from typing import Any


class ProtectionContractError(ValueError):
    """A Bitget native-protection request or response violates the known contract."""


def _positive_decimal(value: Decimal | str, field: str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProtectionContractError(f"{field} must be a positive decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ProtectionContractError(f"{field} must be a positive decimal")
    return parsed


def _decimal_text(value: Decimal | str, field: str) -> str:
    parsed = _positive_decimal(value, field)
    return format(parsed, "f")


def _hold_side(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"long", "buy"}:
        return "buy"
    if normalized in {"short", "sell"}:
        return "sell"
    raise ProtectionContractError("Bitget one-way hold side must be long/buy or short/sell")


def _market_execute_price(value: Decimal | str | None, field: str) -> str:
    """Return the explicit market sentinel and reject accidental limit exits."""
    if value is None or str(value).strip() in {"", "0", "0.0", "0.00"}:
        return "0"
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProtectionContractError(f"{field} must use market execution price 0") from exc
    if not parsed.is_finite() or parsed != 0:
        raise ProtectionContractError(f"{field} must use market execution price 0")
    return "0"


def _default_client_oid(
    prefix: str,
    symbol: str,
    hold_side: str,
    quantity: Decimal,
    trigger: Decimal,
) -> str:
    """Build a stable fallback OID for legacy callers that omit one.

    The production fill path always supplies an entry-derived OID.  This deterministic
    fallback exists for the older operator surface so it does not reintroduce timestamp
    OIDs and duplicate requests on a retry.
    """
    material = f"{prefix}|{symbol.upper()}|{hold_side}|{quantity}|{trigger}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{symbol.upper()}-{digest}"


def _client_oid(
    value: str | None,
    *,
    prefix: str,
    symbol: str,
    hold_side: str,
    quantity: Decimal,
    trigger: Decimal,
) -> str:
    if value is not None and value.strip():
        return value.strip()
    return _default_client_oid(prefix, symbol, hold_side, quantity, trigger)


def build_position_tpsl_payload(
    *,
    symbol: str,
    hold_side: str,
    quantity: Decimal | str,
    stop_loss: Decimal | str | None,
    take_profit: Decimal | str | None,
    stop_loss_execute_price: Decimal | str | None = None,
    take_profit_execute_price: Decimal | str | None = None,
    stop_loss_client_oid: str | None = None,
    take_profit_client_oid: str | None = None,
    stop_loss_size: Decimal | str | None = None,
    take_profit_size: Decimal | str | None = None,
    product_type: str = "USDT-FUTURES",
    margin_coin: str = "USDT",
    include_delegate_type: bool = False,
) -> dict[str, str]:
    """Build a documented Classic V2 ``place-pos-tpsl`` payload.

    ``quantity`` is the confirmed position quantity used to validate the optional
    partial-plan sizes.  Position-level plans intentionally omit the generic ``size``
    field and the two partial-size fields.  The provider interprets an omitted size as
    a position plan; callers that explicitly want partial plans must provide the
    documented ``stopLossSize`` and/or ``stopSurplusSize``.

    Positive execute prices are rejected: Bitget interprets them as limit execution,
    while this safety path requires market execution (the documented ``0`` sentinel).
    ``delegateType`` is opt-in because it is not part of the current official request
    schema; account-specific compatibility must be proven by a separate canary.
    """
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ProtectionContractError("Bitget protection symbol is required")
    if not product_type.strip() or not margin_coin.strip():
        raise ProtectionContractError("Bitget product type and margin coin are required")
    confirmed_quantity = _positive_decimal(quantity, "protection quantity")
    normalized_hold_side = _hold_side(hold_side)
    if stop_loss is None and take_profit is None:
        raise ProtectionContractError("at least one of stop_loss or take_profit is required")

    payload: dict[str, str] = {
        "marginCoin": margin_coin.strip().upper(),
        "productType": product_type.strip(),
        "symbol": normalized_symbol,
        "holdSide": normalized_hold_side,
    }
    if include_delegate_type:
        payload["delegateType"] = "normal"

    if stop_loss is not None:
        stop_loss_decimal = _positive_decimal(stop_loss, "stop loss")
        payload.update(
            {
                "stopLossTriggerPrice": format(stop_loss_decimal, "f"),
                "stopLossTriggerType": "mark_price",
                "stopLossExecutePrice": _market_execute_price(
                    stop_loss_execute_price, "stop loss execute price"
                ),
                "stopLossClientOid": _client_oid(
                    stop_loss_client_oid,
                    prefix="sl",
                    symbol=normalized_symbol,
                    hold_side=normalized_hold_side,
                    quantity=confirmed_quantity,
                    trigger=stop_loss_decimal,
                ),
            }
        )
        if stop_loss_size is not None:
            partial_size = _positive_decimal(stop_loss_size, "stop loss size")
            if partial_size > confirmed_quantity:
                raise ProtectionContractError("stop loss size cannot exceed protection quantity")
            payload["stopLossSize"] = format(partial_size, "f")

    if take_profit is not None:
        take_profit_decimal = _positive_decimal(take_profit, "take profit")
        payload.update(
            {
                "stopSurplusTriggerPrice": format(take_profit_decimal, "f"),
                "stopSurplusTriggerType": "mark_price",
                "stopSurplusExecutePrice": _market_execute_price(
                    take_profit_execute_price, "take profit execute price"
                ),
                "stopSurplusClientOid": _client_oid(
                    take_profit_client_oid,
                    prefix="tp",
                    symbol=normalized_symbol,
                    hold_side=normalized_hold_side,
                    quantity=confirmed_quantity,
                    trigger=take_profit_decimal,
                ),
            }
        )
        if take_profit_size is not None:
            partial_size = _positive_decimal(take_profit_size, "take profit size")
            if partial_size > confirmed_quantity:
                raise ProtectionContractError("take profit size cannot exceed protection quantity")
            payload["stopSurplusSize"] = format(partial_size, "f")

    return payload


def normalize_position_tpsl_response(data: Any) -> list[dict[str, Any]]:
    """Normalize the official ``data: object[]`` placement response."""
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise ProtectionContractError("Bitget placement response must be an array of objects")
    return [dict(row) for row in data]


def normalize_pending_plan_response(data: Any) -> list[dict[str, Any]]:
    """Normalize ``data.entrustedList`` from the pending-plan endpoint."""
    if isinstance(data, list):
        if not all(isinstance(row, dict) for row in data):
            raise ProtectionContractError("Bitget pending plan response must contain objects")
        return [dict(row) for row in data]
    if not isinstance(data, dict):
        raise ProtectionContractError("Bitget pending plan response must be an object")
    rows = data.get("entrustedList")
    if rows is None:
        return []
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ProtectionContractError("Bitget pending plan response must contain objects")
    return [dict(row) for row in rows]
