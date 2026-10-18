#!/usr/bin/env python3
"""One-screen end-to-end trail for a trade, for the next natural signal and for history.

Read-only (every connection is opened with ``default_transaction_read_only=on``).
Never calls the venue.

Usage:
    dispatch_trail.py --latest
    dispatch_trail.py --dispatch <uuid>
    dispatch_trail.py --client-oid <entry client order id>
    dispatch_trail.py --latest --json

Exit codes: 0 printed, 2 not found.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from typing import Any

import psycopg

READ_ONLY = "-c default_transaction_read_only=on"

DISPATCH_SQL = """
SELECT d.id::text, d.state, d.attempts, d.terminal_reason, d.created_at, d.updated_at,
       d.source_type, d.source_id::text, d.exchange, d.revision
FROM dispatches d
WHERE (%s::uuid IS NULL OR d.id = %s::uuid)
ORDER BY d.created_at DESC
LIMIT 1
"""

DISPATCH_BY_OID_SQL = """
SELECT d.id::text, d.state, d.attempts, d.terminal_reason, d.created_at, d.updated_at,
       d.source_type, d.source_id::text, d.exchange, d.revision
FROM dispatches d
WHERE d.id = (SELECT r.dispatch_id FROM bitget_margin_reservations r
               WHERE r.client_order_id = %s
               UNION ALL
              SELECT i.dispatch_id FROM live_order_intents i
               WHERE i.client_order_id = %s AND i.dispatch_id IS NOT NULL
               LIMIT 1)
LIMIT 1
"""

TRANSITIONS_SQL = """
SELECT created_at, from_state, to_state, reason,
       evidence::jsonb ->> 'stage' AS stage,
       evidence::jsonb ->> 'reason_code' AS reason_code,
       evidence::jsonb ->> 'observed_at' AS observed_at,
       evidence::jsonb ->> 'attempt' AS attempt,
       evidence::jsonb ->> 'elapsed_ms' AS elapsed_ms,
       evidence::jsonb ->> 'provider_effect' AS provider_effect,
       evidence::jsonb ->> 'source_received_at' AS source_received_at,
       evidence::jsonb ->> 'canonical_signal_id' AS canonical_signal_id,
       evidence::jsonb ->> 'source_message_id' AS source_message_id
FROM dispatch_transitions WHERE dispatch_id = %s::uuid ORDER BY created_at
"""

SIGNAL_SQL = """
SELECT s.id::text, s.pair_token, s.direction, s.entry_price, s.stop_loss,
       s.created_at, s.message_id::text,
       m.received_at, left(coalesce(m.raw_text, ''), 160)
FROM canonical_signals s
LEFT JOIN telegram_messages m ON m.id = s.message_id
WHERE s.id::text = %s
LIMIT 1
"""

RESERVATIONS_SQL = """
SELECT client_order_id, state, symbol, environment, created_at, resolved_at,
       resolution_reason, has_exposure
FROM bitget_margin_reservations WHERE dispatch_id = %s::uuid ORDER BY created_at
"""

INTENT_SQL = """
SELECT client_order_id, role, state, symbol, side, filled_qty, filled_price,
       provider_order_id, created_at, updated_at,
       (SELECT min(f.filled_at) FROM fills f WHERE f.client_order_id = i.client_order_id)
           AS first_fill_at,
       (SELECT count(*) FROM fills f WHERE f.client_order_id = i.client_order_id) AS fill_rows
FROM live_order_intents i
WHERE client_order_id = %s
   OR protection_root_oid = %s
   OR dispatch_id = %s::uuid
