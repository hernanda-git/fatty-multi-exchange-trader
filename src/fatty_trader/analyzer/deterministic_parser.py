import hashlib
import re
from decimal import Decimal, InvalidOperation

from fatty_trader.domain.enums import Direction
from fatty_trader.domain.models import CanonicalSignal

_PATTERN = re.compile(
    r"^\s*(?P<pair>[A-Z0-9]{2,20})\s+(?P<direction>LONG|SHORT)\s+MARKET\s+"
    r"SL\s+(?P<sl>\d+(?:\.\d+)?)"
    r"(?:\s+TP\s+(?P<tp>\d+(?:\.\d+)?))?\s*$",
    re.IGNORECASE,
)


def parse_explicit_signal(text: str, *, message_id: int) -> CanonicalSignal | None:
    """Accept only deliberately rigid text when Codex is unavailable or has failed."""
    match = _PATTERN.match(text)
    if match is None:
        return None
    try:
        direction = Direction(match["direction"].upper())
        stop_loss = Decimal(match["sl"])
        if not match["tp"]:
            return None
        take_profits = (Decimal(match["tp"]),)
        # A fallback does not query the market. Its midpoint is only used to validate
        # source geometry; the execution service must replace it with a fresh venue price.
        if direction is Direction.LONG:
            entry_price = stop_loss + (take_profits[0] - stop_loss) / 2
        else:
            entry_price = stop_loss - (stop_loss - take_profits[0]) / 2
        return CanonicalSignal(
            source_message_id=message_id,
            source_revision=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            pair_token=match["pair"].upper().removesuffix("USDT"),
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profits=take_profits,
        )
    except (InvalidOperation, ValueError):
        return None
