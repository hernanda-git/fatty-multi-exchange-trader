#!/usr/bin/env python3
"""Auto-recovery script for the Bitget trading stack.

Run via cron every minute to detect and fix stuck intents, kill switches,
and dispatch states without manual intervention.
Only outputs when there is something to report (watchdog pattern).
"""

from __future__ import annotations

import json
import os
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
    """Return read-only recovery evidence; never mutate provider or safety state."""
    results: dict[str, Any] = {
        "intents_reconciled": 0,
        "kill_switch_released": False,
        "dispatches_fixed": 0,
        "read_only": True,
        "alerts": [],
    }
    with _db() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM live_order_intents
               WHERE exchange = 'bitget'
                 AND state NOT IN ('rejected', 'cancelled', 'reconciled', 'filled')"""
        )
        unresolved = int(cur.fetchone()[0])
        cur.execute(
            "SELECT count(*) FROM dispatches WHERE exchange = 'bitget' AND state = 'UNKNOWN'"
        )
        unknown_dispatches = int(cur.fetchone()[0])
        cur.execute("SELECT active FROM venue_kill_switches WHERE scope = 'bitget'")
        row = cur.fetchone()
        kill_switch_active = bool(row[0]) if row is not None else False
    results["unresolved_intents"] = unresolved
    results["unknown_dispatches"] = unknown_dispatches
    results["kill_switch_active"] = kill_switch_active
    if unresolved:
        results["alerts"].append(f"{unresolved} unresolved intents")
    if unknown_dispatches:
        results["alerts"].append(f"{unknown_dispatches} unknown dispatches")
    if kill_switch_active:
        results["alerts"].append("kill switch active; explicit operator review required")
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
    # Watchdog pattern: only output when there's something to report
    if (
        results["alerts"]
        or results["intents_reconciled"] > 0
        or results["kill_switch_released"]
        or results["dispatches_fixed"] > 0
    ):
        print(json.dumps(results, default=str))
    # Otherwise: empty stdout = no delivery
