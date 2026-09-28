"""Paper worker: turn `Kaka trades` messages into paper trades. No venue, ever.

Isolation contract (enforced by tests):
* reads/writes only `telegram_messages` (state transitions) and the `paper_kaka_*` tables;
* never imports a venue adapter, never reads provider credentials, and never touches the
  live execution tables or the protection registry.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fatty_trader.kaka import store
from fatty_trader.kaka.paper import (
    add_leg,
    breakeven,
    close_trade,
    exit_price_for,
    move_stop,
    open_trade,
    set_take_profit,
)
from fatty_trader.kaka.parser import KakaEvent, KakaEventType, parse_kaka_event

TRADE_MUTATING_EVENTS = {
    KakaEventType.ADD,
    KakaEventType.STOP_MOVE,
    KakaEventType.BREAKEVEN,
    KakaEventType.TP,
    KakaEventType.TP_REMOVED,
    KakaEventType.STOP_HIT,
    KakaEventType.CLOSE,
    KakaEventType.CANCEL,
}


def _payload(event: KakaEvent) -> dict[str, Any]:
    return {
        "type": event.type.value,
        "symbol": event.symbol,
        "side": event.side,
        "entry": event.entry,
        "stop_loss": event.stop_loss,
        "take_profit": event.take_profit,
        "dca": event.dca,
        "reported_pnl": event.reported_pnl,
        "raw": event.raw[:400],
    }


def process_paper_batch(
    connection_factory: Any,
    *,
    market_price_lookup: Any,
    channel_id: int = store.KAKA_CHANNEL_ID,
    limit: int = 25,
    image_analyzer: Any = None,
) -> dict[str, int]:
    """Consume one bounded batch of Kaka messages into the paper ledger.

    ``image_analyzer(path, message_id=...)`` is consulted only for messages with no text:
    47 of 171 messages in a 28-day window were chart images. It returns a KakaEvent or None,
    and a failure is recorded rather than guessed at.
    """
    counts = {
        "messages": 0,
        "opened": 0,
        "updated": 0,
        "closed": 0,
        "refused": 0,
        "noise": 0,
        "media_only": 0,
    }
    connection = connection_factory()
    try:
        cursor = connection.cursor()
        for row in store.claim_paper_batch(cursor, channel_id=channel_id, limit=limit):
            data = dict(row) if not isinstance(row, dict) else row
            message_uuid = data["id"]
            message_id = int(data["message_id"])
            text = str(data.get("raw_text") or "")
            media_path = data.get("media_path")
            if not text.strip():
                counts["messages"] += 1
                counts["media_only"] += 1
                _handle_media_only(
                    cursor,
                    message_id=message_id,
                    media_path=media_path,
                    image_analyzer=image_analyzer,
                    market_price_lookup=market_price_lookup,
                    counts=counts,
                )
                store.mark_message(cursor, message_uuid, "ANALYZED")
                continue
            event = parse_kaka_event(text, message_id=message_id)
            counts["messages"] += 1

            def price_of(symbol: str) -> Any:
                return market_price_lookup(symbol.removesuffix("USDT"))

            try:
                handle_event(cursor, event, price_of)
                counts[_counter_for(event)] += 1
            except Exception as exc:  # noqa: BLE001 - one bad message must not stop the batch
                counts["refused"] += 1
                store.record_event(
                    cursor,
                    trade_id=None,
                    message_id=message_id,
                    event_type=f"REFUSED:{event.type.value}",
                    parse_path="deterministic",
                    payload={"error": repr(exc), **_payload(event)},
                )
            store.mark_message(cursor, message_uuid, "ANALYZED")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return counts


def _handle_media_only(
    cursor: Any,
    *,
    message_id: int,
    media_path: Any,
    image_analyzer: Any,
    market_price_lookup: Any,
    counts: dict[str, int],
) -> None:
    """Chart-only messages: analyse the image when enabled, never invent a trade."""
    if image_analyzer is None or not media_path:
        store.record_event(
            cursor,
            trade_id=None,
            message_id=message_id,
            event_type="MEDIA_ONLY",
            parse_path="image",
            payload={"media_path": str(media_path), "analyzed": False},
        )
        return
    try:
        event = image_analyzer(str(media_path), message_id=message_id)
    except Exception as exc:  # noqa: BLE001 - a failed read must not stop the batch
        counts["refused"] += 1
        store.record_event(
            cursor,
            trade_id=None,
            message_id=message_id,
            event_type="REFUSED:IMAGE",
            parse_path="image",
            payload={"error": repr(exc), "media_path": str(media_path)},
        )
        return
    if event is None:
        store.record_event(
            cursor,
            trade_id=None,
            message_id=message_id,
            event_type="MEDIA_ONLY",
            parse_path="image",
            payload={"media_path": str(media_path), "analyzed": True, "signal": False},
        )
        return
    try:
        handle_event(
            cursor,
            event,
            lambda symbol: market_price_lookup(symbol.removesuffix("USDT")),
        )
        counts[_counter_for(event)] += 1
    except Exception as exc:  # noqa: BLE001
        counts["refused"] += 1
        store.record_event(
            cursor,
            trade_id=None,
            message_id=message_id,
            event_type=f"REFUSED:IMAGE:{event.type.value}",
            parse_path="image",
            payload={"error": repr(exc), "media_path": str(media_path)},
        )


def build_digest_text(cursor: Any, *, day_start: str) -> str:
    """Summarise the paper lane: last 24h plus all-time totals. Paper PnL only."""
    cursor.execute(
        """
        SELECT
            count(*) FILTER (WHERE closed_at >= %s) AS closed_today,
            count(*) FILTER (WHERE closed_at >= %s AND realized_pnl_usdt > 0) AS wins_today,
            coalesce(sum(realized_pnl_usdt) FILTER (WHERE closed_at >= %s), 0) AS pnl_today,
            count(*) FILTER (WHERE state = 'closed') AS closed_all,
            count(*) FILTER (WHERE state = 'closed' AND realized_pnl_usdt > 0) AS wins_all,
            coalesce(sum(realized_pnl_usdt) FILTER (WHERE state = 'closed'), 0) AS pnl_all,
            count(*) FILTER (WHERE state = 'open') AS open_now
        FROM paper_kaka_trades
        """,
        (day_start, day_start, day_start),
    )
    row = cursor.fetchone()
    data = dict(row) if not isinstance(row, dict) else row
    open_trades = []
    cursor.execute(
        "SELECT symbol, side, entry_price, stop_loss, legs FROM paper_kaka_trades "
        "WHERE state = 'open' ORDER BY opened_at DESC"
    )
    for entry in cursor.fetchall():
        item = dict(entry) if not isinstance(entry, dict) else entry
        open_trades.append(
            f"{item['symbol']} {item['side']} @{item['entry_price']} "
            f"SL {item['stop_loss']} ({item['legs']} leg)"
        )
    closed_all = int(data["closed_all"] or 0)
    wins_all = int(data["wins_all"] or 0)
    win_rate = (wins_all / closed_all * 100) if closed_all else 0.0
    lines = [
        f"24 jam : {int(data['closed_today'] or 0)} tutup "
        f"({int(data['wins_today'] or 0)} win) | PnL paper "
        f"{Decimal(str(data['pnl_today'])):+.4f} USDT",
        f"Total  : {closed_all} tutup | win rate {win_rate:.1f}% | "
        f"net {Decimal(str(data['pnl_all'])):+.4f} USDT",
        f"Terbuka: {int(data['open_now'] or 0)}"
        + (f" -> {'; '.join(open_trades)}" if open_trades else " (tidak ada)"),
        "Sizing : 1 USDT/margin @20x, fee 0.06%/sisi (paper, tanpa order nyata)",
    ]
    return "\n".join(lines)


def enqueue_digest(cursor: Any, *, dedup_key: str, text: str) -> bool:
    """Queue the digest once per day; the outbox dedup key makes this idempotent."""
    cursor.execute(
        """
        INSERT INTO notifications_outbox (id, dedup_key, payload)
        VALUES (gen_random_uuid(), %s, %s::jsonb)
        ON CONFLICT (dedup_key) DO NOTHING
        RETURNING id
        """,
        (
            dedup_key,
            json.dumps({"kind": "kaka-paper-digest", "text": text}),
        ),
    )
    return cursor.fetchone() is not None


def _counter_for(event: KakaEvent) -> str:
    if event.type is KakaEventType.OPEN:
        return "opened"
    if event.type in (KakaEventType.CLOSE, KakaEventType.STOP_HIT):
        return "closed"
    if event.type is KakaEventType.NOISE:
        return "noise"
    return "updated"


def handle_event(cursor: Any, event: KakaEvent, price_of: Any) -> None:
    """Apply one parsed event to the paper ledger."""
    if event.type is KakaEventType.NOISE:
        store.record_event(
            cursor,
            trade_id=None,
            message_id=event.message_id,
            event_type="NOISE",
            parse_path="deterministic",
            payload=_payload(event),
        )
        return

    if event.type is KakaEventType.OPEN:
        existing = store.load_open_trade(cursor, event.symbol or "")
        if existing is not None:
            store.record_event(
                cursor,
                trade_id=existing[0],
                message_id=event.message_id,
                event_type="OPEN_IGNORED_ALREADY_OPEN",
                parse_path="deterministic",
                payload=_payload(event),
            )
            return
        trade = open_trade(event, market_price=price_of(event.symbol or ""))
        store.insert_trade(cursor, trade)
        store.record_event(
            cursor,
            trade_id=None,
            message_id=event.message_id,
            event_type="OPEN",
            parse_path="deterministic",
            payload={**_payload(event), "entry_used": str(trade.entry_price)},
        )
        return

    if not event.symbol:
        # Management messages usually omit the symbol; apply to the single open trade if
        # exactly one exists, which is the common case for this channel.
        open_rows = None
        cursor.execute(
            "SELECT id, symbol FROM paper_kaka_trades WHERE state = 'open' ORDER BY opened_at DESC"
        )
        open_rows = list(cursor.fetchall())
        if len(open_rows) != 1:
            store.record_event(
                cursor,
                trade_id=None,
                message_id=event.message_id,
                event_type=f"UNMATCHED:{event.type.value}",
                parse_path="deterministic",
                payload=_payload(event),
            )
            return
        row = dict(open_rows[0]) if not isinstance(open_rows[0], dict) else open_rows[0]
        loaded = store.load_open_trade(cursor, str(row["symbol"]))
    else:
        loaded = store.load_open_trade(cursor, event.symbol)

    if loaded is None:
        store.record_event(
            cursor,
            trade_id=None,
            message_id=event.message_id,
            event_type=f"UNMATCHED:{event.type.value}",
            parse_path="deterministic",
            payload=_payload(event),
        )
        return

    trade_id, trade = loaded
    if event.type is KakaEventType.ADD:
        updated = add_leg(trade, price=event.entry, market_price=price_of(trade.symbol))
        if event.stop_loss is not None:
            updated = move_stop(updated, event.stop_loss)
    elif event.type is KakaEventType.STOP_MOVE and event.stop_loss is not None:
        updated = move_stop(trade, event.stop_loss)
    elif event.type is KakaEventType.BREAKEVEN:
        updated = breakeven(trade)
    elif event.type is KakaEventType.TP:
        if event.take_profit is not None:
            updated = set_take_profit(trade, event.take_profit)
        else:
            updated = close_trade(
                trade,
                exit_price=exit_price_for(trade, event, market_price=price_of(trade.symbol)),
                reason="tp",
            )
    elif event.type is KakaEventType.TP_REMOVED:
        updated = set_take_profit(trade, None)
    elif event.type in (KakaEventType.STOP_HIT, KakaEventType.CLOSE, KakaEventType.CANCEL):
        reason = {
            KakaEventType.STOP_HIT: "stop-hit",
            KakaEventType.CLOSE: "manual-close",
            KakaEventType.CANCEL: "cancelled",
        }[event.type]
        updated = close_trade(
            trade,
            exit_price=exit_price_for(trade, event, market_price=price_of(trade.symbol)),
            reason=reason,
        )
    else:  # pragma: no cover - defensive
        raise ValueError(f"unhandled event type {event.type}")

    store.update_trade(cursor, trade_id, updated)
    store.record_event(
        cursor,
        trade_id=trade_id,
        message_id=event.message_id,
        event_type=event.type.value,
        parse_path="deterministic",
        payload={
            **_payload(event),
            "stop_after": str(updated.stop_loss),
            "state_after": updated.state,
            "pnl_after": (
                str(updated.realized_pnl_usdt) if updated.realized_pnl_usdt is not None else None
            ),
        },
    )


def _as_decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None
