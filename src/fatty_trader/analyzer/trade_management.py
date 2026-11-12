"""Recognize source-channel trade-management instructions without provider mutation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

_EXPLICIT_SYMBOL = re.compile(r"[$#]([A-Z0-9]{1,15})\b", re.I)
_FULL_SYMBOL = re.compile(r"\b([A-Z0-9]{1,15}USDT)\b", re.I)
_RESERVED_SYMBOL_TOKENS = {
    "BE",
    "CLOSE",
    "ENTRY",
    "EXIT",
    "LONG",
    "SHORT",
    "SL",
    "STOPLOSS",
    "TARGET",
    "TARGETS",
    "TO",
    "TP",
    "TP1",
    "TRADE",
}
_TP_ACTION = r"(?:book(?:ed|ing)?|take(?:n)?|hit|done|smashed|here)"

_TP1 = re.compile(
    rf"(?:"
    rf"\b(?:tp\s*1|first\s+tp)\b.*\b(?:{_TP_ACTION}|on\b)\b"
    rf"|\b(?:{_TP_ACTION}|on\b).*\b(?:tp\s*1|first\s+tp)\b"
    rf"|\b(?:tp\s*1|first\s+tp)\b"
    rf"|\b(?:close|closed|closing|exit|exited|take|taken|took|book|booked|booking)\b.*(?:\b(?:half|partial)\b|50%)"
    rf"|(?:\b(?:half|partial)\b|50%).*\b(?:close|closed|closing|exit|exited|position|trade|booked|booking|profit)\b"
    rf")",
    re.I,
)

_TP_BOOKED = re.compile(
    rf"(?:"
    rf"\b(?:tp(?:\s*\d+)?|(?:first|second|third|fourth|fifth)\s+tp|take\s*profit(?:\s*\d+)?|full\s+tp)\b.*\b(?:{_TP_ACTION}|on\b)\b"
    rf"|\b(?:{_TP_ACTION}|on\b).*\b(?:tp(?:\s*\d+)?|(?:first|second|third|fourth|fifth)\s+tp|take\s*profit(?:\s*\d+)?|full\s+tp)\b"
    rf"|\bfull\s+tped\b"
    rf")",
    re.I,
)

_SL_TO_ENTRY = re.compile(
    r"\b(?:sl|stop\s*loss)\b.*\b(?:to|at)\s+(?:entry|be|breakeven)\b"
    r"|\b(?:move|moving|moved)\b.*\b(?:sl|stop\s*loss)\b.*\b(?:to|at)\s+(?:entry|be|breakeven)\b",
    re.I,
)

#: A stop move to an explicit price ("sl moved to 4.14 on $SNXX"). The parser
#: demands a management cue (move/shift/raise/trail or "now at") so an entry
#: template's "STOPLOSS 4.072" line can never be mistaken for a stop mutation.
#: Entry-signal markers are vetoed separately below.
_SL_MOVE = re.compile(
    r"(?is)"
    r"\b(?:sl|stop\s*loss|stoploss)\b[^\n]{0,30}?"
    r"\b(?:moved|move|moving|shift(?:ed|ing)?|raise[ds]?|raising|trail(?:ed|ing)?|"
    r"is\s+now|now)\b[^\n0-9]{0,15}?(?P<price1>\d+(?:\.\d+)?)"
    r"|\b(?:moved|move|moving|shift(?:ed|ing)?|raise[ds]?|raising|trail(?:ed|ing)?)\b"
    r"[^\n]{0,30}?\b(?:sl|stop\s*loss|stoploss)\b[^\n0-9]{0,15}?(?P<price2>\d+(?:\.\d+)?)"
    r"|\b(?:sl|stop\s*loss|stoploss)\b\s*(?:is\s+)?(?:now\s+)?(?:at|to)\s*"
    r"(?P<price3>\d+(?:\.\d+)?)"
)
_ENTRY_SIGNAL_MARKER = re.compile(
    r"(?is)\bENTRY\s*:|\bTARGETS?\s*:|\b(?:LONG|SHORT)\b[^\n]{0,50}\bSTOP\s*LOSS\b"
)

_CLOSE = re.compile(
    r"\b(?:close|closed|closing|exit|exited)\b.*\b(?:all|full|position|trade|here|at\s+entry|in\s+(?:small\s+)?profit|in\s+(?:small\s+)?loss|for\s+\d+(?:\.\d+)?R(?:\+)?(?:\s+profit)?)\b"
    r"|\b(?:close|closed|closing|exit|exited)\s+(?:all\s+|fully\s+)?[$#]?[A-Z0-9]{1,15}\b"
    r"|\b(?:fully\s+closed|trade\s+closed)\b",
    re.I,
)

_NEGATED_CLOSE = re.compile(
    r"(?<![$#])\b(?:do\s+not|don['’]?t|dont|never|not)\s+(?:close|exit)\b", re.I
)

_CANDLE_ANALYSIS_CLOSE = re.compile(
    r"\b(?:daily|candle|session|weekly|monthly|4h|1h|2h|15m)\s+close\b|\bclose\s+(?:above|below)\b",
    re.I,
)
_HALF_OR_PARTIAL = re.compile(r"\b(?:half|partial)\b|50%", re.I)
_CONDITIONAL = re.compile(
    r"(?<![$#])\b(?:not|never|don['’]?t|if|when|unless|wait|waiting)\b", re.I
)
# "don't forget to move your stoploss to entry" is an instruction, not a veto.
_AFFIRMATIVE_REDIRECT = re.compile(
    r"\b(?:do\s+not|don['’]?t|never)\s+forget\s+to\b", re.I
)


class ManagementAction(StrEnum):
    TP1_BOOKED = "TP1_BOOKED"
    TP_BOOKED = "TP_BOOKED"
    SL_TO_ENTRY = "SL_TO_ENTRY"
    SL_MOVE = "SL_MOVE"
    CLOSE = "CLOSE"


@dataclass(frozen=True)
class SourceManagement:
    """``symbol`` is None only for instructions that safely scope to every position."""

    symbol: str | None
    action: ManagementAction
    target_price: Decimal | None = None


def parse_source_management(text: str) -> SourceManagement | None:
    """Return a high-confidence management instruction, otherwise None."""
    text = _AFFIRMATIVE_REDIRECT.sub("please", text or "")
    candidates = [match.group(1).upper() for match in _EXPLICIT_SYMBOL.finditer(text)]
    candidates.extend(match.group(1).upper() for match in _FULL_SYMBOL.finditer(text))
    normalized = {
        token if token.endswith("USDT") else f"{token}USDT"
        for token in candidates
        if _is_symbol_candidate(token)
    }
    if not normalized:
        return _generic_stop_management(text)
    if len(normalized) > 1:
        return None
    symbol = normalized.pop()

    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for line in lines:
        if _CONDITIONAL.search(line):
            continue
        if _TP1.search(line):
            return SourceManagement(symbol=symbol, action=ManagementAction.TP1_BOOKED)
        if _TP_BOOKED.search(line):
            return SourceManagement(symbol=symbol, action=ManagementAction.TP_BOOKED)
        if _SL_TO_ENTRY.search(line):
            return SourceManagement(symbol=symbol, action=ManagementAction.SL_TO_ENTRY)
        stop_move = _stop_move_price(line)
        if stop_move is not None:
            return SourceManagement(
                symbol=symbol, action=ManagementAction.SL_MOVE, target_price=stop_move
            )
        if (
            _CLOSE.search(line)
            and not _NEGATED_CLOSE.search(line)
            and not _CANDLE_ANALYSIS_CLOSE.search(line)
            and not _HALF_OR_PARTIAL.search(line)
        ):
            return SourceManagement(symbol=symbol, action=ManagementAction.CLOSE)

    if not _CONDITIONAL.search(text):
        if _TP1.search(text):
            return SourceManagement(symbol=symbol, action=ManagementAction.TP1_BOOKED)
        if _TP_BOOKED.search(text):
            return SourceManagement(symbol=symbol, action=ManagementAction.TP_BOOKED)
        if _SL_TO_ENTRY.search(text):
            return SourceManagement(symbol=symbol, action=ManagementAction.SL_TO_ENTRY)
        stop_move = _stop_move_price(text)
        if stop_move is not None:
            return SourceManagement(
                symbol=symbol, action=ManagementAction.SL_MOVE, target_price=stop_move
            )
        if (
            _CLOSE.search(text)
            and not _NEGATED_CLOSE.search(text)
            and not _CANDLE_ANALYSIS_CLOSE.search(text)
            and not _HALF_OR_PARTIAL.search(text)
        ):
            return SourceManagement(symbol=symbol, action=ManagementAction.CLOSE)
    return None


def _stop_move_price(text: str) -> Decimal | None:
    """Return the explicit stop price when the text is a stop-move instruction."""
    if _ENTRY_SIGNAL_MARKER.search(text):
        return None
    match = _SL_MOVE.search(text)
    if match is None:
        return None
    raw = match.group("price1") or match.group("price2") or match.group("price3")
    try:
        price = Decimal(raw)
    except (InvalidOperation, TypeError):
        return None
    return price if price.is_finite() and price > 0 else None


def _generic_stop_management(text: str) -> SourceManagement | None:
    """Honor a symbol-less stop-to-entry instruction for every open position.

    Closing or booking without a symbol stays ambiguous and is ignored; moving
    stops to entry is safe to widen only because every owned position is already
    trading with a stop. Symbol-less parsing keeps the live lane from silently
    dropping the channel's most common reminder.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if _CONDITIONAL.search(line):
            continue
        if _SL_TO_ENTRY.search(line):
            return SourceManagement(symbol=None, action=ManagementAction.SL_TO_ENTRY)
    if not _CONDITIONAL.search(text) and _SL_TO_ENTRY.search(text):
        return SourceManagement(symbol=None, action=ManagementAction.SL_TO_ENTRY)
    return None


def _is_symbol_candidate(token: str) -> bool:
    base = token[:-4] if token.endswith("USDT") else token
    return (
        token not in _RESERVED_SYMBOL_TOKENS
        and base not in _RESERVED_SYMBOL_TOKENS
        and re.fullmatch(r"TP\d+", base) is None
    )
