"""Read-only public market price lookup for market-entry scalp signals.

Scalp messages from the source state a stop but no entry price. The entry is therefore
"the market, now", and this module is the only place that price may come from: a public
Bitget endpoint, no credentials, no side effects. Every failure returns None, which the
parser treats as "no signal" rather than guessing a price.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.request import urlopen

_TICKER_URL = (
    "https://api.bitget.com/api/v2/mix/market/ticker?symbol={symbol}USDT&productType=USDT-FUTURES"
)
_CACHE_TTL_SECONDS = 10.0
_cache: dict[str, tuple[float, Decimal]] = {}


def public_last_price(
    pair_token: str, *, timeout: float = 5.0, now: float | None = None
) -> Decimal | None:
    """Return the last traded price for ``<pair_token>USDT``, or None.

    Cached for a few seconds so a burst of scalp messages does not fan out into a burst
    of HTTP calls; the cache only ever holds a price that was actually observed.
    """
    symbol = f"{pair_token.upper()}USDT"
    clock = time.monotonic() if now is None else now
    cached = _cache.get(symbol)
    if cached is not None and 0 <= clock - cached[0] <= _CACHE_TTL_SECONDS:
        return cached[1]
    try:
        with urlopen(_TICKER_URL.format(symbol=symbol), timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        data = payload.get("data")
        row: Any = data[0] if isinstance(data, list) and data else data
        price = Decimal(str(row["lastPr"]))
        if price <= 0:
            return None
    except (OSError, KeyError, TypeError, ValueError, InvalidOperation):
        return None
    _cache[symbol] = (clock, price)
    return price
