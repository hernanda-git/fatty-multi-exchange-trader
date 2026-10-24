"""Recognize source-channel trade-management instructions without provider mutation."""

from __future__ import annotations

import re
from dataclasses import dataclass
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

_CLOSE = re.compile(
    r"\b(?:close|closed|closing|exit|exited)\b.*\b(?:all|full|position|trade|here|at\s+entry|in\s+(?:small\s+)?profit|in\s+(?:small\s+)?loss|for\s+\d+(?:\.\d+)?R(?:\+)?(?:\s+profit)?)\b"
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


class ManagementAction(StrEnum):
    TP1_BOOKED = "TP1_BOOKED"
    TP_BOOKED = "TP_BOOKED"
    SL_TO_ENTRY = "SL_TO_ENTRY"
    CLOSE = "CLOSE"


@dataclass(frozen=True)
class SourceManagement:
    symbol: str
    action: ManagementAction


def parse_source_management(text: str) -> SourceManagement | None:
    """Return a high-confidence management instruction, otherwise None."""
    candidates = [match.group(1).upper() for match in _EXPLICIT_SYMBOL.finditer(text or "")]
    candidates.extend(match.group(1).upper() for match in _FULL_SYMBOL.finditer(text or ""))
    normalized = {
        token if token.endswith("USDT") else f"{token}USDT"
        for token in candidates
        if _is_symbol_candidate(token)
    }
    if len(normalized) != 1:
        return None
    if re.search(r"(?<![$#])\b(?:not|never|don['’]?t|if|when|unless|wait|waiting)\b", text, re.I):
        return None
    if _TP1.search(text):
        action = ManagementAction.TP1_BOOKED
    elif _TP_BOOKED.search(text):
        action = ManagementAction.TP_BOOKED
    elif _SL_TO_ENTRY.search(text):
        action = ManagementAction.SL_TO_ENTRY
    elif (
        _CLOSE.search(text)
        and not _NEGATED_CLOSE.search(text)
        and not _CANDLE_ANALYSIS_CLOSE.search(text)
    ):
        action = ManagementAction.CLOSE
    else:
        return None
    return SourceManagement(symbol=normalized.pop(), action=action)


def _is_symbol_candidate(token: str) -> bool:
    base = token[:-4] if token.endswith("USDT") else token
    return (
        token not in _RESERVED_SYMBOL_TOKENS
        and base not in _RESERVED_SYMBOL_TOKENS
        and re.fullmatch(r"TP\d+", base) is None
    )
