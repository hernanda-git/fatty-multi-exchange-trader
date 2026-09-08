"""Recognize source-channel trade-management instructions without provider mutation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_SYMBOL = re.compile(r"\$?([A-Z0-9]{2,15})(?:USDT)?\b", re.I)
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
    symbol_match = _SYMBOL.search(text or "")
    if symbol_match is None:
        return None
    if _TP1.search(text):
        action = ManagementAction.TP1_BOOKED
    elif _SL_TO_ENTRY.search(text):
        action = ManagementAction.SL_TO_ENTRY
    elif _CLOSE.search(text):
        action = ManagementAction.CLOSE
    else:
        return None
    token = symbol_match.group(1).upper()
    symbol = token if token.endswith("USDT") else f"{token}USDT"
    return SourceManagement(symbol=symbol, action=action)
