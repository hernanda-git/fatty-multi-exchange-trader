# The production host runs Python 3.9; datetime.UTC only exists in 3.11+.
# ruff: noqa: UP017
#!/usr/bin/env python3
"""Overnight read-only watchdog for the live Bitget bot.

Runs every WATCHDOG_INTERVAL_SECONDS (default 60) on the host. It observes
containers, the sanitized health payload, the durable ledger, and the venue
(positions + native plans) and pages the owner through the bot's own
notification outbox when the state changes.

It never places, cancels or closes orders, never moves a stop, never releases a
kill switch and never changes configuration. The only write it performs is a
``system-event`` notification card.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path(
    os.environ.get(
        "FATTY_WATCHDOG_STATE_DIR",
        str(Path.home() / ".local" / "state" / "fatty-watchdog"),
    )
)
INTERVAL = max(15, int(os.environ.get("WATCHDOG_INTERVAL_SECONDS", "60")))
ALERTS_ENABLED = os.environ.get("FATTY_WATCHDOG_ALERTS", "1") == "1"
HEALTH_URL = os.environ.get("FATTY_WATCHDOG_HEALTH_URL", "http://127.0.0.1:18081/health/telemetry")
DOCKER = os.environ.get("FATTY_WATCHDOG_DOCKER", "/usr/bin/docker")
REQUIRED_SERVICES = (
    "analyzer",
    "console-worker",
    "dispatcher-bitget",
    "intake",
    "monitor-bitget",
    "notification-sender",
    "operator-bot",
    "postgres",
    "source-management",
    "web",
)
REMINDER_SECONDS = 6 * 3600
SQL = """
SELECT json_build_object(
  'active_latches', (
    SELECT COALESCE(json_agg(json_build_object('scope',scope,'reason',reason)), '[]'::json)
    FROM venue_kill_switches WHERE active),
  'recon_age', (
    SELECT extract(epoch FROM (now() - last_run_at))
    FROM reconciliation_state WHERE scope = 'bitget'),
  'unknown_intents', (
    SELECT count(*) FROM live_order_intents
    WHERE exchange='bitget' AND state='unknown'
      AND updated_at < now() - interval '5 minutes'),
  'errored_intents', (
    SELECT COALESCE(json_agg(json_build_object(
      'client_order_id',client_order_id,'error',last_error,'state',state)), '[]'::json)
    FROM live_order_intents
    WHERE exchange='bitget' AND last_error IS NOT NULL
      AND updated_at < now() - interval '5 minutes'),
  'stale_reservations', (
    SELECT count(*) FROM bitget_margin_reservations
    WHERE exchange='bitget' AND state='reserved'
      AND created_at < now() - interval '10 minutes'),
  'failed_management', (
    SELECT COALESCE(json_agg(row_to_json(f)), '[]'::json) FROM (
      SELECT action, symbol, last_error, updated_at
      FROM source_management_updates
      WHERE state='failed' AND updated_at > now() - interval '2 hours'
      ORDER BY updated_at DESC LIMIT 5) f),
  'same_state_transitions', (
    SELECT count(*) FROM dispatch_transitions
    WHERE from_state = to_state AND created_at > now() - interval '10 minutes'),
  'outbox_pending', (
    SELECT count(*) FROM notifications_outbox
    WHERE sent_at IS NULL AND failed_at IS NULL),
  'outbox_failed_recent', (
    SELECT count(*) FROM notifications_outbox
    WHERE failed_at IS NOT NULL AND failed_at > now() - interval '24 hours'),
  'known_limit_oids', (
    SELECT COALESCE(json_agg(client_order_id), '[]'::json)
    FROM live_order_intents
    WHERE exchange='bitget' AND order_type='limit')
)::text;
"""


def _run(command: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _compose(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return _run([DOCKER, "compose", *args], timeout=timeout)


def _psql(statement: str, *, timeout: int = 30) -> str:
    result = _compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "fatty_app",
        "-d",
        "fatty_trader",
        "-At",
        "-c",
        statement,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "psql failed")
    return result.stdout.strip()


def _json_output(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise ValueError("unparseable JSON output")


def _revision() -> str:
    result = _run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"])
    return result.stdout.strip() or "unknown"


def observe_containers(reasons: list[dict[str, str]]) -> dict[str, Any]:
    result = _compose("ps", "--format", "json")
    if result.returncode != 0:
        reasons.append(
            {"code": "compose-ps-failed", "severity": "critical", "detail": result.stderr[:200]}
        )
        return {}
    try:
        rows = _json_output(result.stdout) or []
    except ValueError:
        reasons.append({"code": "compose-ps-unparseable", "severity": "critical", "detail": ""})
        return {}
    if isinstance(rows, dict):
        rows = [rows]
    services = {str(row.get("Service")): row for row in rows if isinstance(row, dict)}
    for name in REQUIRED_SERVICES:
        row = services.get(name)
        if row is None:
            reasons.append(
                {"code": f"container-missing:{name}", "severity": "critical", "detail": ""}
            )
            continue
        state = str(row.get("State") or "").lower()
        health = str(row.get("Health") or "").lower()
        if state != "running":
            reasons.append(
                {
                    "code": f"container-{state or 'unknown'}:{name}",
                    "severity": "critical",
                    "detail": "",
                }
            )
        elif health != "healthy":
            reasons.append(
                {
                    "code": f"container-{health or 'no-health'}:{name}",
                    "severity": "critical",
                    "detail": "",
                }
            )
    return {
        name: {
            "state": row.get("State"),
            "health": row.get("Health"),
        }
        for name, row in services.items()
    }


def observe_health(reasons: list[dict[str, str]]) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=8) as response:
            payload = json.loads(response.read())
    except Exception as exc:  # noqa: BLE001
        reasons.append(
            {"code": "health-unreachable", "severity": "critical", "detail": type(exc).__name__}
        )
        return {}
    readiness = payload.get("readiness") or {}
    if readiness.get("status") != "ready":
        reasons.append(
            {
                "code": "health-not-ready",
                "severity": "critical",
                "detail": str(readiness.get("components", ""))[:200],
            }
        )
    if payload.get("status") != "ok":
        reasons.append(
            {
                "code": "health-status:" + str(payload.get("status")),
                "severity": "warning",
                "detail": "",
            }
        )
    return {
        "status": payload.get("status"),
        "readiness": readiness.get("status"),
        "live_execution_enabled": payload.get("live_execution_enabled"),
    }


def observe_ledger(reasons: list[dict[str, str]]) -> dict[str, Any]:
    try:
        data = json.loads(_psql(SQL) or "{}")
    except Exception as exc:  # noqa: BLE001
        reasons.append(
            {"code": "ledger-unreachable", "severity": "critical", "detail": type(exc).__name__}
        )
        return {}
    if data.get("active_latches"):
        for latch in data["active_latches"]:
            reasons.append(
                {
                    "code": "kill-switch-active:" + str(latch.get("scope")),
                    "severity": "critical",
                    "detail": str(latch.get("reason") or "")[:160],
                }
            )
    recon_age = data.get("recon_age")
    if recon_age is None:
        reasons.append({"code": "reconciliation-never-ran", "severity": "critical", "detail": ""})
    elif float(recon_age) > 180:
        reasons.append(
            {
                "code": "reconciliation-stale",
                "severity": "critical",
                "detail": f"{float(recon_age):.0f}s",
            }
        )
    if int(data.get("unknown_intents") or 0) > 0:
        reasons.append(
            {
                "code": "intents-unknown",
                "severity": "critical",
                "detail": str(data.get("unknown_intents")),
            }
        )
    for intent in data.get("errored_intents") or []:
        reasons.append(
            {
                "code": "intent-error:" + str(intent.get("client_order_id")),
                "severity": "warning",
                "detail": str(intent.get("error") or "")[:160],
            }
        )
    if int(data.get("stale_reservations") or 0) > 0:
        reasons.append(
            {
                "code": "reservations-stale",
                "severity": "critical",
                "detail": str(data.get("stale_reservations")),
            }
        )
    for update in data.get("failed_management") or []:
        reasons.append(
            {
                "code": "management-failed:" + str(update.get("action")),
                "severity": "warning",
                "detail": f"{update.get('symbol')}: {str(update.get('last_error') or '')[:120]}",
            }
        )
    if int(data.get("same_state_transitions") or 0) > 5:
        reasons.append(
            {
                "code": "transition-churn",
                "severity": "warning",
                "detail": str(data.get("same_state_transitions")),
            }
        )
    if int(data.get("outbox_pending") or 0) > 100:
        reasons.append(
            {
                "code": "outbox-backlog",
                "severity": "warning",
                "detail": str(data.get("outbox_pending")),
            }
        )
    if int(data.get("outbox_failed_recent") or 0) > 0:
        reasons.append(
            {
                "code": "outbox-failed",
                "severity": "warning",
                "detail": str(data.get("outbox_failed_recent")),
            }
        )
    return {key: value for key, value in data.items() if key != "known_limit_oids"} | {
        "known_limit_oids": data.get("known_limit_oids") or []
    }


def observe_venue(reasons: list[dict[str, str]], known_limit_oids: list[str]) -> dict[str, Any]:
    result = _compose(
        "exec",
        "-T",
        "monitor-bitget",
        "sh",
        "-lc",
        "cd /app && PYTHONPATH=src /app/.venv/bin/python scripts/watchdog_probe.py",
        timeout=45,
    )
    if result.returncode != 0:
        reasons.append(
            {
                "code": "venue-probe-failed",
                "severity": "warning",
                "detail": (result.stderr or result.stdout)[:200],
            }
        )
        return {}
    try:
        data = _json_output(result.stdout)
    except ValueError:
        reasons.append({"code": "venue-probe-unparseable", "severity": "warning", "detail": ""})
        return {}
    if not isinstance(data, dict):
        reasons.append({"code": "venue-probe-invalid", "severity": "warning", "detail": ""})
        return {}
    for error in data.get("errors") or []:
        reasons.append(
            {"code": "venue-read:" + str(error)[:80], "severity": "warning", "detail": ""}
        )
    skew = data.get("clock_skew_ms")
    if isinstance(skew, (int, float)) and abs(skew) > 5000:
        reasons.append({"code": "clock-skew", "severity": "warning", "detail": f"{int(skew)}ms"})
    for position in data.get("positions") or []:
        symbol = str(position.get("symbol") or "")
        plans = (data.get("plans") or {}).get(symbol) or []
        live_plans = [p for p in plans if str(p.get("status") or "").lower() == "live"]
        has_stop = any(str(p.get("plan_type")) == "pos_loss" for p in live_plans)
        has_target = any(str(p.get("plan_type")) == "pos_profit" for p in live_plans)
        if not has_stop:
            reasons.append(
                {
                    "code": f"position-unprotected:{symbol}",
                    "severity": "critical",
                    "detail": f"size={position.get('total')} no live pos_loss plan",
                }
            )
        if not has_target:
            reasons.append(
                {
                    "code": f"position-no-target:{symbol}",
                    "severity": "warning",
                    "detail": f"size={position.get('total')} no live pos_profit plan",
                }
            )
    known = {str(oid) for oid in known_limit_oids or []}
    for order in data.get("open_orders") or []:
        oid = str(order.get("client_oid") or "")
        if oid not in known:
            reasons.append(
                {
                    "code": "unexpected-order:" + (oid or str(order.get("symbol"))),
                    "severity": "warning",
                    "detail": (
                        f"{order.get('symbol')} {order.get('side')} "
                        f"{order.get('size')}@{order.get('price')}"
                    ),
                }
            )
    return {
        "position_count": len(data.get("positions") or []),
        "positions": data.get("positions") or [],
        "open_order_count": len(data.get("open_orders") or []),
        "clock_skew_ms": skew,
    }


def enqueue_card(status: str, reasons: list[dict[str, str]], *, reminder: bool = False) -> None:
    if not ALERTS_ENABLED:
        return
    codes = sorted({reason["code"] for reason in reasons})
    digest = hashlib.sha1("\n".join(codes).encode()).hexdigest()[:12]
    if reminder:
        bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        dedup = f"watchdog-reminder:{status}:{bucket}"
    else:
        dedup = f"watchdog-state:{status}:{digest}"
    critical = sum(1 for reason in reasons if reason["severity"] == "critical")
    warning = sum(1 for reason in reasons if reason["severity"] == "warning")
    headline = "all checks clear" if status == "ok" else f"{critical} critical, {warning} warning"
    codes = [reason["code"] for reason in reasons][:5]
    summary = "; ".join(codes)
    payload = {
        "kind": "system-event",
        "state": status,
        "message": f"watchdog {status}: {headline}" + (f" · {summary}" if summary else ""),
        "reasons": [f"{reason['severity']}:{reason['code']}" for reason in reasons][:20],
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).replace("'", "''")
    statement = (
        "INSERT INTO notifications_outbox (id, dedup_key, payload) VALUES "
        f"(gen_random_uuid(), '{dedup.replace(chr(39), chr(39) * 2)}', '{encoded}'::jsonb) "
        "ON CONFLICT (dedup_key) DO NOTHING"
    )
    try:
        _psql(statement)
    except Exception as exc:  # noqa: BLE001 - never crash the watchdog on alerting
        print(f"watchdog: alert enqueue failed: {type(exc).__name__}", flush=True)


def write_state(status: str, reasons: list[dict[str, str]], observation: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    status_payload = {
        "status": status,
        "checked_at": now,
        "revision": _revision(),
        "reasons": reasons,
        "observation": observation,
    }
    (STATE_DIR / "status.json").write_text(
        json.dumps(status_payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    with (STATE_DIR / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(status_payload, separators=(",", ":"), default=str) + "\n")


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    last_status: str | None = None
    last_alert_at = 0.0
    status_path = STATE_DIR / "status.json"
    if status_path.exists():
        try:
            last_status = json.loads(status_path.read_text(encoding="utf-8")).get("status")
        except Exception:  # noqa: BLE001
            last_status = None
    print(
        f"watchdog: start interval={INTERVAL}s revision={_revision()} alerts={ALERTS_ENABLED}",
        flush=True,
    )
    while True:
        reasons: list[dict[str, str]] = []
        observation: dict[str, Any] = {}
        try:
            observation["containers"] = observe_containers(reasons)
            observation["health"] = observe_health(reasons)
            ledger = observe_ledger(reasons)
            observation["ledger"] = {
                key: value for key, value in ledger.items() if key != "known_limit_oids"
            }
            observation["venue"] = observe_venue(reasons, ledger.get("known_limit_oids") or [])
        except Exception as exc:  # noqa: BLE001 - observation failure is itself an alert
            reasons.append(
                {
                    "code": "watchdog-observation-failed",
                    "severity": "critical",
                    "detail": type(exc).__name__,
                }
            )
        critical = any(reason["severity"] == "critical" for reason in reasons)
        warning = any(reason["severity"] == "warning" for reason in reasons)
        status = "alert" if critical else "degraded" if warning else "ok"
        write_state(status, reasons, observation)
        now = time.monotonic()
        changed = status != last_status
        reminder = status != "ok" and (now - last_alert_at) >= REMINDER_SECONDS
        if changed or reminder:
            enqueue_card(status, reasons, reminder=reminder and not changed)
            last_alert_at = now
        if changed:
            summary = ", ".join(reason["code"] for reason in reasons) or "none"
            print(f"watchdog: status={status} reasons={summary}", flush=True)
        last_status = status
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
