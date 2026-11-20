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

#: Entry announcements that a management fast path must never swallow. The
#: parser's own veto covers the full templates; the shorter "longed $TIA here |
#: Sl at 0.3902" form carries its stop on the entry line and would otherwise
#: look like an SL_MOVE.
_ENTRY_ANNOUNCEMENT = re.compile(
    r"(?is)\bENTRY\s*:|\bTARGETS?\s*:|\bSTOP\s*LOSS\b"
    r"|\b(?:LONG|SHORT)\s+TRADE\b"
    r"|\b(?:longed|shorted)\b[^\n]{0,40}\b(?:here|at|around|entry)\b"
    r"|\b(?:LONG|SHORT)\b[^\n]{0,20}\bENTRY\b"
)


def looks_like_entry_signal(text: str) -> bool:
    """Whether the message announces an entry and must reach the analyzer."""
    return bool(_ENTRY_ANNOUNCEMENT.search(text or ""))


_CLOSE = re.compile(
    r"\b(?:close|closed|closing|exit|exited)\b.*\b(?:all|full|position|trade|here|at\s+entry|in\s+(?:small\s+)?profit|in\s+(?:small\s+)?loss|for\s+\d+(?:\.\d+)?R(?:\+)?(?:\s+profit)?)\b"
    r"|\b(?:close|closed|closing|exit|exited)\s+(?:all\s+|fully\s+)?[$#]?[A-Z0-9]{1,15}\b"
    r"|\b(?:fully\s+closed|trade\s+closed)\b",
    re.I,
)

_NEGATED_CLOSE = re.compile(
    r"(?<![$#])\b(?:do\s+not|don['’]?t|dont|never|not)\s+(?:close|exit)\b", re.I
)

#: A close stated at the breakeven area, or an imperative "close it". Matched before
#: the conditional guard because the channel often adds an unrelated intention in the
#: same sentence: "$PEPE close around entry Will wait for better opportunity" is an
#: instruction to close, followed by what the author will do next (LIVE 2026-11-17).
_CLOSE_IMPERATIVE = re.compile(
    r"\b(?:close|closed|closing|exit|exited)\b[^\n]{0,20}?"
    r"\b(?:around|near|at)\s+(?:entry|be|breakeven)\b"
    r"|\b(?:close|exit)\s+(?:it|this one|the trade)\b",
    re.I,
)

_CANDLE_ANALYSIS_CLOSE = re.compile(
    r"\b(?:daily|candle|session|weekly|monthly|4h|1h|2h|15m)\s+close\b|\bclose\s+(?:above|below)\b",
    re.I,
)
_HALF_OR_PARTIAL = re.compile(r"\b(?:half|partial)\b|50%", re.I)
#: A booking instruction with no fraction and no TP label: the channel's
#: "$TIA 2.5R Up so far, book some profits here" and "Book some profits also".
#: Owner policy maps it to the existing TP1 booking (half close, stop to entry);
#: it is never a full close.
_BARE_BOOKING = re.compile(
    r"\b(?:book|booking|booked)\b[^\n]{0,30}?\bprofits?\b"
    r"|\bprofits?\b[^\n]{0,20}?\bbook(?:ed|ing)?\b"
    # "take some profit" must carry "some": a bare "take profit 1.2" is an entry
    # template target, not an instruction to close.
    r"|\b(?:take|took|taking)\s+some\s+profits?\b",
    re.I,
)
#: Past narration ("booked profits last week") is a report, not an instruction.
_HISTORICAL_BOOKING = re.compile(
    r"\b(?:last\s+(?:week|month|night|time)|yesterday|previous(?:ly)?|earlier|ago|"
    r"already\s+booked|the\s+other\s+day)\b",
    re.I,
)
_CONDITIONAL = re.compile(r"(?<![$#])\b(?:not|never|don['’]?t|if|when|unless|wait|waiting)\b", re.I)
# "don't forget to move your stoploss to entry" is an instruction, not a veto.
_AFFIRMATIVE_REDIRECT = re.compile(r"\b(?:do\s+not|don['’]?t|never)\s+forget\s+to\b", re.I)


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


def is_bare_booking_text(text: str) -> bool:
    """True when the text books profit without a fraction or a TP label.

    The channel writes "$TIA 2.5R Up so far, book some profits here" and
    "Book some profits also". Neither names how much, so the owner policy maps
    it to the existing TP1 booking (half close, stop to entry) instead of
    silently dropping the instruction.
    """
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _CONDITIONAL.search(line) or _HISTORICAL_BOOKING.search(line):
            continue
        if _HALF_OR_PARTIAL.search(line):
            # "booked half" is the explicit TP1 form and is matched earlier.
            continue
        if _BARE_BOOKING.search(line):
            return True
    return False


