#!/usr/bin/env python3
"""P0-1 measurement: where protection time actually goes, per trade.

Read-only by construction (every connection is opened with
``default_transaction_read_only=on``). It never calls the venue, never writes, and is
safe to run against production at any time.

It answers, from durable rows alone:

* signal receipt -> dispatch creation (how long the lane takes to engage);
* entry fill -> first recovery readback (when the venue was first consulted);
* protection verdict: verified, missing (with reason), or settled closed;
* entry fill -> close fill (what a stop policy has to beat);
* transition churn: repeated identical transitions for the same dispatch (audit noise
  that buries real events and grows the table; today four dispatches carry 5 285 rows
  each).

It also states which questions the durable record cannot answer yet, because the number
that matters most — how long the venue takes to make protection *visible* after the POST
— is currently only in log lines and the capability error trail, not in a table.
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import UTC, datetime
from typing import Any

import psycopg

READ_ONLY = "-c default_transaction_read_only=on"

TRADES_SQL = """
SELECT d.id::text AS dispatch_id,
       d.state AS dispatch_state,
       d.attempts,
       d.terminal_reason,
       d.created_at AS dispatched_at,
       d.updated_at AS dispatch_updated_at,
       i.client_order_id AS entry_oid,
       i.symbol,
       i.side,
       i.state AS entry_state,
       i.filled_qty AS entry_filled_qty,
       (SELECT min(f.filled_at) FROM fills f WHERE f.client_order_id = i.client_order_id)
           AS entry_filled_at,
       (SELECT count(*) FROM dispatch_transitions t WHERE t.dispatch_id = d.id)
           AS transition_rows
FROM dispatches d
LEFT JOIN bitget_margin_reservations r ON r.dispatch_id = d.id
LEFT JOIN live_order_intents i
       ON i.role = 'ENTRY'
      AND (i.client_order_id = r.client_order_id OR i.dispatch_id = d.id)
ORDER BY d.created_at DESC
LIMIT %s
"""

LEGS_SQL = """
SELECT client_order_id, role, state, filled_qty, created_at, updated_at,
       (SELECT min(f.filled_at) FROM fills f
         WHERE f.client_order_id = live_order_intents.client_order_id) AS filled_at
FROM live_order_intents
WHERE protection_root_oid = %s OR client_order_id LIKE %s || '-%%'
ORDER BY created_at
"""

TIMELINE_SQL = """
SELECT created_at, from_state, to_state, reason,
       evidence::jsonb ->> 'stage' AS stage,
       evidence::jsonb ->> 'reason_code' AS reason_code,
       evidence::jsonb ->> 'observed_at' AS observed_at,
       evidence::jsonb ->> 'attempt' AS attempt,
       evidence::jsonb ->> 'source_received_at' AS source_received_at
FROM dispatch_transitions
WHERE dispatch_id = %s
ORDER BY created_at
"""

CAPABILITY_SQL = """
SELECT native_state, last_verified_at, stream_state, last_stream_at, updated_at
FROM bitget_protection_capabilities WHERE symbol = %s ORDER BY updated_at DESC LIMIT 1
"""


def _seconds(start: Any, end: Any) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 3)


def _collapse(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse consecutive identical (to_state, reason) rows into one entry with count.

    The recovery lane re-transitions unresolved lifecycles on every tick, so raw
    timelines are dominated by thousands of identical rows; collapsing keeps the real
    sequence readable and exposes the churn count instead of hiding it.
    """
    collapsed: list[dict[str, Any]] = []
    for row in timeline:
        key = (row["to_state"], row["reason"])
        if collapsed and (collapsed[-1]["_to_state"], collapsed[-1]["_reason"]) == key:
            collapsed[-1]["repeats"] += 1
            collapsed[-1]["last_at"] = row["created_at"]
            continue
        entry = dict(row)
        entry["_to_state"] = row["to_state"]
        entry["_reason"] = row["reason"]
        entry["repeats"] = 1
        entry["first_at"] = row["created_at"]
        entry["last_at"] = row["created_at"]
        collapsed.append(entry)
    return collapsed


def _ratio(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile)))
    return ordered[index]


