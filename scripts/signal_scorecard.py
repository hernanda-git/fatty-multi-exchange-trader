#!/usr/bin/env python3
"""Read-only per-dispatch scorecard: which signals paid, which did not.

Runs inside the deployed image (it owns the DB credentials):

    docker compose exec -T dispatcher-bitget /app/.venv/bin/python \
        /app/scripts/signal_scorecard.py

SCORECARD_WINDOW (default ``30 days``) and SCORECARD_JSON=1 are optional.
The transaction is read-only: this report can never mutate the ledger.
"""

import json
import os
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row

from fatty_trader.ops.signal_scorecard import attribute, format_report, summarize

_DISPATCHES = """
SELECT d.id AS dispatch_id,
       s.pair_token || 'USDT' AS symbol,
       s.direction, s.entry_price, s.stop_loss,
       d.state, d.terminal_reason, d.created_at
FROM dispatches d
JOIN canonical_signals s ON s.id = d.source_id
WHERE d.created_at >= now() - %s::interval
ORDER BY d.created_at
"""

_INTENTS = """
SELECT dispatch_id, client_order_id
FROM live_order_intents
WHERE role = 'ENTRY' AND dispatch_id IS NOT NULL
  AND created_at >= now() - %s::interval
"""

_FILLS = """
SELECT symbol, client_order_id, price, quantity, realized_pnl, fee, filled_at
FROM fills
WHERE filled_at >= now() - %s::interval - interval '1 day'
"""


def main() -> int:
    window = os.environ.get("SCORECARD_WINDOW", "30 days")
    with psycopg.connect(
        options="-c default_transaction_read_only=on", row_factory=dict_row
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(_DISPATCHES, (window,))
            dispatches = cursor.fetchall()
            cursor.execute(_INTENTS, (window,))
            intents = cursor.fetchall()
            cursor.execute(_FILLS, (window,))
            fills = cursor.fetchall()
    trades = attribute(
        dispatches=dispatches,
        intents=intents,
        fills=fills,
        now=datetime.now(UTC),
    )
    summary = summarize(trades)
    print(format_report(trades, summary))
    if os.environ.get("SCORECARD_JSON") == "1":
        print(json.dumps(summary, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
