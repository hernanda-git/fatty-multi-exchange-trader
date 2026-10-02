#!/usr/bin/env python3
# ruff: noqa: E501
"""Periodic health report for Fatty Bitget.

Rich Telegram HTML report modeled after the original telegram_health_report.sh format.
Runs on host, uses docker compose exec for DB + Bitget API access.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc  # noqa: UP017
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The card renderer lives in the package so the Telegram /health slash
# command produces byte-identical output. This script owns the I/O.
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from fatty_trader.operator.health_report_format import (  # noqa: E402
    fmt_price,
    format_report,
)

# ── Docker exec helpers (matches original shell script pattern) ──────────


def query_one(sql: str):
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "fatty_app",
            "-d",
            "fatty_trader",
            "-At",
            "-F",
            "|",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        return {}
    line = result.stdout.strip()
    if not line:
        return {}
    return {f"col{i}": v for i, v in enumerate(line.split("|"))}


def query_all(sql: str):
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "fatty_app",
            "-d",
            "fatty_trader",
            "-At",
            "-F",
            "|",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        return []
    return [line.split("|") for line in result.stdout.strip().split("\n") if line]


def docker_exec_bitget(cmd: str) -> str:
    """Run a command inside the dispatcher-bitget container."""
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "dispatcher-bitget", "sh", "-lc", cmd],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    return result.stdout.strip()


# ── Runtime ──────────────────────────────────────────────────────────────


def get_runtime_modes() -> dict:
    raw = docker_exec_bitget(
        'printf "%s|%s|%s" "$TRADER_MODE" "$BITGET_MODE" "$BITGET_EXECUTION_ENABLED"'
    )
    parts = raw.split("|") if raw else ["UNKNOWN"] * 3
    return {
        "mode": parts[0] if len(parts) > 0 else "UNKNOWN",
        "venue_mode": parts[1] if len(parts) > 1 else "UNKNOWN",
        "execution_enabled": parts[2] if len(parts) > 2 else "UNKNOWN",
    }


def get_current_price(symbol: str) -> Decimal | None:
    try:
        url = f"""https://api.bitget.com/api/v2/mix/market/ticker?symbol={
            (symbol)
        }&productType=USDT-FUTURES"""
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return Decimal(data["data"][0]["lastPr"])
    except Exception:
        return None


def get_account() -> dict:
    raw = docker_exec_bitget("cd /app && . .venv/bin/activate && python3 scripts/_probe_account.py")
    if not raw or "|" not in raw:
        return {}
    parts = raw.split("|")
    if len(parts) >= 3:
        return {
            "equity": parts[0],
            "available": parts[1],
            "unrealized_pl": parts[2],
            "locked": parts[3] if len(parts) > 3 else "N/A",
            "isolated_margin": parts[4] if len(parts) > 4 else "N/A",
            "crossed_margin": parts[5] if len(parts) > 5 else "N/A",
            "margin_mode": parts[6] if len(parts) > 6 else "N/A",
        }
    return {}


def get_single_position(symbol: str) -> list:
    raw = docker_exec_bitget(
        f"cd /app && . .venv/bin/activate && python3 scripts/_probe_position.py {symbol}"
    )
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def load_provider_state() -> dict[str, Any] | None:
    """Read provider positions/orders once; None means provider state is unknown."""
    raw = docker_exec_bitget(
        "cd /app && . .venv/bin/activate && python3 scripts/_probe_open_state.py"
    )
    if not raw:
        return None
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    if not isinstance(state.get("positions"), list) or not isinstance(
        state.get("open_orders"), list
    ):
        return None
    if any(isinstance(value, dict) and "error_type" in value for value in state.values()):
        return None
    return state


def _provider_quantity(row: dict[str, Any]) -> Decimal | None:
    for key in ("total", "size", "quantity"):
        if key not in row:
            continue
        try:
            return abs(Decimal(str(row[key] or "0")))
        except (InvalidOperation, TypeError, ValueError):
            continue
    return None


# ── Data ─────────────────────────────────────────────────────────────────


def load_positions(provider_state: dict[str, Any] | None = None) -> list[dict] | None:
    """Build the open-position view from Bitget, enriching it with local metadata."""
    state = provider_state if provider_state is not None else load_provider_state()
    if state is None:
        return None
    provider_rows = state["positions"]
    local_rows = query_all(
        "SELECT p.exchange, p.symbol, p.direction, p.quantity::text, "
        "p.protection_state, p.opened_at FROM positions p "
        "WHERE p.closed_at IS NULL ORDER BY p.opened_at DESC"
    )
    local_by_symbol = {row[1].upper(): row for row in local_rows if len(row) > 1}
    positions: list[dict] = []
    for row in provider_rows:
        if not isinstance(row, dict):
            continue
        quantity = _provider_quantity(row)
        if quantity is None or quantity <= 0:
            continue
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        local = local_by_symbol.get(symbol)
        positions.append(
            {
                "exchange": "bitget",
                "symbol": symbol,
                "direction": "SHORT" if str(row.get("holdSide", "")).lower() == "short" else "LONG",
                "qty": str(row.get("total", row.get("size", quantity))),
                "protection_state": local[4] if local and len(local) > 4 else "provider",
                "opened_at": local[5] if local and len(local) > 5 else row.get("cTime"),
                "current_price": fmt_price(row.get("markPrice")),
                "entry_price": fmt_price(row.get("openPriceAvg")),
                "mark_price": fmt_price(row.get("markPrice")),
                "unrealized_pl": row.get("unrealizedPL", "N/A"),
                # Bitget's position margin is the amount actually committed by
                # the open position; account.available alone does not expose it.
                "margin_used": row.get("marginSize", row.get("margin", "N/A")),
                "leverage": str(row.get("leverage", "?")),
                "margin_mode": str(row.get("marginMode", "N/A")).lower(),
                "liquidation_price": fmt_price(row.get("liquidationPrice")),
            }
        )
    return positions


def load_pending_orders(provider_state: dict[str, Any] | None = None) -> list[dict] | None:
    """Return provider pending orders; None means the provider read failed."""
    state = provider_state if provider_state is not None else load_provider_state()
    if state is None:
        return None
    orders = state["open_orders"]
    return [
        {
            "exchange": "bitget",
            "symbol": str(row.get("symbol", "?")),
            "side": str(row.get("side", "?")).upper(),
            "role": str(row.get("role", row.get("orderType", "ORDER"))).upper(),
            "qty": str(row.get("size", row.get("quantity", "?"))),
            "price": str(row.get("price", row.get("executePrice", "N/A"))),
            "state": str(row.get("status", row.get("state", "?"))).upper(),
        }
        for row in orders
        if isinstance(row, dict)
    ]


def load_sltp_status(provider_state: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Report native protection versus bot-managed fallback protection."""
    statuses: dict[str, dict[str, Any]] = {}
    if provider_state is not None:
        for row in provider_state.get("positions", []):
            if not isinstance(row, dict) or (_provider_quantity(row) or Decimal("0")) <= 0:
                continue
            symbol = str(row.get("symbol", "")).upper()
            if not symbol:
                continue
            native_sl_id = str(row.get("stopLossId") or "")
            native_tp_id = str(row.get("takeProfitId") or "")
            native_sl_value = row.get("stopLoss") or row.get("stopLossPrice")
            native_tp_value = row.get("takeProfit") or row.get("takeProfitPrice")
            statuses[symbol] = {
                "has_sl": bool(native_sl_id or native_sl_value),
                "has_tp": bool(native_tp_id or native_tp_value),
                "sl_label": "OK" if (native_sl_id or native_sl_value) else "MISS",
                "tp_label": "OK" if (native_tp_id or native_tp_value) else "MISS",
                "native_sl": fmt_price(native_sl_value)
                if native_sl_value
                else (f"id:{native_sl_id}" if native_sl_id else "N/A"),
                "native_tp": fmt_price(native_tp_value)
                if native_tp_value
                else (f"id:{native_tp_id}" if native_tp_id else "N/A"),
            }
    fallback_rows = query_all(
        "SELECT symbol, direction, entry_price::text, stop_loss::text, "
        "take_profits::text, quantity::text, state, position_key, updated_at::text "
        "FROM fallback_protection "
        "WHERE exchange = 'bitget' AND state IN ('active', 'closing') "
        "ORDER BY updated_at DESC"
    )
    for row in fallback_rows:
        if not row:
            continue
        symbol = str(row[0]).upper()
        status = statuses.setdefault(symbol, {"has_sl": False, "has_tp": False})
        take_profits: list[str] = []
        if len(row) > 4 and row[4]:
            try:
                raw_tps = json.loads(row[4])
                if isinstance(raw_tps, list):
                    take_profits = [fmt_price(value) for value in raw_tps]
            except (TypeError, ValueError, InvalidOperation):
                take_profits = [str(row[4])]
        status.update(
            {
                "fallback": True,
                "fallback_direction": str(row[1]).upper() if len(row) > 1 else "N/A",
                "fallback_entry": row[2] if len(row) > 2 else "N/A",
                "fallback_sl": row[3] if len(row) > 3 else "N/A",
                "fallback_tp": ", ".join(take_profits) if take_profits else "N/A",
                "fallback_quantity": row[5] if len(row) > 5 else "N/A",
                "fallback_state": str(row[6]).upper() if len(row) > 6 else "UNKNOWN",
                "fallback_position_key": row[7] if len(row) > 7 else "N/A",
                "fallback_updated_at": row[8] if len(row) > 8 else "N/A",
            }
        )
        if not status.get("has_sl"):
            status["sl_label"] = "MON"
        if not status.get("has_tp"):
            status["tp_label"] = "MON"
    return statuses


