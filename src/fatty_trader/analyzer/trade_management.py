"""Recognize source-channel trade-management instructions without provider mutation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_EXPLICIT_SYMBOL = re.compile(r"[$#]([A-Z0-9]{2,15})\b", re.I)
_FULL_SYMBOL = re.compile(r"\b([A-Z0-9]{2,15}USDT)\b", re.I)
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
_TP1 = re.compile(
    r"\b(?:tp\s*1|first\s+tp)\b.*\b(?:book(?:ed)?|take(?:n)?|hit)\b"
    r"|\b(?:book(?:ed)?|take(?:n)?|hit)\b.*\b(?:tp\s*1|first\s+tp)\b",
    re.I,
)
_SL_TO_ENTRY = re.compile(r"\b(?:sl|stop\s*loss)\b.*\b(?:to|at)\s+(?:entry|be|breakeven)\b", re.I)
_CLOSE = re.compile(r"\b(?:close|exit)\b.*\b(?:all|full|position)\b", re.I)


class ManagementAction(StrEnum):
    TP1_BOOKED = "TP1_BOOKED"
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
    if _TP1.search(text):
        action = ManagementAction.TP1_BOOKED
    elif _SL_TO_ENTRY.search(text):
        action = ManagementAction.SL_TO_ENTRY
    elif _CLOSE.search(text):
        action = ManagementAction.CLOSE
    else:
        return None
    return SourceManagement(symbol=normalized.pop(), action=action)


def _is_symbol_candidate(token: str) -> bool:
    base = token[:-4] if token.endswith("USDT") else token
    return token not in _RESERVED_SYMBOL_TOKENS and base not in _RESERVED_SYMBOL_TOKENS