def collect(connection: psycopg.Connection, limit: int) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    with connection.cursor() as cursor:
        cursor.execute(TRADES_SQL, (limit,))
        rows = cursor.fetchall()
        for row in rows:
            trade: dict[str, Any] = {
                "dispatch_id": row[0],
                "dispatch_state": row[1],
                "attempts": row[2],
                "terminal_reason": row[3],
                "dispatched_at": row[4],
                "entry_oid": row[6],
                "symbol": row[7],
                "side": row[8],
                "entry_state": row[9],
                "entry_filled_qty": float(row[10]) if row[10] is not None else None,
                "entry_filled_at": row[11],
                "transition_rows": row[12],
            }
            cursor.execute(LEGS_SQL, (row[6], row[6])) if row[6] else None
            trade["legs"] = [
                {
                    "client_order_id": leg[0],
                    "role": leg[1],
                    "state": leg[2],
                    "filled_qty": float(leg[3]) if leg[3] is not None else None,
                    "created_at": leg[4],
                    "updated_at": leg[5],
                    "filled_at": leg[6],
                }
                for leg in (cursor.fetchall() if row[6] else [])
            ]
            cursor.execute(TIMELINE_SQL, (row[0],))
            trade["timeline"] = _collapse(
                [
                    {
                        "created_at": item[0],
                        "from_state": item[1],
                        "to_state": item[2],
                        "reason": item[3],
                        "stage": item[4],
                        "reason_code": item[5],
                        "observed_at": item[6],
                        "attempt": item[7],
                        "source_received_at": item[8],
                    }
                    for item in cursor.fetchall()
                ]
            )
            cursor.execute(CAPABILITY_SQL, (row[7],))
            capability = cursor.fetchone()
            trade["capability"] = (
                {
                    "native_state": capability[0],
                    "last_verified_at": capability[1],
                    "stream_state": capability[2],
                    "last_stream_at": capability[3],
                }
                if capability
                else None
            )
            trades.append(trade)
    return {"generated_at": datetime.now(UTC).isoformat(), "trades": trades}


def summarise(report: dict[str, Any]) -> dict[str, Any]:
    verdicts = {
        "verified": 0,
        "missing-protection": 0,
        "settled-closed": 0,
        "no-entry": 0,
        "other": 0,
    }
    fills_to_claim: list[float] = []
    fills_to_readback: list[float] = []
    fills_to_close: list[float] = []
    signal_to_dispatch: list[float] = []
    churn: list[dict[str, Any]] = []
    for trade in report["trades"]:
        reason = trade["terminal_reason"] or ""
        if not trade["entry_oid"]:
            verdicts["no-entry"] += 1
        elif "recovery-closed" in reason:
            verdicts["settled-closed"] += 1
        elif "missing-protection" in reason:
            verdicts["missing-protection"] += 1
        elif "recovery-protection-verified" in reason or trade["dispatch_state"] in {
            "FILLED",
            "RECONCILED",
        }:
            verdicts["verified"] += 1
        else:
            verdicts["other"] += 1
        if trade["entry_filled_at"]:
            claim = next(
                (
                    leg["created_at"]
                    for leg in trade["legs"]
                    if leg["role"] in {"SL", "TP"} and leg["created_at"]
                ),
                None,
            )
            delta = _seconds(trade["entry_filled_at"], claim)
            if delta is not None:
                fills_to_claim.append(delta)
            for event in trade["timeline"]:
                if event["stage"] == "recovery_readback":
                    delta = _seconds(trade["entry_filled_at"], event["created_at"])
                    if delta is not None:
                        fills_to_readback.append(delta)
                    break
            for leg in trade["legs"]:
                if leg["role"] in {"CLOSE", "EMERGENCY_CLOSE"} and leg["filled_at"]:
                    delta = _seconds(trade["entry_filled_at"], leg["filled_at"])
                    if delta is not None:
                        fills_to_close.append(delta)
                    break
        for event in trade["timeline"]:
            if event["source_received_at"] and trade["dispatched_at"]:
                received = datetime.fromisoformat(event["source_received_at"])
                delta = _seconds(received, trade["dispatched_at"])
                if delta is not None:
                    signal_to_dispatch.append(delta)
                break
        if trade["transition_rows"] > 50:
            churn.append(
                {
                    "dispatch_id": trade["dispatch_id"],
                    "symbol": trade["symbol"],
                    "transition_rows": trade["transition_rows"],
                    "distinct_sequences": len(trade["timeline"]),
                }
            )
    return {
        "trades": len(report["trades"]),
        "join_coverage": {
            "with_entry_intent": sum(1 for trade in report["trades"] if trade["entry_oid"]),
            "without_entry_intent": sum(
                1 for trade in report["trades"] if not trade["entry_oid"]
            ),
        },
        "verdicts": verdicts,
        "entry_fill_to_protection_claim_seconds": {
            "median": round(statistics.median(fills_to_claim), 3) if fills_to_claim else None,
            "p95": _ratio(fills_to_claim, 0.95),
            "samples": len(fills_to_claim),
        },
        "entry_fill_to_first_recovery_readback_seconds": {
            "median": round(statistics.median(fills_to_readback), 3) if fills_to_readback else None,
            "p95": _ratio(fills_to_readback, 0.95),
            "samples": len(fills_to_readback),
        },
        "entry_fill_to_close_fill_seconds": {
            "median": round(statistics.median(fills_to_close), 3) if fills_to_close else None,
            "p95": _ratio(fills_to_close, 0.95),
            "samples": len(fills_to_close),
        },
        "signal_receipt_to_dispatch_seconds": {
            "median": round(statistics.median(signal_to_dispatch), 3)
            if signal_to_dispatch
            else None,
            "p95": _ratio(signal_to_dispatch, 0.95),
            "samples": len(signal_to_dispatch),
        },
        "transition_churn": churn,
    }