def is_symbol_less_booking_text(text: str) -> bool:
    """True when a booking instruction names no symbol and therefore fans out.

    Only this form is narrowed to positions already in profit at execution time;
    an instruction that names its symbol is executed exactly as written.
    """
    if not is_bare_booking_text(text):
        return False
    candidate = text or ""
    return not (_EXPLICIT_SYMBOL.search(candidate) or _FULL_SYMBOL.search(candidate))


def _symbol_candidates(text: str) -> set[str]:
    candidates = [match.group(1).upper() for match in _EXPLICIT_SYMBOL.finditer(text)]
    candidates.extend(match.group(1).upper() for match in _FULL_SYMBOL.finditer(text))
    return {
        token if token.endswith("USDT") else f"{token}USDT"
        for token in candidates
        if _is_symbol_candidate(token)
    }


def parse_source_management(text: str) -> SourceManagement | None:
    """Return a high-confidence single-symbol management instruction."""
    cleaned = _AFFIRMATIVE_REDIRECT.sub("please", text or "")
    normalized = _symbol_candidates(cleaned)
    if not normalized:
        return _generic_stop_management(cleaned)
    if len(normalized) > 1:
        return None
    return _instruction_for_symbol(cleaned, normalized.pop())


def parse_source_managements(text: str) -> list[SourceManagement]:
    """Every management instruction in one message, one per named symbol.

    The channel routinely posts one instruction covering several symbols
    ("$WLD $BTC TP2 booked"). Collapsing that to a single instruction returned
    ``None`` and dropped the whole message; each symbol is now enrolled on its
    own, deduplicated by ``(source_message_id, revision, symbol, action)``.
    """
    cleaned = _AFFIRMATIVE_REDIRECT.sub("please", text or "")
    normalized = _symbol_candidates(cleaned)
    if not normalized:
        generic = _generic_stop_management(cleaned)
        return [generic] if generic is not None else []
    updates = (_instruction_for_symbol(cleaned, symbol) for symbol in sorted(normalized))
    return [update for update in updates if update is not None]


def _instruction_for_symbol(text: str, symbol: str) -> SourceManagement | None:
    """The instruction this text gives for one named symbol, if any."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for line in lines:
        if (
            _CLOSE_IMPERATIVE.search(line)
            and not _NEGATED_CLOSE.search(line)
            and not _CANDLE_ANALYSIS_CLOSE.search(line)
            # "closed half at entry" is the TP1 form; it must reach the TP1 branch.
            and not _HALF_OR_PARTIAL.search(line)
        ):
            return SourceManagement(symbol=symbol, action=ManagementAction.CLOSE)
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
        if is_bare_booking_text(line):
            return SourceManagement(symbol=symbol, action=ManagementAction.TP1_BOOKED)
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
        if is_bare_booking_text(text):
            return SourceManagement(symbol=symbol, action=ManagementAction.TP1_BOOKED)
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
    """Honor a symbol-less stop or booking instruction for every open position.

    A symbol-less close stays ambiguous and is ignored. A symbol-less booking
    ("Book some profits also") reaches every open position because the owner
    made that policy explicit; the executor then books only the positions that
    are already in profit and records ``not-in-profit`` for the rest. Moving
    stops to entry is safe because every owned position already trades with a
    stop. Symbol-less parsing keeps the live lane from silently dropping the
    channel's most common reminder.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if _CONDITIONAL.search(line):
            continue
        if _SL_TO_ENTRY.search(line):
            return SourceManagement(symbol=None, action=ManagementAction.SL_TO_ENTRY)
        if is_bare_booking_text(line):
            # Owner policy: a symbol-less booking reaches every open position, and
            # the executor books only the ones already in profit.
            return SourceManagement(symbol=None, action=ManagementAction.TP1_BOOKED)
    if not _CONDITIONAL.search(text) and _SL_TO_ENTRY.search(text):
        return SourceManagement(symbol=None, action=ManagementAction.SL_TO_ENTRY)
    if is_bare_booking_text(text):
        return SourceManagement(symbol=None, action=ManagementAction.TP1_BOOKED)
    return None


def _is_symbol_candidate(token: str) -> bool:
    base = token[:-4] if token.endswith("USDT") else token
    return (
        token not in _RESERVED_SYMBOL_TOKENS
        and base not in _RESERVED_SYMBOL_TOKENS
        and re.fullmatch(r"TP\d+", base) is None
    )
