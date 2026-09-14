"""Pure Bitget Classic WebSocket messages and event normalization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from fatty_trader.exchanges.bitget.client import build_signature


class WebSocketProtocolError(ValueError):
    """A WebSocket message violates the known Classic contract."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BitgetWebSocketEvent:
    """Normalized event shared by protection and reconciliation consumers."""

    kind: str
    symbol: str
    event_time_ms: int
    mark_price: Decimal | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    fee: Decimal | None = None
    liquidation_price: Decimal | None = None
    provider_order_id: str | None = None
    client_oid: str | None = None
    provider_fill_id: str | None = None
    trigger_price: Decimal | None = None
    raw: dict[str, Any] | None = None


def _positive(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise WebSocketProtocolError(f"Bitget websocket {field} is invalid") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise WebSocketProtocolError(f"Bitget websocket {field} must be positive")
    return parsed


def _nonnegative(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise WebSocketProtocolError(f"Bitget websocket {field} is invalid") from exc
    if not parsed.is_finite() or parsed < 0:
        raise WebSocketProtocolError(f"Bitget websocket {field} must be nonnegative")
    return parsed


def _optional_positive(value: Any, field: str) -> Decimal | None:
    if value is None or str(value).strip() in {"", "0", "0.0"}:
        return None
    return _positive(value, field)


def _time_ms(row: dict[str, Any], fallback: Any = None) -> int:
    for field in ("eventTime", "systemTime", "uTime", "fillTime", "cTime", "ts", "time"):
        value = row.get(field, fallback if field == "eventTime" else None)
        if value is None or str(value).strip() == "":
            continue
        try:
            timestamp = int(str(value))
        except (TypeError, ValueError) as exc:
            raise WebSocketProtocolError("Bitget websocket event time is invalid") from exc
        if timestamp > 0:
            return timestamp
    raise WebSocketProtocolError("Bitget websocket event time is missing")


def _symbol(row: dict[str, Any]) -> str:
    value = row.get("instId", row.get("symbol"))
    symbol = str(value or "").strip().upper()
    if not symbol or symbol == "DEFAULT":
        raise WebSocketProtocolError("Bitget websocket symbol is missing")
    return symbol


def _rows(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise WebSocketProtocolError("Bitget websocket data must be an array of objects")
    return [dict(row) for row in data]


def build_login_message(
    *, api_key: str, passphrase: str, secret: str, timestamp_seconds: int | str
) -> dict[str, Any]:
    """Build the Classic login message; Classic timestamps are seconds, not REST ms."""
    try:
        timestamp = int(str(timestamp_seconds))
    except (TypeError, ValueError) as exc:
        raise ValueError("Bitget websocket login timestamp must be an integer") from exc
    if timestamp <= 0:
        raise ValueError("Bitget websocket login timestamp must be positive")
    timestamp_text = str(timestamp)
    signature = build_signature(secret, timestamp_text, "GET", "/user/verify", "", "")
    return {
        "op": "login",
        "args": [
            {
                "apiKey": api_key,
                "passphrase": passphrase,
                "timestamp": timestamp_text,
                "sign": signature,
            }
        ],
    }


def build_subscription_message(symbols: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Build the documented Classic public/private subscriptions."""
    normalized_symbols: list[str] = []
    for raw_symbol in symbols:
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError("Bitget websocket symbol is required")
        if symbol not in normalized_symbols:
            normalized_symbols.append(symbol)
    if not normalized_symbols:
        raise ValueError("Bitget websocket requires at least one symbol")
    args: list[dict[str, str]] = [
        {"instType": "mc", "channel": "ticker", "instId": symbol} for symbol in normalized_symbols
    ]
    args.extend(
        [
            {"instType": "UMCBL", "channel": "positions", "instId": "default"},
            {"instType": "UMCBL", "channel": "orders", "instId": "default"},
            {"instType": "UMCBL", "channel": "ordersAlgo", "instId": "default"},
        ]
    )
    return {"op": "subscribe", "args": args}


def _ticker_events(rows: list[dict[str, Any]], fallback_time: Any) -> list[BitgetWebSocketEvent]:
    events: list[BitgetWebSocketEvent] = []
    for row in rows:
        events.append(
            BitgetWebSocketEvent(
                kind="mark_price",
                symbol=_symbol(row),
                mark_price=_positive(row.get("markPrice"), "mark price"),
                event_time_ms=_time_ms(row, fallback_time),
                raw=row,
            )
        )
    return events


def _position_events(rows: list[dict[str, Any]], fallback_time: Any) -> list[BitgetWebSocketEvent]:
    events: list[BitgetWebSocketEvent] = []
    for row in rows:
        events.append(
            BitgetWebSocketEvent(
                kind="position",
                symbol=_symbol(row),
                quantity=_nonnegative(row.get("total", "0"), "position quantity"),
                liquidation_price=_optional_positive(row.get("liqPx"), "liquidation price"),
                event_time_ms=_time_ms(row, fallback_time),
                raw=row,
            )
        )
    return events


def _order_events(rows: list[dict[str, Any]], fallback_time: Any) -> list[BitgetWebSocketEvent]:
    events: list[BitgetWebSocketEvent] = []
    for row in rows:
        symbol = _symbol(row)
        order_id = str(row.get("ordId", "")).strip() or None
        client_oid = str(row.get("clOrdId", "")).strip() or None
        event_time = _time_ms(row, fallback_time)
        events.append(
            BitgetWebSocketEvent(
                kind="order",
                symbol=symbol,
                quantity=_nonnegative(
                    row.get("accFillSz", row.get("fillSz", "0")), "order quantity"
                ),
                price=_optional_positive(row.get("avgPx"), "average order price"),
                provider_order_id=order_id,
                client_oid=client_oid,
                event_time_ms=event_time,
                raw=row,
            )
        )
        fill_size = _nonnegative(row.get("fillSz", "0"), "fill quantity")
        if fill_size <= 0:
            continue
        fill_time = _time_ms({"fillTime": row.get("fillTime")}, event_time)
        fill_id = str(row.get("tradeId", "")).strip() or (
            f"{order_id}:{fill_time}" if order_id is not None else f"{symbol}:{fill_time}"
        )
        fee_raw = row.get("fillFee", "0")
        try:
            fee = Decimal(str(fee_raw))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise WebSocketProtocolError("Bitget websocket fill fee is invalid") from exc
        if not fee.is_finite():
            raise WebSocketProtocolError("Bitget websocket fill fee is invalid")
        events.append(
            BitgetWebSocketEvent(
                kind="fill",
                symbol=symbol,
                quantity=fill_size,
                price=_positive(row.get("fillPx"), "fill price"),
                fee=fee,
                provider_order_id=order_id,
                client_oid=client_oid,
                provider_fill_id=fill_id,
                event_time_ms=fill_time,
                raw=row,
            )
        )
    return events


def _plan_events(rows: list[dict[str, Any]], fallback_time: Any) -> list[BitgetWebSocketEvent]:
    events: list[BitgetWebSocketEvent] = []
    for row in rows:
        events.append(
            BitgetWebSocketEvent(
                kind="plan",
                symbol=_symbol(row),
                trigger_price=_positive(row.get("triggerPx"), "trigger price"),
                provider_order_id=(str(row.get("id", "")).strip() or None),
                client_oid=(str(row.get("cOid", "")).strip() or None),
                event_time_ms=_time_ms(row, fallback_time),
                raw=row,
            )
        )
    return events


def normalize_ws_message(raw: str | bytes) -> list[BitgetWebSocketEvent]:
    """Normalize one Classic WebSocket frame into zero or more typed events."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WebSocketProtocolError("Bitget websocket frame is not UTF-8") from exc
    if raw.strip() == "pong":
        return []
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise WebSocketProtocolError("Bitget websocket frame is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise WebSocketProtocolError("Bitget websocket frame must be an object")
    if payload.get("event") == "error":
        code = str(payload.get("code", ""))
        message = str(payload.get("msg", "websocket provider error"))
        raise WebSocketProtocolError(f"Bitget websocket error {code}: {message}", code=code)
    if payload.get("event") in {"login", "subscribe", "unsubscribe"}:
        return []
    arg = payload.get("arg")
    if not isinstance(arg, dict):
        raise WebSocketProtocolError("Bitget websocket event argument is missing")
    channel = str(arg.get("channel", "")).strip()
    rows = _rows(payload.get("data"))
    if channel == "ticker":
        return _ticker_events(rows, payload.get("ts"))
    if channel == "positions":
        return _position_events(rows, payload.get("ts"))
    if channel == "orders":
        return _order_events(rows, payload.get("ts"))
    if channel == "ordersAlgo":
        return _plan_events(rows, payload.get("ts"))
    raise WebSocketProtocolError(f"Bitget websocket channel is unsupported: {channel}")
