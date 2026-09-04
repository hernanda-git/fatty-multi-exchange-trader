import hashlib
import re
from decimal import Decimal, InvalidOperation

from fatty_trader.domain.enums import Direction
from fatty_trader.domain.models import CanonicalSignal

_CHANNEL = re.compile(
    r"(?is)^\s*(?:#|\$)?(?P<pair>[A-Z0-9]{2,20})(?:\s+\$?(?P<pair2>[A-Z0-9]{2,20}))?\s+"
    r"(?P<direction>LONG|SHORT).*?(?:ENTRY\s*:\s*|MARKET\s+)(?P<entry>\d+(?:\.\d+)?).*?"
    r"(?:TARGET|TP)\s*:?\s*(?P<tp>\d+(?:\.\d+)?).*?"
    r"(?:STOPLOSS|SL)\s*:?\s*(?P<sl>\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)
_RIGID = re.compile(
    r"^\s*(?P<pair>[A-Z0-9]{2,20})\s+(?P<direction>LONG|SHORT)\s+MARKET\s+"
    r"SL\s+(?P<sl>\d+(?:\.\d+)?)\s+TP\s+(?P<tp>\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)


def parse_explicit_signal(text: str, *, message_id: int) -> CanonicalSignal | None:
    """Accept rigid explicit trade syntax without using market data."""
    match = _CHANNEL.match(text) or _RIGID.match(text)
    if match is None:
        return None
    try:
        direction = Direction(match["direction"].upper())
        stop_loss = Decimal(match["sl"])
        take_profits = (Decimal(match["tp"]),)
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