ORDER BY created_at
"""

FILLS_SQL = """
SELECT client_order_id, provider_fill_id, price, quantity, fee, realized_pnl, filled_at
FROM fills WHERE client_order_id LIKE %s || '%%' ORDER BY filled_at
"""

CAPABILITY_SQL = """
SELECT native_state, last_verified_at, stream_state, last_stream_at, last_error
FROM bitget_protection_capabilities WHERE symbol = %s ORDER BY updated_at DESC LIMIT 1
"""

POST_FILL_SQL = """
SELECT status, reason, observed_quantity, observed_entry_price, observed_at
FROM bitget_post_fill_reconciliations WHERE client_order_id = %s
ORDER BY created_at DESC LIMIT 1
"""


def _delta(start: Any, end: Any) -> str:
    if start is None or end is None:
        return "-"
    return f"{(end - start).total_seconds():.3f}s"


def _ts(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return str(value)


def find_dispatch(
    cursor: psycopg.Cursor, dispatch_id: str | None, client_oid: str | None, latest: bool
) -> tuple[Any, ...] | None:
    if client_oid:
        cursor.execute(DISPATCH_BY_OID_SQL, (client_oid, client_oid))
    else:
        cursor.execute(DISPATCH_SQL, (dispatch_id, dispatch_id))
    row = cursor.fetchone()
    if row is None and latest:
        cursor.execute(DISPATCH_SQL, (None, None))
        row = cursor.fetchone()
    return row


def collect(
    connection: psycopg.Connection,
    dispatch_id: str | None,
    client_oid: str | None,
) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        dispatch = find_dispatch(cursor, dispatch_id, client_oid, latest=not dispatch_id and not client_oid)
        if dispatch is None:
            return None
        did = dispatch[0]
        trail: dict[str, Any] = {
            "dispatch": {
                "id": did,
                "state": dispatch[1],
                "attempts": dispatch[2],
                "terminal_reason": dispatch[3],
                "created_at": dispatch[4],
                "updated_at": dispatch[5],
                "source_type": dispatch[6],
                "source_id": dispatch[7],
                "exchange": dispatch[8],
            },
            "transitions": [],
            "reservations": [],
            "intents": [],
            "fills": [],
            "signal": None,
            "capability": None,
            "post_fill": None,
        }
        cursor.execute(TRANSITIONS_SQL, (did,))
        trail["transitions"] = [
            {
                "at": row[0],
                "from_state": row[1],
                "to_state": row[2],
                "reason": row[3],
                "stage": row[4],
                "reason_code": row[5],
                "observed_at": row[6],
                "attempt": row[7],
                "elapsed_ms": row[8],
                "provider_effect": row[9],
                "source_received_at": row[10],
                "canonical_signal_id": row[11],
                "source_message_id": row[12],
            }
            for row in cursor.fetchall()
        ]
        signal_id = next(
            (item["canonical_signal_id"] for item in trail["transitions"] if item["canonical_signal_id"]),
            dispatch[7],
        )
        if signal_id:
            cursor.execute(SIGNAL_SQL, (signal_id,))
            row = cursor.fetchone()
            if row:
                trail["signal"] = {
                    "id": row[0],
                    "pair_token": row[1],
                    "direction": row[2],
                    "entry_price": float(row[3]) if row[3] is not None else None,
                    "stop_loss": float(row[4]) if row[4] is not None else None,
                    "created_at": row[5],
                    "message_id": row[6],
                    "message_received_at": row[7],
                    "message_excerpt": row[8],
                }
        cursor.execute(RESERVATIONS_SQL, (did,))
        trail["reservations"] = [
            {
                "client_order_id": row[0],
                "state": row[1],
                "symbol": row[2],
                "environment": row[3],
                "created_at": row[4],
                "resolved_at": row[5],
                "resolution_reason": row[6],
                "has_exposure": row[7],
            }
            for row in cursor.fetchall()
        ]
        root = next((item["client_order_id"] for item in trail["reservations"] if item["client_order_id"]), None)
        entry_oid = root or client_oid or ""
        if entry_oid:
            cursor.execute(INTENT_SQL, (entry_oid, entry_oid, did))
            trail["intents"] = [
                {
                    "client_order_id": row[0],
                    "role": row[1],
                    "state": row[2],
                    "symbol": row[3],
                    "side": row[4],
                    "filled_qty": float(row[5]) if row[5] is not None else None,
                    "filled_price": float(row[6]) if row[6] is not None else None,
                    "provider_order_id": row[7],
                    "created_at": row[8],
                    "updated_at": row[9],
                    "first_fill_at": row[10],
                    "fill_rows": row[11],
                }
                for row in cursor.fetchall()
            ]
            cursor.execute(FILLS_SQL, (entry_oid,))
            trail["fills"] = [
                {
                    "client_order_id": row[0],
                    "provider_fill_id": row[1],
                    "price": float(row[2]) if row[2] is not None else None,
                    "quantity": float(row[3]) if row[3] is not None else None,
                    "fee": float(row[4]) if row[4] is not None else None,
                    "realized_pnl": float(row[5]) if row[5] is not None else None,
                    "filled_at": row[6],
                }
                for row in cursor.fetchall()
            ]
            symbol = next((item["symbol"] for item in trail["intents"] if item["symbol"]), None)
            if symbol:
                cursor.execute(CAPABILITY_SQL, (symbol,))
                row = cursor.fetchone()
                if row:
                    trail["capability"] = {
                        "native_state": row[0],
                        "last_verified_at": row[1],
                        "stream_state": row[2],
                        "last_stream_at": row[3],
                        "last_error": row[4],
                    }
            cursor.execute(POST_FILL_SQL, (entry_oid,))
            row = cursor.fetchone()
            if row:
                trail["post_fill"] = {
                    "status": row[0],
                    "reason": row[1],
                    "observed_quantity": float(row[2]) if row[2] is not None else None,
                    "observed_entry_price": float(row[3]) if row[3] is not None else None,
                    "observed_at": row[4],
                }
        return trail


def render(trail: dict[str, Any]) -> str:
    dispatch = trail["dispatch"]
    signal = trail["signal"] or {}
    lines = [
        f"# Trail {dispatch['id']}",
        "",
        f"dispatch: `{dispatch['state']}` attempts={dispatch['attempts']} "
        f"verdict=`{dispatch['terminal_reason'] or '-'}` ({dispatch['exchange']})",
        f"created {_ts(dispatch['created_at'])}  updated {_ts(dispatch['updated_at'])}",
    ]
    if signal:
        lines += [
            "",
            f"signal `{signal['pair_token']}` {signal['direction']} "
            f"entry={signal['entry_price']} stop={signal['stop_loss']}",
            f"message received {_ts(signal['message_received_at'])}: "
            f"{signal['message_excerpt'] or ''}",
        ]
    first_fill = next((item["filled_at"] for item in trail["fills"] if item["filled_at"]), None)
    first_claim = next(
        (item["created_at"] for item in trail["intents"] if item["role"] in {"SL", "TP"}), None
    )
    close_fill = next(
        (
            item["filled_at"]
            for item in trail["fills"]
            if item["client_order_id"].endswith(("-emergency", "-close")) and item["filled_at"]
        ),
        None,
    )
    lines += [
        "",
        f"timing: signal→dispatch {_delta(signal.get('message_received_at'), dispatch['created_at'])}"
        f" · fill→protection claim {_delta(first_fill, first_claim)}"
        f" · fill→close fill {_delta(first_fill, close_fill)}",
        "",
        "## Transitions",
        "",
        "| at | transition | stage | reason_code | attempt | provider effect |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in trail["transitions"]:
        lines.append(
            f"| {_ts(item['at'])} | {item['from_state']} → {item['to_state']} | "
            f"{item['stage'] or '-'} | {item['reason_code'] or item['reason']} | "
            f"{item['attempt'] or '-'} | {item['provider_effect'] or '-'} |"
        )
    lines += ["", "## Intents", "", "| oid | role | state | filled | provider id | created |", "| --- | --- | --- | --- | --- | --- |"]
    for item in trail["intents"]:
        lines.append(
            f"| `{item['client_order_id']}` | {item['role']} | {item['state']} | "
            f"{item['filled_qty']}@{item['filled_price']} | {item['provider_order_id'] or '-'} | "
            f"{_ts(item['created_at'])} |"
        )
    lines += ["", "## Fills", "", "| oid | price | qty | fee | pnl | at |", "| --- | --- | --- | --- | --- | --- |"]
    for item in trail["fills"]:
        lines.append(
            f"| `{item['client_order_id']}` | {item['price']} | {item['quantity']} | "
            f"{item['fee']} | {item['realized_pnl']} | {_ts(item['filled_at'])} |"
        )
    if trail["capability"]:
        capability = trail["capability"]
        lines += [
            "",
            f"capability: native=`{capability['native_state']}` verified={_ts(capability['last_verified_at'])} "
            f"stream=`{capability['stream_state']}` last_stream={_ts(capability['last_stream_at'])} "
            f"last_error={capability['last_error'] or '-'}",
        ]
    if trail["post_fill"]:
        post = trail["post_fill"]
        lines += [
            f"post-fill: `{post['status']}` {post['reason'] or '-'} qty={post['observed_quantity']} "
            f"price={post['observed_entry_price']} observed={_ts(post['observed_at'])}",
        ]
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--latest", action="store_true")
    target.add_argument("--dispatch", default=None)
    target.add_argument("--client-oid", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dsn", default=None)
    args = parser.parse_args()
    if not (args.latest or args.dispatch or args.client_oid):
        parser.error("one of --latest, --dispatch, --client-oid is required")

    connection = psycopg.connect(args.dsn or "", options=READ_ONLY)
    try:
        trail = collect(connection, args.dispatch, args.client_oid)
    finally:
        connection.close()
    if trail is None:
        print("not found")
        return 2
    print(json.dumps(trail, default=str) if args.json else render(trail))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
