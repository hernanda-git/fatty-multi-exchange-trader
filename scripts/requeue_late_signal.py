#!/usr/bin/env python3
# ruff: noqa: E501
"""Re-admit a late signal the lane missed, with an explicit operator reference.

A signal whose five-minute window lapsed while the dispatcher was blocked (a wedge,
an outage, a rollout) is otherwise lost: the bot refuses stale entries by design and
the dispatch expires. This tool re-opens exactly one such signal so the bot's own
fenced path can take it — same cap, canary, reservation, protection and price checks.

It never bypasses a fence: it refuses anything but an expired, untouched dispatch whose
stop is still intact and whose target is not already reached, records the operator
reference on the source message, and transitions the dispatch with an audited reason.

Refuses when a superseded revision, an open position, or a breached stop makes the
late entry unsafe.

    docker compose exec -T dispatcher-bitget /app/.venv/bin/python \
        scripts/requeue_late_signal.py --message 16326 \
        --approval-reference owner-approved-late-near-20261018 [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import psycopg  # noqa: E402

from fatty_trader.exchanges.bitget.client import BitgetRestClient  # noqa: E402
from fatty_trader.execution.bitget_dispatch_repository import (  # noqa: E402
    PostgresBitgetDispatchRepository,
)

READMITTABLE_STATES = {"EXPIRED", "QUEUED"}
OVERRIDABLE_REJECTIONS = {None, "stale-source-message"}


def evaluate_readmission(
    *,
    state: str,
    attempts: int,
    direction: str,
    rejection: str | None,
    last: Decimal | None,
    stop_loss: Decimal,
    take_profit: Decimal | None,
) -> tuple[bool, str]:
    """Pure guards for a late entry; the market must still be on the right side."""
    if state not in READMITTABLE_STATES:
        return False, f"dispatch state {state} is not re-admittable"
    if attempts:
        return False, "dispatch already executed an attempt"
    if rejection not in OVERRIDABLE_REJECTIONS:
        return False, f"source revision is vetoed ({rejection})"
    if last is None:
        return False, "provider price is unavailable"
    entry_side = direction.upper()
    if entry_side == "SHORT":
        if last >= stop_loss:
            return False, f"short stop already breached (last {last} >= sl {stop_loss})"
        if take_profit is not None and last <= take_profit:
            return False, f"short target already reached (last {last} <= tp {take_profit})"
    elif entry_side == "LONG":
        if last <= stop_loss:
            return False, f"long stop already breached (last {last} <= sl {stop_loss})"
        if take_profit is not None and last >= take_profit:
            return False, f"long target already reached (last {last} >= tp {take_profit})"
    else:
        return False, f"direction {direction} is not tradable"
    return True, f"last {last} is on the correct side of stop {stop_loss}"


def load_context(message_ref: int) -> dict[str, object] | None:
    with psycopg.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tm.id::text, tm.entry_rejection_reason, s.id::text, s.pair_token,
                   s.direction, s.stop_loss::text, s.take_profits::text,
                   d.id::text, d.state, d.attempts, d.exchange
            FROM telegram_messages tm
            JOIN canonical_signals s ON s.message_id = tm.id
            JOIN dispatches d ON d.source_type = 'canonical_signal'
                AND d.source_id = s.id AND d.exchange = 'bitget'
            WHERE tm.message_id = %s
            ORDER BY d.created_at DESC
            LIMIT 1
            """,
            (message_ref,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    values = list(row.values()) if isinstance(row, dict) else list(row)
    targets = values[6]
    if isinstance(targets, str):
        targets = json.loads(targets)
    return {
        "message_uuid": str(values[0]),
        "rejection": values[1],
        "signal_id": str(values[2]),
        "pair": str(values[3]),
        "direction": str(values[4]),
        "stop_loss": Decimal(str(values[5])),
        "take_profits": [Decimal(str(value)) for value in (targets or [])],
        "dispatch_id": str(values[7]),
        "state": str(values[8]),
        "attempts": int(values[9]),
        "exchange": str(values[10]),
    }


def apply_readmission(context: dict[str, object], reference: str, note: str) -> None:
    repository = PostgresBitgetDispatchRepository(psycopg.connect)
    with psycopg.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE telegram_messages SET re_admission_reference = %s, "
            "re_admitted_at = clock_timestamp(), entry_rejection_reason = NULL "
            "WHERE id = %s",
            (reference, context["message_uuid"]),
        )
        connection.commit()
    repository.transition(
        uuid.UUID(str(context["dispatch_id"])),
        expected_state=str(context["state"]),
        target_state="QUEUED",
        reason=f"operator-re-admission:{reference}",
        evidence={
            "stage": "operator_re_admission",
            "approval_reference": reference,
            "operator": "cli",
            "note": note,
            "source_message_ref": int(context.get("message_ref", 0)) or None,
        },
    )


async def run(message_ref: int, reference: str, dry_run: bool) -> int:
    if not reference.strip():
        print(json.dumps({"re_admitted": False, "refused": "approval reference is required"}))
        return 2
    context = load_context(message_ref)
    if context is None:
        print(
            json.dumps({"re_admitted": False, "refused": f"message #{message_ref} has no signal"})
        )
        return 2
    context["message_ref"] = message_ref
    mode = os.environ.get("BITGET_MODE", "LIVE")
    client = BitgetRestClient(
        os.environ["BITGET_API_KEY"],
        os.environ["BITGET_API_SECRET"],
        os.environ["BITGET_API_PASSPHRASE"],
        mode=mode,
        max_get_retries=0,
    )
    try:
        symbol = f"{context['pair']}USDT"
        positions = await client.get_single_position(symbol)
        rows = positions.get("data") if isinstance(positions, dict) else positions
        if not isinstance(rows, list) or rows:
            print(json.dumps({"re_admitted": False, "refused": f"{symbol} already has a position"}))
            return 2
        ticker = await client.get_ticker(symbol)
        row = ticker[0] if isinstance(ticker, list) else ticker
        try:
            last = Decimal(str(row.get("lastPr")))
        except (InvalidOperation, TypeError, ValueError):
            last = None
        targets = context["take_profits"]
        allowed, reason = evaluate_readmission(
            state=str(context["state"]),
            attempts=int(context["attempts"]),
            direction=str(context["direction"]),
            rejection=context["rejection"],
            last=last,
            stop_loss=context["stop_loss"],
            take_profit=targets[0] if targets else None,
        )
        if not allowed:
            print(json.dumps({"re_admitted": False, "refused": reason}))
            return 2
        if not dry_run:
            apply_readmission(context, reference, reason)
        print(
            json.dumps(
                {
                    "re_admitted": not dry_run,
                    "dry_run": dry_run,
                    "message": message_ref,
                    "dispatch_id": context["dispatch_id"],
                    "approval_reference": reference,
                    "evidence": reason,
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        await client.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", type=int, required=True, help="source message id, e.g. 16326")
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args.message, args.approval_reference, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
