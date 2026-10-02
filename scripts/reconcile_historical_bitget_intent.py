"""Auditably terminalize a historical Bitget intent only after a flat provider read."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from uuid import uuid4

import psycopg

from fatty_trader.exchanges.bitget.client import BitgetRestClient


async def _provider_snapshot() -> dict[str, object]:
    client = BitgetRestClient(
        os.environ["BITGET_API_KEY"],
        os.environ["BITGET_API_SECRET"],
        os.environ["BITGET_API_PASSPHRASE"],
        mode=os.environ["BITGET_MODE"],
    )
    try:
        positions = await client.get_all_positions()
        orders = await client.get_pending_orders()
    finally:
        await client.aclose()
    if not isinstance(positions, list) or not isinstance(orders, list):
        raise ValueError("unknown provider snapshot shape")
    if not all(isinstance(row, dict) for row in orders):
        raise ValueError("unknown provider order evidence")
    open_positions = []
    for row in positions:
        if not isinstance(row, dict):
            raise ValueError("unknown provider position evidence")
        try:
            quantity = Decimal(str(row.get("total")))
        except InvalidOperation as exc:
            raise ValueError("unknown provider position quantity") from exc
        if not quantity.is_finite() or quantity < 0:
            raise ValueError("unknown provider position quantity")
        if quantity > 0:
            open_positions.append(row)
    return {"open_positions": len(open_positions), "pending_orders": len(orders)}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--confirm" not in argv:
        print("REFUSED: explicit --confirm required")
        return 2
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-oid", required=True)
    parser.add_argument("--resolved-state", choices=("reconciled", "rejected"), required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    if not args.approval_reference.strip():
        raise ValueError("approval reference is required")
    if args.resolved_state == "rejected":
        print(
            "REFUSED: a flat snapshot cannot prove historical rejection; "
            "use provider-order reconciliation"
        )
        return 2
    snapshot = asyncio.run(_provider_snapshot())
    if snapshot != {"open_positions": 0, "pending_orders": 0}:
        raise RuntimeError("provider is not flat; refusing historical reconciliation")
    with psycopg.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT state FROM live_order_intents
               WHERE exchange = 'bitget' AND client_order_id = %s FOR UPDATE""",
            (args.client_oid,),
        )
        row = cursor.fetchone()
        if row is None:
            raise LookupError("historical Bitget intent not found")
        prior_state = str(row[0])
        if prior_state in {"filled", "reconciled", "rejected", "cancelled"}:
            raise ValueError("intent is already terminal")
        cursor.execute(
            """UPDATE live_order_intents SET state = %s, updated_at = CURRENT_TIMESTAMP
               WHERE exchange = 'bitget' AND client_order_id = %s""",
            (args.resolved_state, args.client_oid),
        )
        cursor.execute(
            """INSERT INTO intent_reconciliation_audit
                (id, exchange, client_order_id, prior_state, resolved_state, provider_snapshot,
                 approval_reference)
               VALUES (%s, 'bitget', %s, %s, %s, %s::jsonb, %s)
               ON CONFLICT (exchange, client_order_id, resolved_state) DO NOTHING""",
            (
                uuid4(),
                args.client_oid,
                prior_state,
                args.resolved_state,
                json.dumps(snapshot),
                args.approval_reference.strip(),
            ),
        )
    print(f"reconciled={args.client_oid} state={args.resolved_state} snapshot=flat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