def load_pnl() -> dict:
    row = query_one(
        "SELECT count(*)::text, "
        "coalesce(sum(realized_pnl), 0)::text, "
        "coalesce(sum(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 0)::text, "
        "coalesce(sum(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END), 0)::text, "
        "coalesce(sum(fee), 0)::text "
        "FROM fills WHERE exchange = 'bitget'"
    )
    if not row:
        return {}
    return {
        "fill_n": row.get("col0", "0"),
        "total_pnl": row.get("col1", "0"),
        "gross_profit": row.get("col2", "0"),
        "gross_loss": row.get("col3", "0"),
        "total_fees": row.get("col4", "0"),
    }


def load_latest_messages(n: int = 1) -> list[dict]:
    raw = query_all(
        f"SELECT message_id::text, received_at, "
        f"LEFT(replace(replace(raw_text, chr(13), ' '), chr(10), ' '), 500) "
        f"FROM telegram_messages ORDER BY received_at DESC, message_id DESC LIMIT {n}"
    )
    return [{"msg_id": r[0], "received_at": r[1], "preview": r[2]} for r in raw]


def load_db_metrics() -> dict:
    row = query_one(
        "SELECT "
        "(SELECT count(*)::text FROM telegram_messages), "
        "(SELECT count(*)::text FROM canonical_signals), "
        "(SELECT count(*)::text FROM positions WHERE closed_at IS NULL), "
        "(SELECT count(*)::text FROM orders "
        "WHERE state NOT IN ('FILLED','CANCELLED','REJECTED','CLOSED')), "
        "(SELECT count(*)::text FROM orders), "
        "(SELECT count(*)::text FROM live_order_intents WHERE exchange = 'bitget' "
        " AND state NOT IN ('filled','rejected','cancelled','reconciled')), "
        "(SELECT count(*)::text FROM fallback_protection WHERE exchange = 'bitget' "
        " AND state IN ('active','closing')), "
        "(SELECT CASE WHEN active THEN 'ACTIVE' "
        " WHEN reason LIKE 'released:%' THEN 'RELEASED' ELSE 'INACTIVE' END "
        " FROM venue_kill_switches WHERE scope = 'bitget'), "
        "(SELECT COALESCE(json_object_agg(state, count), '{}'::json) FROM (SELECT state, count(*) FROM bitget_margin_reservations WHERE exchange = 'bitget' GROUP BY state) r), "
        "(SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - max(captured_at)))::text FROM balance_snapshots WHERE exchange = 'bitget'), "
        "(SELECT status FROM bitget_post_fill_reconciliations WHERE exchange = 'bitget' ORDER BY created_at DESC LIMIT 1)"
    )
    return {
        "messages": row.get("col0", "0"),
        "signals": row.get("col1", "0"),
        "open_positions": row.get("col2", "0"),
        "pending_orders": row.get("col3", "0"),
        "total_orders": row.get("col4", "0"),
        "active_intents": row.get("col5", "0"),
        "fallback_positions": row.get("col6", "0"),
        "kill_switch": row.get("col7", "UNKNOWN"),
        "reservation_totals": row.get("col8", "{}"),
        "newest_balance_snapshot_age_seconds": row.get("col9", "N/A"),
        "latest_post_fill_reconciliation": row.get("col10", "N/A"),
    }


