"""Pure Bitget V2 WebSocket message construction and frame normalization.

Every constant here was verified against the LIVE endpoint rather than copied
from vendor documentation, which is wrong about the frame envelope: the v2
subscribe frame uses ``op``, not ``action``. See
``tests/unit/test_bitget_ws_v2.py`` for the captured wire behaviour.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fatty_trader.exchanges.bitget.client import build_signature
from fatty_trader.exchanges.bitget.ws_models import (
    BitgetWebSocketEvent,
    WebSocketProtocolError,
)

V2_INST_TYPE = "USDT-FUTURES"

#: Private account channels for USDT-M futures. ``orders-algo`` is the v2
#: spelling; the v1 ``ordersAlgo`` channel does not exist on v2.
V2_PRIVATE_CHANNELS: tuple[str, ...] = ("positions", "orders", "orders-algo")

TICKER_CHANNEL = "ticker"


def _normalize_symbols(symbols: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw_symbol in symbols:
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError("Bitget websocket symbol is required")
        if symbol not in normalized:
            normalized.append(symbol)
    return tuple(normalized)


def build_v2_public_subscription(
    symbols: list[str] | tuple[str, ...], *, allow_empty: bool = False
) -> dict[str, Any]:
    """Build the public ticker subscription frame."""
    normalized = _normalize_symbols(symbols)
    if not normalized and not allow_empty:
        raise ValueError("Bitget websocket requires at least one symbol")
    return {
        "op": "subscribe",
        "args": [
            {"instType": V2_INST_TYPE, "channel": TICKER_CHANNEL, "instId": symbol}
            for symbol in normalized
        ],
    }


def build_v2_private_subscription() -> dict[str, Any]:
    """Build the private account subscription frame (no per-symbol args)."""
    return {
        "op": "subscribe",
        "args": [
            {"instType": V2_INST_TYPE, "channel": channel, "instId": "default"}
            for channel in V2_PRIVATE_CHANNELS
        ],
    }


def build_v2_login_message(
    *, api_key: str, passphrase: str, secret: str, timestamp_seconds: int | str
) -> dict[str, Any]:
    """Build the private login frame; the timestamp is in SECONDS, not ms."""
    try:
        timestamp = int(str(timestamp_seconds))
    except (TypeError, ValueError) as exc:
        raise ValueError("Bitget websocket login timestamp must be an integer") from exc
    if timestamp <= 0:
        raise ValueError("Bitget websocket login timestamp must be positive")
    timestamp_text = str(timestamp)
    return {
        "op": "login",
        "args": [
            {
                "apiKey": api_key,
                "passphrase": passphrase,
                "timestamp": timestamp_text,
                "sign": build_signature(secret, timestamp_text, "GET", "/user/verify", "", ""),
            }
        ],
    }


def normalize_v2_frame(raw: str | bytes) -> list[BitgetWebSocketEvent]:
    """Normalize one v2 frame into typed events; acks and heartbeats yield none."""
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

    event_name = payload.get("event")
    if event_name == "error":
        raise WebSocketProtocolError(
            f"Bitget websocket error {payload.get('code')}: {payload.get('msg')}",
            code=str(payload.get("code", "")) or None,
        )
    if event_name == "login":
        # Only a zero-code ack is benign. A re-auth/expired-session login frame
        # must raise, never be swallowed as a successful ack.
        if str(payload.get("code", "0")) not in {"0", "00000"}:
            raise WebSocketProtocolError("Bitget websocket login was not acknowledged")
        return []

    if event_name in {"subscribe", "unsubscribe", "channel-conn-count"}:
        return []

    arg = payload.get("arg")
    if not isinstance(arg, dict):
        raise WebSocketProtocolError("Bitget websocket event argument is missing")
    channel = str(arg.get("channel", "")).strip()
    raw_data = payload.get("data")
    if not isinstance(raw_data, list) or not all(isinstance(row, dict) for row in raw_data):
        raise WebSocketProtocolError("Bitget websocket data must be an array of objects")
    rows = [dict(row) for row in raw_data]

    if channel == TICKER_CHANNEL:
        return _ticker_events(rows, payload.get("ts"))
    if channel == "positions":
        return _position_events(rows, payload.get("ts"))
    if channel == "orders":
        return _order_events(rows, payload.get("ts"))
    if channel == "orders-algo":
        return _plan_events(rows, payload.get("ts"))
    raise WebSocketProtocolError(f"Bitget websocket channel is unsupported: {channel}")


def _event_time_ms(row: dict[str, Any], fallback: Any) -> int:
    for field in ("ts", "uTime", "cTime", "fillTime", "systemTime", "eventTime"):
        value = row.get(field, fallback if field == "ts" else None)
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
    symbol = str(row.get("instId") or "").strip().upper()
    if not symbol:
        raise WebSocketProtocolError("Bitget websocket symbol is missing")
    return symbol


def _required_size(row: dict[str, Any]) -> Any:
    """Return the filled-size field, or raise if the row is malformed.

    Defaulting to "0" would fabricate a zero-quantity order event, which reads
    downstream as a real order that filled nothing.
    """
    for field in ("accFillSz", "fillSz", "baseVolume", "sz"):
        if row.get(field) is not None:
            return row[field]
    raise WebSocketProtocolError("Bitget websocket order row is missing a size field")


def _positive(value: Any, field: str) -> Decimal:
    from decimal import Decimal, InvalidOperation

    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise WebSocketProtocolError(f"Bitget websocket {field} is invalid") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise WebSocketProtocolError(f"Bitget websocket {field} must be positive")
    return parsed


def _optional_positive(value: Any, field: str) -> Decimal | None:
    if value is None or str(value).strip() in {"", "0", "0.0"}:
        return None
    return _positive(value, field)


def _nonnegative(value: Any, field: str) -> Decimal:
    from decimal import Decimal, InvalidOperation

    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise WebSocketProtocolError(f"Bitget websocket {field} is invalid") from exc
    if not parsed.is_finite() or parsed < 0:
        raise WebSocketProtocolError(f"Bitget websocket {field} must be nonnegative")
    return parsed


def _ticker_events(rows: list[dict[str, Any]], fallback_time: Any) -> list[BitgetWebSocketEvent]:
    return [
        BitgetWebSocketEvent(
            kind="mark_price",
            symbol=_symbol(row),
            mark_price=_positive(row.get("markPrice"), "mark price"),
            event_time_ms=_event_time_ms(row, fallback_time),
            raw=row,
        )
        for row in rows
    ]


def _position_events(rows: list[dict[str, Any]], fallback_time: Any) -> list[BitgetWebSocketEvent]:
    return [
        BitgetWebSocketEvent(
            kind="position",
            symbol=_symbol(row),
            quantity=_nonnegative(row.get("total", "0"), "position quantity"),
            liquidation_price=_optional_positive(row.get("liqPx"), "liquidation price"),
            event_time_ms=_event_time_ms(row, fallback_time),
            raw=row,
        )
        for row in rows
    ]


def _order_events(rows: list[dict[str, Any]], fallback_time: Any) -> list[BitgetWebSocketEvent]:
    events: list[BitgetWebSocketEvent] = []
    for row in rows:
        symbol = _symbol(row)
        order_id = str(row.get("ordId", "")).strip() or None
        client_oid = str(row.get("clOrdId", "")).strip() or None
        event_time = _event_time_ms(row, fallback_time)
        events.append(
            BitgetWebSocketEvent(
                kind="order",
                symbol=symbol,
                quantity=_nonnegative(_required_size(row), "order quantity"),
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
        fill_time = _event_time_ms({"fillTime": row.get("fillTime")}, event_time)
        fill_id = str(row.get("tradeId", "")).strip() or f"{order_id or symbol}:{fill_time}"
        events.append(
            BitgetWebSocketEvent(
                kind="fill",
                symbol=symbol,
                quantity=fill_size,
                price=_positive(row.get("fillPx"), "fill price"),
                fee=_nonnegative(row.get("fillFee", "0"), "fill fee"),
                provider_order_id=order_id,
                client_oid=client_oid,
                provider_fill_id=fill_id,
                event_time_ms=fill_time,
                raw=row,
            )
        )
    return events


def _plan_events(rows: list[dict[str, Any]], fallback_time: Any) -> list[BitgetWebSocketEvent]:
    return [
        BitgetWebSocketEvent(
            kind="plan",
            symbol=_symbol(row),
            trigger_price=_positive(row.get("triggerPx"), "trigger price"),
            provider_order_id=(str(row.get("id", "")).strip() or None),
            client_oid=(str(row.get("cOid", "")).strip() or None),
            event_time_ms=_event_time_ms(row, fallback_time),
            raw=row,
        )
        for row in rows
    ]
