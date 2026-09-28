"""Persistence for the Kaka paper ledger. Paper tables only — never the live ledger."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from fatty_trader.kaka.paper import PaperTrade

KAKA_CHANNEL_ID = -1003763643270

_SELECT_RECEIVED = """
SELECT id, message_id, raw_text
FROM telegram_messages
WHERE intake_state = 'RECEIVED' AND channel_id = %s
ORDER BY received_at, message_id
FOR UPDATE SKIP LOCKED
LIMIT %s
"""
_UPDATE_STATE = "UPDATE telegram_messages SET intake_state = %s WHERE id = %s"

_SELECT_OPEN = """
SELECT id, source_message_id, symbol, side, entry_price, stop_loss, take_profit, dca_level,
       notional_usdt, margin_usdt, leverage, legs, state, close_price, close_reason,
       realized_pnl_usdt
FROM paper_kaka_trades
WHERE state = 'open' AND symbol = %s
ORDER BY opened_at DESC
LIMIT 1
"""

_INSERT_TRADE = """
INSERT INTO paper_kaka_trades
    (id, source_message_id, symbol, side, entry_price, stop_loss, take_profit, dca_level,
     notional_usdt, margin_usdt, leverage, legs, state, close_price, close_reason,
     realized_pnl_usdt, opened_at, closed_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        CURRENT_TIMESTAMP, CASE WHEN %s = 'closed' THEN CURRENT_TIMESTAMP ELSE NULL END)
ON CONFLICT (source_message_id) DO NOTHING
"""

_UPDATE_TRADE = """
UPDATE paper_kaka_trades
SET entry_price = %s, stop_loss = %s, take_profit = %s, dca_level = %s,
    notional_usdt = %s, margin_usdt = %s, legs = %s, state = %s,
    close_price = %s, close_reason = %s, realized_pnl_usdt = %s,
    closed_at = CASE WHEN %s = 'closed' THEN CURRENT_TIMESTAMP ELSE closed_at END
WHERE id = %s
"""

_INSERT_EVENT = """
INSERT INTO paper_kaka_events
    (id, trade_id, source_message_id, event_type, parse_path, parsed_json)
VALUES (gen_random_uuid(), %s, %s, %s, %s, %s::jsonb)
ON CONFLICT (source_message_id, event_type) DO NOTHING
"""


def _uuid(value: Any) -> Any:
    return value if isinstance(value, UUID) else (UUID(str(value)) if value else None)


def to_trade(row: Any) -> PaperTrade:
    data = dict(row) if not isinstance(row, dict) else row
    return PaperTrade(
        source_message_id=int(data["source_message_id"]),
        symbol=str(data["symbol"]),
        side=str(data["side"]),
        entry_price=Decimal(str(data["entry_price"])),
        stop_loss=Decimal(str(data["stop_loss"])),
        take_profit=Decimal(str(data["take_profit"])) if data["take_profit"] is not None else None,
        dca_level=Decimal(str(data["dca_level"])) if data["dca_level"] is not None else None,
        notional_usdt=Decimal(str(data["notional_usdt"])),
        margin_usdt=Decimal(str(data["margin_usdt"])),
        leverage=int(data["leverage"]),
        legs=int(data.get("legs") or 1),
        state=str(data["state"]),
        close_price=Decimal(str(data["close_price"])) if data["close_price"] is not None else None,
        close_reason=data["close_reason"],
        realized_pnl_usdt=(
            Decimal(str(data["realized_pnl_usdt"]))
            if data["realized_pnl_usdt"] is not None
            else None
        ),
    )


def claim_paper_batch(cursor: Any, *, channel_id: int, limit: int) -> list[Any]:
    cursor.execute(_SELECT_RECEIVED, (channel_id, limit))
    return list(cursor.fetchall())


def mark_message(cursor: Any, message_uuid: Any, state: str) -> None:
    cursor.execute(_UPDATE_STATE, (state, message_uuid))


def load_open_trade(cursor: Any, symbol: str) -> tuple[Any, PaperTrade] | None:
    cursor.execute(_SELECT_OPEN, (symbol,))
    row = cursor.fetchone()
    if row is None:
        return None
    data = dict(row) if not isinstance(row, dict) else row
    return data["id"], to_trade(data)


def insert_trade(cursor: Any, trade: PaperTrade) -> None:
    cursor.execute(
        _INSERT_TRADE,
        (
            _new_uuid(),
            trade.source_message_id,
            trade.symbol,
            trade.side,
            trade.entry_price,
            trade.stop_loss,
            trade.take_profit,
            trade.dca_level,
            trade.notional_usdt,
            trade.margin_usdt,
            trade.leverage,
            trade.legs,
            trade.state,
            trade.close_price,
            trade.close_reason,
            trade.realized_pnl_usdt,
            trade.state,
        ),
    )


def update_trade(cursor: Any, trade_id: Any, trade: PaperTrade) -> None:
    cursor.execute(
        _UPDATE_TRADE,
        (
            trade.entry_price,
            trade.stop_loss,
            trade.take_profit,
            trade.dca_level,
            trade.notional_usdt,
            trade.margin_usdt,
            trade.legs,
            trade.state,
            trade.close_price,
            trade.close_reason,
            trade.realized_pnl_usdt,
            trade.state,
            trade_id,
        ),
    )


def record_event(
    cursor: Any,
    *,
    trade_id: Any,
    message_id: int,
    event_type: str,
    parse_path: str,
    payload: dict[str, Any],
) -> None:
    cursor.execute(
        _INSERT_EVENT,
        (_uuid(trade_id), message_id, event_type, parse_path, json.dumps(payload, default=str)),
    )


def _new_uuid() -> UUID:
    from uuid import uuid4

    return uuid4()
