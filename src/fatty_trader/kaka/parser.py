"""Deterministic parser for the `Kaka trades` channel formats.

Learned from 171 real messages (2026-09-01 → 2026-09-28). The channel mixes a structured
SETUP block with terse one-liners, and three traps show up constantly in the real data:

* a leading dash is a **separator**, not a minus sign: ``SL -2401`` means 2401;
* numbers use thousands separators: ``$76,150``, ``Move sl -74,980``;
* ``SL hit | Profit+50$`` is a **win** (the stop had been moved above entry), so "stop hit"
  must never be scored as a loss on its own.

Anything unrecognised is NOISE. NOISE never opens a trade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class KakaEventType(StrEnum):
    OPEN = "OPEN"
    ADD = "ADD"  # 2nd entry / DCA fill
    TP = "TP"
    TP_REMOVED = "TP_REMOVED"
    STOP_MOVE = "STOP_MOVE"
    BREAKEVEN = "BREAKEVEN"
    STOP_HIT = "STOP_HIT"
    CLOSE = "CLOSE"
    CANCEL = "CANCEL"
    NOISE = "NOISE"


@dataclass(frozen=True)
class KakaEvent:
    type: KakaEventType
    symbol: str | None = None
    side: str | None = None
    entry: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    dca: Decimal | None = None
    reported_pnl: Decimal | None = None
    message_id: int = 0
    raw: str = ""


# A channel number: optional sign, optional currency, thousands separators.
_NUM_PAT = r"(-?\s*\$?\s*\d[\d,]*(?:\.\d+)?)"
# "Entry: $76,150", "SL at -84,100", "Move sl -74,980", "TP 2540"
_CONNECTOR = r"\s*(?:at|to|:|=)?\s*"

_SYMBOL = re.compile(r"\$([A-Za-z]{2,10})\b|\b([A-Z]{2,10})\b")
_KNOWN_SYMBOLS = {
    "BTC",
    "ETH",
    "SOL",
    "HYPE",
    "XRP",
    "BNB",
    "DOGE",
    "ADA",
    "LINK",
    "TON",
    "SUI",
    "AVAX",
}
_SYMBOL_WORD = re.compile(
    r"\b(" + "|".join(sorted(_KNOWN_SYMBOLS, key=len, reverse=True)) + r")\b", re.IGNORECASE
)


def _symbol(text: str) -> str | None:
    """Resolve the traded symbol from "$ETH", "BTC", "Eth long" or the "ETHSHORT" shorthands."""
    for ticker, plain in _SYMBOL.findall(text):
        for candidate in (ticker, plain):
            if not candidate:
                continue
            upper = candidate.upper()
            if upper in _KNOWN_SYMBOLS:
                return f"{upper}USDT"
            if candidate.isupper():
                for known in _KNOWN_SYMBOLS:  # ETHSHORT, BTCLONG
                    if upper.startswith(known) and len(known) >= 3:
                        return f"{known}USDT"
    match = _SYMBOL_WORD.search(text)  # "Eth long", "btc long"
    return f"{match.group(1).upper()}USDT" if match else None


def _num(value: str | None) -> Decimal | None:
    """Parse a channel number: strip currency, thousands separators and a leading dash."""
    if value is None:
        return None
    try:
        cleaned = str(value).replace(",", "").replace("$", "").strip().lstrip("-").strip()
        if not cleaned:
            return None
        parsed = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed > 0 else None


def _extract(text: str, label_pattern: str) -> Decimal | None:
    """First number that follows a label, tolerating ": $", "at ", "-" and commas."""
    match = re.search(rf"{label_pattern}{_CONNECTOR}{_NUM_PAT}", text, re.IGNORECASE)
    return _num(match.group(1)) if match else None


def _side(text: str) -> str | None:
    """Match "Short $ETH", "Long -$ETH", "Eth long", "-$btc", "BTC LONG SETUP"."""
    lowered = text.lower()
    if re.search(r"\bshort\b", lowered):
        return "SHORT"
    if re.search(r"\blong\b", lowered):
        return "LONG"
    return None


def _reported_pnl(text: str) -> Decimal | None:
    match = re.search(r"(profit|loss)\s*:?\s*([+-]?\s*\$?\s*\d[\d,]*(?:\.\d+)?)", text, re.I)
    if not match:
        return None
    value = _num(match.group(2))
    if value is None:
        return None
    return -value if match.group(1).lower().startswith("loss") else value


def parse_kaka_event(text: str, *, message_id: int) -> KakaEvent:
    """Classify one channel message. Unrecognised text is NOISE, never a trade."""
    return replace(_classify(text or ""), message_id=message_id)


def _classify(text: str) -> KakaEvent:
    raw = text
    lowered = raw.lower()

    # Cancels come first: an instruction to stand down must never open a trade.
    if re.search(r"cancel|forget it|don'?t take|do not take|wait and watch", lowered):
        return KakaEvent(KakaEventType.CANCEL, raw=raw)

    # An entry needs a side, a symbol and an explicit stop clause.
    side = _side(raw)
    symbol = _symbol(raw)
    has_stop = re.search(r"\b(sl|stop\s*loss)\b", raw, re.IGNORECASE) is not None
    if side and symbol and has_stop:
        return KakaEvent(
            KakaEventType.OPEN,
            symbol=symbol,
            side=side,
            entry=_extract(raw, r"\b(?:ENTRY|ENRTY|ENTERY|ENTRYY|ENTRY\s*PRICE)\b"),
            stop_loss=_extract(raw, r"(?:🛑\s*)?\b(?:SL|STOP\s*LOSS)\b"),
            take_profit=_extract(raw, r"(?:🎯\s*)?\b(?:TP|TAKE\s*PROFIT|TARGET)\b"),
            dca=_extract(raw, r"(?:➕\s*)?\b(?:2ND\s*DCA|DCA|2ND|SECOND)\b"),
            raw=raw,
        )

    if re.search(r"\b(at\s+be|breakeven|break\s*even)\b", lowered):
        return KakaEvent(KakaEventType.BREAKEVEN, raw=raw)

    if re.search(r"\bsl\s*(got\s*)?hit\b|\bstopped\s+out\b", lowered):
        return KakaEvent(KakaEventType.STOP_HIT, reported_pnl=_reported_pnl(raw), raw=raw)

    if re.search(r"\b(trade\s+)?closed\b|\bclosing\s+the\s+trade\b|\bclose\b", lowered):
        return KakaEvent(KakaEventType.CLOSE, reported_pnl=_reported_pnl(raw), raw=raw)

    if re.search(r"\bremove\s+tp\b|\bcancel\s+tp\b", lowered):
        return KakaEvent(KakaEventType.TP_REMOVED, raw=raw)

    if re.search(r"\btp\b|\btake\s*profit\b|\btarget\b", lowered):
        return KakaEvent(
            KakaEventType.TP,
            take_profit=_extract(raw, r"\b(?:TP|TAKE\s*PROFIT|TARGET)\b"),
            reported_pnl=_reported_pnl(raw),
            raw=raw,
        )

    if re.search(
        r"\b2nd\s*entry\b|\bsecond\s+entry\b|\b2nd\s*dca\b|\btake\s+entry\b|\bdca\b", lowered
    ):
        return KakaEvent(
            KakaEventType.ADD,
            entry=_extract(raw, r"\b(?:2ND\s*ENTRY|SECOND\s+ENTRY|2ND\s*DCA|DCA)\b")
            or _extract(raw, r"\bat\b"),
            stop_loss=_extract(raw, r"\b(?:SL|STOP\s*LOSS)\b"),
            raw=raw,
        )

    if has_stop:
        value = _extract(raw, r"(?:MOVE\s*)?\b(?:SL|STOP\s*LOSS)\b")
        if value is not None:
            return KakaEvent(KakaEventType.STOP_MOVE, stop_loss=value, raw=raw)

    return KakaEvent(KakaEventType.NOISE, raw=raw)
