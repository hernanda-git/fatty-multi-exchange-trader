#!/usr/bin/env python3
"""Replay the Kaka trades channel history through the paper engine and report the result.

Reads the JSONL dump of the channel (one message per line), replays it chronologically using
the same parser and ledger rules as the live paper lane, and prices market entries/exits from
historical public candles. Output is a CSV plus a markdown summary — it never writes to the
paper tables, so a replay can never masquerade as live paper trading.

Usage:
    python scripts/replay_kaka_paper.py [--jsonl PATH] [--out-csv PATH] [--out-md PATH]
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from fatty_trader.kaka.paper import (
    add_leg,
    breakeven,
    close_trade,
    move_stop,
    open_trade,
    set_take_profit,
)
from fatty_trader.kaka.parser import KakaEventType, parse_kaka_event

DEFAULT_JSONL = Path("/home/valarion/dumps/kaka_trades_since_20260901.jsonl")
_CANDLES = "https://api.bitget.com/api/v2/mix/market/candles?symbol={symbol}&productType=USDT-FUTURES&granularity=1m&startTime={start}&endTime={end}&limit=200"
_cache: dict[tuple[str, int], Decimal | None] = {}


def price_at(symbol: str, when: datetime, *, retries: int = 2) -> Decimal | None:
    """Close of the 1-minute candle containing ``when`` (public API, no credentials)."""
    minute = int(when.timestamp() // 60) * 60
    key = (symbol, minute)
    if key in _cache:
        return _cache[key]
    url = _CANDLES.format(symbol=symbol, start=minute * 1000, end=(minute + 60) * 1000)
    price: Decimal | None = None
    for attempt in range(retries + 1):
        try:
            with urlopen(url, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            rows = payload.get("data") or []
            if rows:
                price = Decimal(str(rows[0][4]))  # close
                break
        except Exception:  # noqa: BLE001 - replay is best effort on prices
            time.sleep(0.5 + attempt)
    _cache[key] = price
    return price


@dataclass
class ReplayResult:
    trades: list[dict[str, Any]]
    skipped: list[dict[str, Any]]


def replay(rows: list[dict[str, Any]], *, price_lookup: Any = price_at) -> ReplayResult:
    """Replay messages in order, mirroring the live paper lane's rules."""
    open_trades: dict[str, Any] = {}
    trades: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    def price_of(symbol: str, when: datetime) -> Decimal | None:
        return price_lookup(symbol, when)

    for row in rows:
        text = (row.get("text") or "").strip()
        if not text:
            skipped.append({"message_id": row["message_id"], "reason": "media-only"})
            continue
        when = datetime.fromisoformat(row["date_utc"])
        event = parse_kaka_event(text, message_id=int(row["message_id"]))
        if event.type is KakaEventType.NOISE:
            continue

        if event.type is KakaEventType.OPEN:
            if event.symbol in open_trades:
                skipped.append({"message_id": row["message_id"], "reason": "already-open"})
                continue
            market = None if event.entry is not None else price_of(event.symbol or "", when)
            try:
                trade = open_trade(event, market_price=market)
            except ValueError as exc:
                skipped.append({"message_id": row["message_id"], "reason": f"refused:{exc}"})
                continue
            assert event.symbol is not None
            open_trades[event.symbol] = (trade, when)
            continue

        # Management messages usually omit the symbol, and the channel often runs one trade at
        # a time while talking about its most recent one. Resolve in that order: explicit
        # symbol, the only open trade, then the most recently opened trade.
        target_symbol = None
        if event.symbol in open_trades:
            target_symbol = event.symbol
        elif len(open_trades) == 1:
            target_symbol = next(iter(open_trades))
        elif open_trades:
            target_symbol = max(open_trades, key=lambda sym: open_trades[sym][1])
        if target_symbol is None or target_symbol not in open_trades:
            skipped.append(
                {"message_id": row["message_id"], "reason": f"unmatched:{event.type.value}"}
            )
            continue
        trade, opened_at = open_trades[target_symbol]

        try:
            if event.type is KakaEventType.ADD:
                trade = add_leg(trade, price=event.entry, market_price=price_of(trade.symbol, when))
                if event.stop_loss is not None:
                    trade = move_stop(trade, event.stop_loss)
            elif event.type is KakaEventType.STOP_MOVE and event.stop_loss is not None:
                trade = move_stop(trade, event.stop_loss)
            elif event.type is KakaEventType.BREAKEVEN:
                trade = breakeven(trade)
            elif event.type is KakaEventType.TP:
                if event.take_profit is not None:
                    trade = set_take_profit(trade, event.take_profit)
                else:
                    exit_price = trade.take_profit or price_of(trade.symbol, when)
                    if exit_price is None:
                        skipped.append({"message_id": row["message_id"], "reason": "no-exit-price"})
                        continue
                    trade = close_trade(trade, exit_price=exit_price, reason="tp")
            elif event.type is KakaEventType.TP_REMOVED:
                trade = set_take_profit(trade, None)
            elif event.type in (KakaEventType.STOP_HIT, KakaEventType.CLOSE, KakaEventType.CANCEL):
                if event.type is KakaEventType.STOP_HIT:
                    exit_price = trade.stop_loss
                    reason = "stop-hit"
                else:
                    exit_price = price_of(trade.symbol, when)
                    reason = "manual-close" if event.type is KakaEventType.CLOSE else "cancelled"
                    if exit_price is None:
                        skipped.append({"message_id": row["message_id"], "reason": "no-exit-price"})
                        continue
                trade = close_trade(trade, exit_price=exit_price, reason=reason)
        except ValueError as exc:
            skipped.append({"message_id": row["message_id"], "reason": f"refused:{exc}"})
            continue

        if trade.state == "closed":
            trades.append(
                {
                    "symbol": trade.symbol,
                    "side": trade.side,
                    "entry": trade.entry_price,
                    "stop": trade.stop_loss,
                    "legs": trade.legs,
                    "exit": trade.close_price,
                    "reason": trade.close_reason,
                    "notional": trade.notional_usdt,
                    "pnl_usdt": trade.realized_pnl_usdt,
                    "channel_pnl": event.reported_pnl,
                    "opened_at": opened_at.isoformat(),
                    "closed_at": when.isoformat(),
                    "open_message_id": trade.source_message_id,
                }
            )
            del open_trades[target_symbol]
        else:
            open_trades[target_symbol] = (trade, opened_at)

    for _symbol, (trade, opened_at) in open_trades.items():
        trades.append(
            {
                "symbol": trade.symbol,
                "side": trade.side,
                "entry": trade.entry_price,
                "stop": trade.stop_loss,
                "legs": trade.legs,
                "exit": None,
                "reason": "still-open-at-end-of-replay",
                "notional": trade.notional_usdt,
                "pnl_usdt": None,
                "channel_pnl": None,
                "opened_at": opened_at.isoformat(),
                "closed_at": None,
                "open_message_id": trade.source_message_id,
            }
        )
    return ReplayResult(trades=trades, skipped=skipped)


