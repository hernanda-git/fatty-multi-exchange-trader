#!/usr/bin/env python3
# ruff: noqa: E501
"""Settle `unknown` non-entry intents against the venue (GET-only, operator tool).

An emergency close or protection plan that the provider no longer holds leaves its
durable intent `unknown` forever: recovery then refuses to run (a non-entry intent is
not terminal) and an approved baseline is blocked too. This runs the repo's own
reconciler for exactly those intents and persists the provider verdict.

Refuses to run unless the account is flat for the intent's symbol: an open position is
never settled from absence of evidence.

    docker compose exec -T dispatcher-bitget /app/.venv/bin/python \
        scripts/reconcile_unknown_intents.py [--symbol WLDUSDT] [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import psycopg  # noqa: E402

from fatty_trader.exchanges.bitget.client import BitgetRestClient  # noqa: E402
from fatty_trader.exchanges.bitget.reconciliation import (  # noqa: E402
    reconcile_unknown_intent,
)
from fatty_trader.storage.live_intents import PostgresLiveIntentStore  # noqa: E402

SETTLED = {"reconciled", "cancelled", "rejected", "filled"}


def unknown_non_entry_oids(symbol: str | None) -> list[str]:
    """Client order ids of unknown non-entry intents (read-only)."""
    sql = (
        "SELECT client_order_id FROM live_order_intents WHERE exchange='bitget' "
        "AND role <> 'ENTRY' AND state = 'unknown' "
        + ("AND symbol = %s " if symbol else "")
        + "ORDER BY created_at"
    )
    with psycopg.connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql, (symbol,) if symbol else ())
        rows = cursor.fetchall()
    return [str(list(row.values())[0] if isinstance(row, dict) else row[0]) for row in rows]


async def settle_absent_protection(
    client: BitgetRestClient, store: PostgresLiveIntentStore, intent
) -> int:
    """Settle a protection intent the venue provably no longer holds.

    Guarded on every side: the symbol must be flat, the venue must list no plan and no
    pending order for it, and the intent must have filled nothing. Only then is the
    plan's absence proof that it is gone rather than unproven.
    """
    if intent.filled_qty != 0 or intent.state != "unknown":
        print(f"  not settled: {intent.client_oid} is not an unfilled unknown intent")
        return 0
    plans = await client.get_pending_plan_orders(intent.symbol)
    orders = await client.get_pending_orders()
    rows = plans.get("data") if isinstance(plans, dict) else plans
    if not isinstance(rows, list) or rows:
        print(f"  not settled: {intent.symbol} still lists a plan")
        return 0
    if not isinstance(orders, list) or any(
        isinstance(row, dict) and str(row.get("symbol") or "").upper() == intent.symbol
        for row in orders
    ):
        print(f"  not settled: {intent.symbol} still lists a pending order")
        return 0
    with psycopg.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE live_order_intents SET state = 'cancelled' WHERE client_order_id = %s "
            "AND state = 'unknown' AND role <> 'ENTRY' AND filled_qty = 0",
            (intent.client_oid,),
        )
        changed = cursor.rowcount
        cursor.execute(
            "INSERT INTO notifications_outbox (id, dedup_key, payload) VALUES "
            "(gen_random_uuid(), %s, %s::jsonb) ON CONFLICT (dedup_key) DO NOTHING",
            (
                f"protection-absent-settled:{intent.client_oid}",
                json.dumps(
                    {
                        "kind": "execution-alert",
                        "intent": intent.client_oid,
                        "symbol": intent.symbol,
                        "reason": "protection-plan-absent-settled",
                        "evidence": "flat position, no pending plan, no pending order, filled_qty=0",
                    },
                    sort_keys=True,
                ),
            ),
        )
        connection.commit()
    if changed:
        print(f"  settled {intent.client_oid} as cancelled (plan absent, flat, unfilled)")
    return 1 if changed else 0


async def run(symbol: str | None, apply: bool, settle_flat_protection: bool) -> int:
    mode = os.environ.get("BITGET_MODE", "LIVE")
    client = BitgetRestClient(
        os.environ["BITGET_API_KEY"],
        os.environ["BITGET_API_SECRET"],
        os.environ["BITGET_API_PASSPHRASE"],
        mode=mode,
        max_get_retries=0,
    )
    store = PostgresLiveIntentStore(psycopg.connect, console_enabled=False)
    settled = 0
    try:
        for client_oid in unknown_non_entry_oids(symbol):
            intent = store.get(client_oid)
            if intent is None:
                continue
            positions = await client.get_single_position(intent.symbol)
            rows = positions.get("data") if isinstance(positions, dict) else positions
            if not isinstance(rows, list) or rows:
                print(f"skip {intent.client_oid}: symbol is not flat")
                continue
            reconciled = await reconcile_unknown_intent(
                intent,
                read_order_detail=lambda sym, oid: client.get_order_detail(sym, client_oid=oid),
                read_fills=client.get_fills,
                read_position=client.get_single_position,
                read_pending_orders=lambda _symbol: client.get_pending_orders(),
            )
            print(f"{intent.client_oid}: {intent.state} -> {reconciled.state}")
            if apply and reconciled.state in SETTLED:
                store.update(reconciled)
                settled += 1
            elif reconciled.state == "unknown" and settle_flat_protection:
                settled += await settle_absent_protection(client, store, intent)
            elif apply:
                print(f"  not persisted: {reconciled.state} is not terminal")
    finally:
        await client.aclose()
    print(f"settled={settled} apply={apply}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--settle-flat-protection",
        action="store_true",
        help="treat a plan the venue provably no longer holds as cancelled (flat + unfilled)",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.symbol, args.apply, args.settle_flat_protection))


if __name__ == "__main__":
    raise SystemExit(main())
