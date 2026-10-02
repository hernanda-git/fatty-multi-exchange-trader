#!/usr/bin/env python3
"""Retired raw LIVE entry bypass; use the durable dispatcher for approved trades.

For the original read-only checks use check_position_btc.py (positions, account,
fills) or _probe_position.py SYMBOL. No flag authorizes a new trade here.
"""


def main(argv=None):
    print(
        "REFUSED: raw entries lack durable intent, risk preflight and protection guarantees; "
        "use the production dispatcher. Inspect with check_position_btc.py instead."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