def summarise(result: ReplayResult) -> str:
    closed = [t for t in result.trades if t["pnl_usdt"] is not None]
    wins = [t for t in closed if t["pnl_usdt"] > 0]
    losses = [t for t in closed if t["pnl_usdt"] < 0]
    total = sum((t["pnl_usdt"] for t in closed), Decimal("0"))
    channel_total = sum(
        (t["channel_pnl"] for t in closed if t["channel_pnl"] is not None), Decimal("0")
    )
    by_symbol: dict[str, list[Decimal]] = {}
    for trade in closed:
        by_symbol.setdefault(trade["symbol"], []).append(trade["pnl_usdt"])
    lines = [
        "# Kaka trades — paper evaluation (replay of the channel history)",
        "",
        "Replay of the channel's own message history through the same parser and ledger rules as",
        "the live paper lane: 1 USDT margin per leg at 20x, taker fee 0.06% per side, DCA merged",
        "into one averaged position, market entries/exits priced from 1-minute public candles.",
        "Nothing here is written to the paper tables; this is an offline evaluation.",
        "",
        f"- Trades closed: **{len(closed)}** (wins {len(wins)} / losses {len(losses)})",
        f"- Open at end of replay: {len([t for t in result.trades if t['pnl_usdt'] is None])}",
        f"- Realised paper PnL: **{total:.4f} USDT**",
        f"- Win rate: {(len(wins) / len(closed) * 100) if closed else 0:.1f}%",
        f"- Average per trade: {(total / len(closed)) if closed else Decimal(0):.4f} USDT",
        f"- Channel's own reported PnL over the same trades: {channel_total:.2f}$ (their sizing)",
        f"- Messages skipped: {len(result.skipped)}",
        "",
        "## Per symbol",
        "",
        "| Symbol | Trades | Net PnL (USDT) |",
        "|---|---|---|",
    ]
    for symbol, pnls in sorted(by_symbol.items()):
        lines.append(f"| {symbol} | {len(pnls)} | {sum(pnls, Decimal('0')):.4f} |")
    lines += [
        "",
        "## Trades",
        "",
        "| Closed (UTC) | Symbol | Side | Legs | Entry | Exit | Reason | PnL | Channel |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for trade in result.trades:
        pnl = "open" if trade["pnl_usdt"] is None else f"{trade['pnl_usdt']:.4f}"
        channel = "-" if trade["channel_pnl"] is None else f"{trade['channel_pnl']}$"
        closed_at = trade["closed_at"] or "-"
        lines.append(
            f"| {closed_at} | {trade['symbol']} | {trade['side']} | {trade['legs']} | "
            f"{trade['entry']} | {trade['exit'] or '-'} | {trade['reason']} | {pnl} | {channel} |"
        )
    if result.skipped:
        lines += ["", "## Skipped messages", ""]
        for item in result.skipped[:60]:
            lines.append(f"- `{item['message_id']}`: {item['reason']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay the Kaka channel into paper results")
    parser.add_argument("--jsonl", default=str(DEFAULT_JSONL))
    parser.add_argument("--out-csv", default="/home/valarion/dumps/kaka_paper_replay.csv")
    parser.add_argument("--out-md", default="docs/KAKA-PAPER-EVALUATION.md")
    args = parser.parse_args()

    rows = [json.loads(line) for line in Path(args.jsonl).read_text(encoding="utf-8").splitlines()]
    rows.sort(key=lambda row: row["date_utc"])
    result = replay(rows)

    csv_path = Path(args.out_csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(result.trades[0].keys()) if result.trades else []
        )
        writer.writeheader()
        for trade in result.trades:
            writer.writerow(trade)

    md_path = Path(args.out_md)
    md_path.write_text(summarise(result), encoding="utf-8")

    closed = [t for t in result.trades if t["pnl_usdt"] is not None]
    wins = [t for t in closed if t["pnl_usdt"] > 0]
    total = sum((t["pnl_usdt"] for t in closed), Decimal("0"))
    win_rate = (len(wins) / len(closed) * 100) if closed else 0.0
    print(
        f"replayed_messages={len(rows)} closed_trades={len(closed)} "
        f"win_rate={win_rate:.1f}% net_pnl_usdt={total:.4f} skipped={len(result.skipped)}"
    )
    print(f"csv={csv_path} md={md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
