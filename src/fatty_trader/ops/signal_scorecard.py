"""Per-dispatch trade attribution from the durable ledger.

The ledger records an entry fill under the intent's client order id and the
position close under the venue's own plan order id, so a trade is attributed by
symbol and time window, not by a foreign key. Every window ends at the next
dispatch on the same symbol, the only ambiguity-free rule the data supports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class Trade:
    dispatch_id: str
    symbol: str
    direction: str
    state: str
    terminal_reason: str | None
    signal_entry: Decimal | None
    stop_loss: Decimal | None
    filled_price: Decimal | None
    filled_qty: Decimal
    realized: Decimal
    fees: Decimal
    opened_at: datetime
    closed_at: datetime | None
    linked: bool

    @property
    def net(self) -> Decimal:
        return self.realized - self.fees

    @property
    def risk_per_unit(self) -> Decimal | None:
        if self.signal_entry is None or self.stop_loss is None:
            return None
        risk = abs(self.signal_entry - self.stop_loss)
        return risk if risk > 0 else None

    @property
    def r_multiple(self) -> Decimal | None:
        risk = self.risk_per_unit
        if risk is None or self.filled_qty <= 0:
            return None
        return self.net / (risk * self.filled_qty)

    @property
    def entered(self) -> bool:
        return self.filled_price is not None and self.filled_qty > 0


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def attribute(
    *,
    dispatches: Sequence[Mapping[str, Any]],
    intents: Iterable[Mapping[str, Any]],
    fills: Iterable[Mapping[str, Any]],
    now: datetime,
    horizon: timedelta = timedelta(days=3),
) -> list[Trade]:
    """Attribute realised PnL to each dispatch without double counting.

    ``dispatches[*]['symbol']`` must be the venue symbol (``POPCATUSDT``), the
    same key ``fills`` uses.
    """
    entry_oids = {
        intent["dispatch_id"]: intent["client_order_id"]
        for intent in intents
        if intent.get("dispatch_id") and intent.get("client_order_id")
    }
    entry_fills = {fill["client_order_id"]: fill for fill in fills if fill.get("client_order_id")}
    by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    for fill in fills:
        by_symbol.setdefault(str(fill["symbol"]), []).append(fill)
    for rows in by_symbol.values():
        rows.sort(key=lambda fill: fill["filled_at"])

    ordered = sorted(dispatches, key=lambda row: row["created_at"])
    next_starts: list[datetime] = []
    for index, dispatch in enumerate(ordered):
        successor = next(
            (
                later["created_at"]
                for later in ordered[index + 1 :]
                if later["symbol"] == dispatch["symbol"]
            ),
            None,
        )
        next_starts.append(successor or (now + horizon))

    trades: list[Trade] = []
    for dispatch, end in zip(ordered, next_starts):
        symbol = str(dispatch["symbol"])
        oid = entry_oids.get(str(dispatch["dispatch_id"]))
        entry_fill = entry_fills.get(oid) if oid else None
        opened_at = entry_fill["filled_at"] if entry_fill is not None else dispatch["created_at"]
        window = [
            fill for fill in by_symbol.get(symbol, []) if opened_at <= fill["filled_at"] < end
        ]
        # A dispatch created before intents carried dispatch_id still has a real
        # entry fill; the first fill without realised PnL in its window is it.
        linked = entry_fill is not None
        if entry_fill is None:
            entry_fill = next(
                (fill for fill in window if not fill.get("realized_pnl")),
                window[0] if window else None,
            )
        realized = sum(
            (_decimal(fill.get("realized_pnl")) or Decimal("0") for fill in window),
            Decimal("0"),
        )
        fees = sum((_decimal(fill.get("fee")) or Decimal("0") for fill in window), Decimal("0"))
        filled_qty = (_decimal(entry_fill.get("quantity")) if entry_fill else None) or Decimal("0")
        trades.append(
            Trade(
                dispatch_id=str(dispatch["dispatch_id"]),
                symbol=symbol,
                direction=str(dispatch.get("direction") or ""),
                state=str(dispatch.get("state") or ""),
                terminal_reason=dispatch.get("terminal_reason"),
                signal_entry=_decimal(dispatch.get("entry_price")),
                stop_loss=_decimal(dispatch.get("stop_loss")),
                filled_price=_decimal(entry_fill.get("price")) if entry_fill else None,
                filled_qty=filled_qty,
                realized=realized,
                fees=fees,
                opened_at=opened_at,
                closed_at=max((fill["filled_at"] for fill in window), default=None),
                linked=linked,
            )
        )
    return trades


def summarize(trades: Sequence[Trade]) -> dict[str, Any]:
    closed = [trade for trade in trades if trade.realized != 0 or trade.state == "CLOSED"]
    entered = [trade for trade in trades if trade.entered]
    wins = [trade for trade in closed if trade.net > 0]
    losses = [trade for trade in closed if trade.net < 0]
    r_values = [trade.r_multiple for trade in entered if trade.r_multiple is not None]
    return {
        "dispatches": len(trades),
        "entered": len(entered),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "realized": sum((trade.realized for trade in closed), Decimal("0")),
        "fees": sum((trade.fees for trade in closed), Decimal("0")),
        "net": sum((trade.net for trade in closed), Decimal("0")),
        "avg_r": (sum(r_values, Decimal("0")) / len(r_values)) if r_values else None,
        "win_rate": (Decimal(len(wins)) / Decimal(len(closed))) if closed else None,
        "unlinked": sum(1 for trade in entered if not trade.linked),
    }


def format_report(trades: Sequence[Trade], summary: Mapping[str, Any]) -> str:
    lines = [
        "signal scorecard",
        "----------------",
        f"dispatches={summary['dispatches']} entered={summary['entered']} "
        f"closed={summary['closed']} wins={summary['wins']} losses={summary['losses']}",
        f"realised={summary['realized']:.4f} fees={summary['fees']:.4f} net={summary['net']:.4f}",
        f"win_rate={_pct(summary['win_rate'])} avg_r={_num(summary['avg_r'])} "
        f"unlinked_entries={summary['unlinked']}",
        "",
        f"{'symbol':<14} {'state':<9} {'realised':>10} {'fees':>8} {'R':>7}  dispatch",
    ]
    for trade in trades:
        lines.append(
            f"{trade.symbol:<14} {trade.state:<9} {trade.realized:>10.4f} "
            f"{trade.fees:>8.4f} {_num(trade.r_multiple):>7}  {trade.dispatch_id[:8]}"
        )
    return "\n".join(lines)


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{Decimal(value) * 100:.1f}%"


def _num(value: Any) -> str:
    return "n/a" if value is None else f"{Decimal(value):.2f}"