def get_service_status() -> dict[str, int | str]:
    """Read Compose service state without changing containers."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "ps",
            "--all",
            "--format",
            "{{.Service}}|{{.State}}|{{.Health}}",
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        return {"status": "UNKNOWN", "total": 0, "running": 0, "healthy": 0}
    config = subprocess.run(
        [
            "docker",
            "compose",
            "config",
            "--no-interpolate",
            "--no-env-resolution",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    try:
        if config.returncode != 0:
            raise ValueError("Compose config unavailable")
        configured = json.loads(config.stdout)["services"]
        # One-shot setup jobs and disabled lanes are not LIVE operational health.
        excluded = {"init", "migrate", "paper-kaka", "dispatcher-binance", "monitor-binance"}
        expected = {
            name
            for name, service in configured.items()
            if name not in excluded
            and not name.startswith("paper-")
            and "demo" not in name.lower()
            and not service.get("profiles")
        }
    except (ValueError, KeyError, TypeError):
        return {"status": "UNKNOWN", "total": 0, "running": 0, "healthy": 0}
    rows = [line.split("|", 2) for line in result.stdout.splitlines() if line]
    rows = [
        row
        for row in rows
        if row[0] in expected
        or (row[0] not in {"init", "migrate"} and len(row) > 1 and row[1].lower() == "running")
    ]
    unexpected = sum(row[0] not in expected for row in rows)
    present = {row[0] for row in rows}
    rows.extend([name, "missing", ""] for name in sorted(expected - present))
    running = sum(len(row) > 1 and row[1].lower() == "running" for row in rows)
    healthy = sum(len(row) > 2 and row[2].lower() == "healthy" for row in rows)
    starting = sum(len(row) > 2 and row[2].lower() == "starting" for row in rows)
    unhealthy = sum(len(row) > 2 and row[2].lower() == "unhealthy" for row in rows)
    return {
        "status": "OK",
        "total": len(rows),
        "running": running,
        "healthy": healthy,
        "starting": starting,
        "unhealthy": unhealthy,
        "unexpected_runtime": unexpected,
    }


# ── Helpers ──────────────────────────────────────────────────────────────


# ── Codex Usage ──────────────────────────────────────────────────────────


def get_codex_usage() -> dict:
    """Fetch Codex usage from ChatGPT API (matching original telegram_health_report.sh logic)."""
    cache_path = Path.home() / ".cache" / "fatty" / "codex_usage.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    def fmt_reset(seconds):
        if seconds is None:
            return "N/A"
        seconds = max(0, int(seconds))
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        if days:
            return f"{days}d {hours}h"
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    def auth_candidates():
        for path in (
            Path.home() / ".pi" / "agent" / "auth.json",
            Path.home() / ".codex" / "auth.json",
        ):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue

            def walk(value):
                if isinstance(value, dict):
                    token = value.get("access_token") or value.get("accessToken")
                    account = value.get("account_id") or value.get("accountId")
                    if isinstance(token, str) and token and not token.startswith("sk-"):
                        yield token, account if isinstance(account, str) else None
                    for child in value.values():
                        yield from walk(child)

            yield from walk(data)

    try:
        token, account_id = next(auth_candidates())
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        if account_id:
            headers["chatgpt-account-id"] = account_id
        req = urllib.request.Request("https://chatgpt.com/backend-api/wham/usage", headers=headers)
        with urllib.request.urlopen(req, timeout=12) as response:
            data = json.load(response)
        rate = data["rate_limit"]
        primary = rate["primary_window"]
        secondary = rate["secondary_window"]
        now = datetime.now(UTC).astimezone(timezone(timedelta(hours=7))).strftime("%m-%d %H:%M WIB")
        plan = str(data.get("plan_type") or "N/A")
        fresh = {
            "5h": f"{primary['used_percent']}% used / {100 - primary['used_percent']}% left",
            "7d": f"{secondary['used_percent']}% used / {100 - secondary['used_percent']}% left",
            "reset": (
                f"5h {fmt_reset(primary.get('reset_after_seconds'))}; "
                f"7d {fmt_reset(secondary.get('reset_after_seconds'))}"
            ),
            "plan": plan,
            "refreshed": now,
            "saved_at": int(datetime.now().timestamp()),
        }
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(fresh), encoding="utf-8")
        tmp.rename(cache_path)
        return {"status": "LIVE", **fresh}
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            return {
                "status": "AUTH_FAILED",
                "plan": "N/A",
                "5h": "N/A",
                "7d": "N/A",
                "reset": "N/A",
                "refreshed": "auth failed",
                "error": f"Codex usage API HTTP {exc.code}; re-authentication required",
            }
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            return {"status": "STALE", **cached, "error": f"Codex usage API HTTP {exc.code}"}
        except Exception:
            return {
                "status": "N/A",
                "plan": "N/A",
                "5h": "N/A",
                "7d": "N/A",
                "reset": "N/A",
                "refreshed": "never",
                "error": f"Codex usage API HTTP {exc.code}",
            }
    except Exception as exc:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            error = f"Codex usage unavailable: {type(exc).__name__}"
            return {"status": "STALE", **cached, "error": error}
        except Exception:
            return {
                "status": "N/A",
                "plan": "N/A",
                "5h": "N/A",
                "7d": "N/A",
                "reset": "N/A",
                "refreshed": "never",
                "error": f"Codex usage unavailable: {type(exc).__name__}",
            }


# ── Format ────────────────────────────────────────────────────────────────


def send_direct(html: str) -> bool:
    bot_token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_TARGET_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = json.dumps(
            {
                "chat_id": chat_id,
                "text": html,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("ok", False)
    except Exception as e:
        print(f"Direct send failed: {e}")
        return False


if __name__ == "__main__":
    modes = get_runtime_modes()
    account = get_account()
    provider_state = load_provider_state()
    positions = load_positions(provider_state)
    pending = load_pending_orders(provider_state)
    sltp = load_sltp_status(provider_state)
    pnl = load_pnl()
    messages = load_latest_messages(1)
    metrics = load_db_metrics()
    metrics["provider_positions"] = "UNKNOWN" if positions is None else str(len(positions))
    metrics["provider_pending_orders"] = "UNKNOWN" if pending is None else str(len(pending))
    codex = get_codex_usage()
    services = get_service_status()

    report = format_report(
        positions,
        pending,
        sltp,
        pnl,
        messages,
        metrics,
        account,
        modes,
        codex,
        services,
    )
    print(report)
    print("\n---")

    if send_direct(report):
        print("SENT")
    else:
        print("FAILED")
        sys.exit(1)
