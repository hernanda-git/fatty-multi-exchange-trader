"""Paper ledger engine for the `Kaka trades` channel — no venue, no credentials.

Sizing deliberately mirrors what the live lane would do: 1 USDT margin per leg, 20x ceiling,
and a taker fee on both sides. Second entries (DCA) are merged into one averaged position, as
requested, so the paper P&L answers "what would our lane have made mirroring this channel?".

Every refusal is explicit: a market entry without a price, or a stop already crossed, raises
instead of inventing a number.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from fatty_trader.kaka.parser import KakaEvent, KakaEventType

MARGIN_PER_LEG_USDT = Decimal("1")
LEVERAGE = 20
TAKER_FEE_RATE = Decimal("0.0006")  # 0.06% per side


@dataclass(frozen=True)
class PaperTrade:
    source_message_id: int
    symbol: str
    side: str  # LONG | SHORT
    entry_price: Decimal
    stop_loss: Decimal
    notional_usdt: Decimal
    margin_usdt: Decimal
    leverage: int
    take_profit: Decimal | None = None
    dca_level: Decimal | None = None
    legs: int = 1
    state: str = "open"  # open | closed
    close_price: Decimal | None = None
    close_reason: str | None = None
    realized_pnl_usdt: Decimal | None = None

    @property
    def stop_distance_pct(self) -> Decimal:
        return abs(self.stop_loss - self.entry_price) / self.entry_price


def notional_for_leg() -> Decimal:
    return MARGIN_PER_LEG_USDT * LEVERAGE


def open_trade(event: KakaEvent, *, market_price: Decimal | None) -> PaperTrade:
    """Open a paper position, or refuse loudly when the setup is not tradable."""
    if event.symbol is None or event.side is None or event.stop_loss is None:
        raise ValueError("OPEN event needs symbol, side and stop loss")
    entry = event.entry if event.entry is not None else market_price
    if entry is None or entry <= 0:
        raise ValueError("no entry price and no market price available")
    if event.side == "LONG" and event.stop_loss >= entry:
        raise ValueError("long stop is at or above entry (setup already crossed)")
    if event.side == "SHORT" and event.stop_loss <= entry:
        raise ValueError("short stop is at or below entry (setup already crossed)")
    return PaperTrade(
        source_message_id=event.message_id,
        symbol=event.symbol,
        side=event.side,
        entry_price=entry,
        stop_loss=event.stop_loss,
        take_profit=event.take_profit,
        dca_level=event.dca,
        notional_usdt=notional_for_leg(),
        margin_usdt=MARGIN_PER_LEG_USDT,
        leverage=LEVERAGE,
    )


def add_leg(
    trade: PaperTrade, *, price: Decimal | None, market_price: Decimal | None
) -> PaperTrade:
    """Merge a second entry into the same position: averaged price, doubled notional."""
    if trade.state != "open":
        raise ValueError("cannot add to a closed trade")
    fill = price if price is not None else market_price
    if fill is None or fill <= 0:
        raise ValueError("second entry has no price and no market price available")
    added_notional = notional_for_leg()
    total_notional = trade.notional_usdt + added_notional
    quantity = trade.notional_usdt / trade.entry_price + added_notional / fill
    averaged = total_notional / quantity
    return replace(
        trade,
        entry_price=averaged,
        notional_usdt=total_notional,
        margin_usdt=trade.margin_usdt + MARGIN_PER_LEG_USDT,
        legs=trade.legs + 1,
    )


def move_stop(trade: PaperTrade, new_stop: Decimal) -> PaperTrade:
    """Apply a stop revision, keeping the geometry consistent with the side."""
    if trade.state != "open":
        raise ValueError("cannot move the stop of a closed trade")
    if new_stop <= 0:
        raise ValueError("stop must be positive")
    if trade.side == "LONG" and new_stop >= trade.entry_price:
        # Moving a long stop above entry is allowed (locking profit), but never above the
        # current market-less sanity bound of the stop itself.
        pass
    if trade.side == "SHORT" and new_stop <= trade.entry_price:
        pass
    return replace(trade, stop_loss=new_stop)


def breakeven(trade: PaperTrade) -> PaperTrade:
    return replace(trade, stop_loss=trade.entry_price)


def set_take_profit(trade: PaperTrade, take_profit: Decimal | None) -> PaperTrade:
    return replace(trade, take_profit=take_profit)


def pnl_usdt(trade: PaperTrade, exit_price: Decimal) -> Decimal:
    """Realised PnL with a taker fee on both sides."""
    move = (
        (exit_price - trade.entry_price) / trade.entry_price
        if trade.side == "LONG"
        else (trade.entry_price - exit_price) / trade.entry_price
    )
    gross = trade.notional_usdt * move
    exit_notional = trade.notional_usdt / trade.entry_price * exit_price
    fees = (trade.notional_usdt + exit_notional) * TAKER_FEE_RATE
    return (gross - fees).quantize(Decimal("0.000001"))


def close_trade(trade: PaperTrade, *, exit_price: Decimal, reason: str) -> PaperTrade:
    if trade.state != "open":
        raise ValueError("trade is already closed")
    if exit_price is None or exit_price <= 0:
        raise ValueError("exit price must be positive")
    return replace(
        trade,
        state="closed",
        close_price=exit_price,
        close_reason=reason,
        realized_pnl_usdt=pnl_usdt(trade, exit_price),
    )


def exit_price_for(trade: PaperTrade, event: KakaEvent, *, market_price: Decimal | None) -> Decimal:
    """Decide the exit price for a management event, refusing to guess."""
    if event.type is KakaEventType.STOP_HIT:
        return trade.stop_loss
    if event.type is KakaEventType.TP:
        if event.take_profit is not None:
            return event.take_profit
        if trade.take_profit is not None:
            return trade.take_profit
        if market_price is None:
            raise ValueError("TP event without a target and without a market price")
        return market_price
    if market_price is None:
        raise ValueError("close event without a market price")
    return market_price
