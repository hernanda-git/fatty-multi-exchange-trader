import hashlib
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from fatty_trader.analyzer.source_guard import entry_stands_down
from fatty_trader.domain.enums import Direction
from fatty_trader.domain.models import CanonicalSignal

_CHANNEL = re.compile(
    r"(?is)^\s*(?:#|\$)?(?P<pair>[A-Z0-9]{2,20})(?:\s+\$?(?P<pair2>[A-Z0-9]{2,20}))?\s+"
    r"(?P<direction>LONG|SHORT).*?(?:ENTRY\s*:\s*|MARKET\s+)(?P<entry>\d+(?:\.\d+)?).*?"
    r"(?:TARGETS?|TPS?)\s*:?\s*(?P<tps>\d+(?:\.\d+)?(?:\s*(?:-|,|/)\s*\d+(?:\.\d+)?){0,4}).*?"
    r"(?:STOPLOSS|SL)\s*:?\s*(?P<sl>\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)
_NATURAL_STOP_ONLY = re.compile(
    r"^\s*(?P<direction>LONGING|SHORTING|LONG|SHORT)\s+"
    r"(?:here\s+)?\$?(?P<pair>[A-Z0-9]{2,20})\s+"
    r"(?:here\s+)?(?:around|at|near)\s+"
    r"(?P<entry>\d+(?:\.\d+)?).*?"
    r"(?:STOPLOSS|STOP LOSS|SL)\s*:?\s*(?P<sl>\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_RIGID = re.compile(
    r"^\s*(?P<pair>[A-Z0-9]{2,20})\s+(?P<direction>LONG|SHORT)\s+MARKET\s+"
    r"SL\s+(?P<sl>\d+(?:\.\d+)?)\s+TP\s+(?P<tp>\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)
# The source also posts the same idea without any scalp/here marker:
#   "$ENA long, sl below 0.27845"
# so the shape required is: pair + a long/short verb first, and a stop-loss clause with a
# number last. Anything that merely mentions a stop mid-sentence stays unparsed, and an
# entry price in the text always wins (that path is tried before this one).
_SCALP_MARKET_STOP_ONLY = re.compile(
    r"(?is)^\s*(?:#|\$)?(?P<pair>[A-Z0-9]{2,20})"
    r"(?:\s+\$?(?P<pair2>[A-Z0-9]{2,20}))?\s+"
    r"(?P<direction>LONGED|SHORTED|LONGING|SHORTING|LONG|SHORT)\b.*?"
    r"(?:STOPLOSS|STOP\s*LOSS|SL)\b[^0-9]*(?P<sl>\d+(?:\.\d+)?)\s*$"
)


def parse_explicit_signal(
    text: str,
    *,
    message_id: int,
    market_price_lookup: Callable[[str], Decimal | None] | None = None,
) -> CanonicalSignal | None:
    """Accept rigid explicit trade syntax without using market data.

    ``market_price_lookup`` is consulted only for scalp messages that state a stop but no
    entry price. It must return the current market price for the pair token, or None; a
    missing price means no signal, never a guess.
    """
    if entry_stands_down(text):
        return None
    match = _CHANNEL.match(text) or _NATURAL_STOP_ONLY.match(text) or _RIGID.match(text)
    if match is None:
        return _scalp_market_signal(
            text, message_id=message_id, market_price_lookup=market_price_lookup
        )
    try:
        direction_text = match["direction"].upper()
        direction = {
            "LONGING": Direction.LONG,
            "SHORTING": Direction.SHORT,
            "LONG": Direction.LONG,
            "SHORT": Direction.SHORT,
        }[direction_text]
        stop_loss = Decimal(match["sl"])
        target_text = match.groupdict().get("tps") or match.groupdict().get("tp")
        take_profits = (
            tuple(Decimal(value) for value in re.findall(r"\d+(?:\.\d+)?", target_text))
            if target_text
            else ()
        )
        if not match.groupdict().get("entry") and not take_profits:
            return None
        entry = (
            Decimal(match["entry"])
            if match.groupdict().get("entry")
            else (
                stop_loss + (take_profits[0] - stop_loss) / 2
                if direction is Direction.LONG
                else stop_loss - (stop_loss - take_profits[0]) / 2
            )
        )
        return CanonicalSignal(
            source_message_id=message_id,
            source_revision=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            pair_token=(
                (match.groupdict().get("pair2") or match["pair"]).upper().removesuffix("USDT")
            ),
            direction=direction,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profits=take_profits,
        )
    except (InvalidOperation, ValueError):
        return None


def _scalp_market_signal(
    text: str,
    *,
    message_id: int,
    market_price_lookup: Callable[[str], Decimal | None] | None,
) -> CanonicalSignal | None:
    """Turn a stop-only scalp message into a market-entry signal, or refuse.

    Refusal is the default: without a price source, or when the current market has
    already crossed the stated stop, there is no tradable setup. CanonicalSignal's own
    geometry validator rejects a long whose stop sits at or above its entry (and the
    mirror case for shorts), so a dead scalp can never become a signal.
    """
    match = _SCALP_MARKET_STOP_ONLY.match(text)
    if match is None or market_price_lookup is None:
        return None
    pair_token = (match.groupdict().get("pair2") or match["pair"]).upper().removesuffix("USDT")
    entry = market_price_lookup(pair_token)
    if entry is None or entry <= 0:
        return None
    try:
        return CanonicalSignal(
            source_message_id=message_id,
            source_revision=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            pair_token=pair_token,
            direction={
                "LONGED": Direction.LONG,
                "LONGING": Direction.LONG,
                "LONG": Direction.LONG,
                "SHORTED": Direction.SHORT,
                "SHORTING": Direction.SHORT,
                "SHORT": Direction.SHORT,
            }[match["direction"].upper()],
            entry_price=entry,
            stop_loss=Decimal(match["sl"]),
            take_profits=(),
        )
    except (InvalidOperation, ValueError):
        return None