GAPS = [
    "protection POST -> first provably-visible readback: no `native_plan_posted_at` / "
    "`native_plan_visible_at` exists anywhere. The grace loop records attempts only in the "
    "capability error trail (`|grace:N`) and a log line (`native-protection-verified-late`); "
    "a durable per-attempt row (symbol, root, attempt, observed_at, outcome) is the missing "
    "instrumentation.",
    "`bitget_protection_capabilities.last_verified_at` is local `datetime.now(UTC)` at "
    "capability update, not a venue timestamp, and it is per (exchange, environment, symbol) "
    "— not per trade. Failed or late confirmations leave it NULL.",
    "stream freshness: `last_stream_at` is the *provider* event timestamp, not socket receipt, "
    "and it is overwritten rather than sampled, so staleness distribution cannot be "
    "reconstructed after the fact.",
    "close POST / flat-proof timestamps are absent: close latency is measured from "
    "`fills.filled_at`, and closes that are not root-linked (`fb-…-close`, provider-only "
    "exits) cannot be attributed to a trade by client-order-id prefix.",
    "protection readback attempts are not durable: `dispatches.attempts` counts dispatch "
    "attempts, not protection confirmations.",
]


def render_markdown(report: dict[str, Any], summary: dict[str, Any]) -> str:
    lines = ["# Protection latency report", "", f"generated: {report['generated_at']}", ""]
    lines += [
        f"trades analysed: **{summary['trades']}** — "
        + ", ".join(f"{key}: {value}" for key, value in summary["verdicts"].items()),
        f"entry-intent join coverage: {summary['join_coverage']['with_entry_intent']} linked, "
        f"{summary['join_coverage']['without_entry_intent']} unlinked",
        "",
        "| metric | median (s) | p95 (s) | samples |",
        "| --- | --- | --- | --- |",
    ]
    for label, key in (
        ("signal receipt → dispatch", "signal_receipt_to_dispatch_seconds"),
        ("entry fill → protection claim (pre-POST)", "entry_fill_to_protection_claim_seconds"),
        ("entry fill → first recovery readback", "entry_fill_to_first_recovery_readback_seconds"),
        ("entry fill → close fill", "entry_fill_to_close_fill_seconds"),
    ):
        data = summary[key]
        lines.append(
            f"| {label} | {data['median']} | {data['p95']} | {data['samples']} |"
        )
    lines += ["", "## Per trade", ""]
    for trade in report["trades"]:
        reason = trade["terminal_reason"] or "-"
        lines += [
            f"### {trade['symbol']} · {trade['side']} · {trade['dispatch_id'][:8]}",
            "",
            f"- dispatch: `{trade['dispatch_state']}` attempts={trade['attempts']} "
            f"verdict=`{reason}`",
            f"- entry `{trade['entry_oid']}` state={trade['entry_state']} "
            f"filled={trade['entry_filled_qty']} at {trade['entry_filled_at']}",
            f"- capability: {trade['capability']}",
            "",
            "| when | transition | stage | reason_code | repeats |",
            "| --- | --- | --- | --- | --- |",
        ]
        for event in trade["timeline"][:12]:
            lines.append(
                f"| {event['first_at']} | {event['from_state']} → {event['to_state']} | "
                f"{event['stage'] or '-'} | {event['reason_code'] or event['reason']} | "
                f"{event['repeats']} |"
            )
        lines.append("")
    if summary["transition_churn"]:
        lines += ["## Transition churn (repeated no-op transitions)", ""]
        lines += ["| dispatch | symbol | rows | distinct sequences |", "| --- | --- | --- | --- |"]
        for item in summary["transition_churn"]:
            lines.append(
                f"| {item['dispatch_id'][:8]} | {item['symbol']} | "
                f"{item['transition_rows']} | {item['distinct_sequences']} |"
            )
        lines.append("")
    lines += ["## Still unmeasurable from durable rows", ""]
    lines += [f"- {gap}" for gap in GAPS]
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=25, help="how many dispatches to inspect")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--dsn", default=None, help="psycopg DSN (defaults to PG* env)")
    args = parser.parse_args()

    connection = psycopg.connect(args.dsn or "", options=READ_ONLY)
    try:
        report = collect(connection, args.limit)
    finally:
        connection.close()
    summary = summarise(report)
    if args.json:
        print(json.dumps({"summary": summary, "report": report}, default=str))
    else:
        print(render_markdown(report, summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
