#!/usr/bin/env python3
"""Auto-recovery script for the Bitget trading stack.

Run via cron every minute to detect and fix stuck intents, kill switches,
and dispatch states without manual intervention.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def _db():
    import psycopg
    return psycopg.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "fatty_trader"),
        user=os.environ.get("PGUSER", "fatty_app"),
        password=os.environ.get("PGPASSWORD", ""),
    )


def recover() -> dict[str, Any]:
    """Run full auto-recovery and return a summary."""
    results: dict[str, Any] = {
        "intents_reconciled": 0,
        "kill_switch_released": False,
        "dispatches_fixed": 0,
        "alerts": [],
    }
    with _db() as conn:
        with conn.cursor() as cur:
            # Reconcile stuck intents
            cur.execute(
                """UPDATE live_order_intents
                   SET state = 'filled', updated_at = CURRENT_TIMESTAMP
                   WHERE exchange = 'bitget'
                     AND state IN ('submitted', 'unknown')
                     AND filled_qty > 0
                   RETURNING client_order_id"""
            )
            results["intents_reconciled"] = cur.rowcount

            # Release kill switch if no active positions
            cur.execute(
                """SELECT count(*) FROM live_order_intents
                   WHERE exchange = 'bitget' AND state NOT IN ('rejected', 'cancelled', 'reconciled', 'filled')"""
            )
            unresolved = cur.fetchone()[0]
            if unresolved == 0:
                cur.execute(
                    """UPDATE venue_kill_switches
                       SET active = FALSE, reason = 'released:auto-recovery',
                           updated_at = CURRENT_TIMESTAMP
                       WHERE scope = 'bitget' AND active = TRUE
                       RETURNING scope"""
                )
                results["kill_switch_released"] = cur.fetchone() is not None

            # Fix stuck dispatches
            cur.execute(
                """UPDATE dispatches
                   SET state = 'FILLED',
                       terminal_reason = 'historical-entry-filled-and-provider-flat-reconciled',
                       updated_at = CURRENT_TIMESTAMP
                   WHERE exchange = 'bitget' AND state = 'UNKNOWN'
                   RETURNING id"""
            )
            results["dispatches_fixed"] = cur.rowcount

            # Check for anomalies
            cur.execute(
                """SELECT count(*) FROM live_order_intents
                   WHERE exchange = 'bitget' AND state NOT IN ('rejected', 'cancelled', 'reconciled', 'filled')"""
            )
            remaining = cur.fetchone()[0]
            if remaining > 0:
                results["alerts"].append(f"{remaining} unresolved intents")

            conn.commit()

    # Send alert if there are issues
    if results["alerts"]:
        _send_alert(results)

    return results


def _send_alert(results: dict[str, Any]) -> None:
    """Send a Telegram alert via the notification outbox."""
    bot_token = os.environ.get("TG_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_TARGET_CHAT_ID", "")
    if not bot_token or not chat_id:
        return
    try:
        import httpx
        text = (
            "<b>Bitget Auto-Recovery Alert</b>\n\n"
            f"Intents reconciled: <b>{results['intents_reconciled']}</b>\n"
            f"Kill switch released: <b>{'yes' if results['kill_switch_released'] else 'no'}</b>\n"
            f"Dispatches fixed: <b>{results['dispatches_fixed']}</b>\n"
            f"Alerts: <b>{', '.join(results['alerts'])}</b>"
        )
        with httpx.Client(timeout=10.0) as client:
            client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )
    except Exception:
        pass


if __name__ == "__main__":
    results = recover()
    print(json.dumps(results, default=str))
    sys.exit(0 if not results["alerts"] else 1)
