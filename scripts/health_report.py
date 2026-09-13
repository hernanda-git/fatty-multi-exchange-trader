#!/usr/bin/env python3
"""Periodic health report for Fatty Bitget.

Rich Telegram HTML report modeled after the original telegram_health_report.sh format.
Runs on host, uses docker compose exec for DB + Bitget API access.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
        url = f"https://api.bitget.com/api/v2/mix/market/ticker?symbol={symbol}&productType=USDT-FUTURES"
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
        return {"equity": parts[0], "available": parts[1], "unrealized_pl": parts[2]}
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
        " FROM venue_kill_switches WHERE scope = 'bitget')"
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
    }


def get_service_status() -> dict[str, int | str]:
    """Read Compose service state without changing containers."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "ps",
            "--format",
            "{{.Service}}|{{.State}}|{{.Health}}",
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        return {"status": "UNKNOWN", "total": 0, "running": 0, "healthy": 0}
    rows = [line.split("|", 2) for line in result.stdout.splitlines() if line]
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
    }


# ── Helpers ──────────────────────────────────────────────────────────────


def fmt_price(val) -> str:
    if val is None or val == "N/A":
        return "N/A"
    try:
        d = Decimal(str(val))
        return f"{d:.8f}".rstrip("0").rstrip(".")
    except (InvalidOperation, TypeError, ValueError):
        return str(val)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "", "N/A", "?"):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def fmt_amount(value: Any) -> str:
    decimal = _decimal(value)
    if decimal is None:
        return str(value) if value not in (None, "") else "N/A"
    return f"{decimal:,.6f}".rstrip("0").rstrip(".") or "0"


def fmt_gap(mark: Any, level: Any) -> str:
    mark_decimal = _decimal(mark)
    level_decimal = _decimal(level)
    if mark_decimal is None or level_decimal is None or mark_decimal == 0:
        return "N/A"
    return f"{abs(mark_decimal - level_decimal) / abs(mark_decimal) * 100:.2f}%"


def fallback_is_active(protection: dict[str, Any]) -> bool:
    return bool(
        protection.get("fallback")
        or protection.get("sl_label") == "MON"
        or protection.get("tp_label") == "MON"
    )


def protection_state(protection: dict[str, Any]) -> str:
    if fallback_is_active(protection):
        state = str(protection.get("fallback_state", "ACTIVE")).upper()
        return f"FALLBACK {state}"
    if protection.get("has_sl") and protection.get("has_tp"):
        return "NATIVE OK"
    if protection.get("has_sl") or protection.get("has_tp"):
        return "PARTIAL"
    return "MISSING"


def _html(value: Any, default: str = "N/A", limit: int = 500) -> str:
    text = default if value is None or value == "" else str(value)
    return escape(text[:limit], quote=False)


def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _provider_summary(positions, pending_orders) -> str:
    if positions is None or pending_orders is None:
        return "UNKNOWN · provider read failed"
    return f"OK · {len(positions)} pos · {len(pending_orders)} orders"


def _reconciliation_summary(positions, metrics: dict[str, Any]) -> str:
    provider_count = metrics.get("provider_positions", "UNKNOWN")
    db_count = metrics.get("open_positions", "0")
    if positions is None:
        return "UNKNOWN · provider read failed"
    if str(provider_count) == str(db_count):
        return f"OK · provider {provider_count} = DB {db_count}"
    return f"DRIFT · provider {provider_count} vs DB {db_count}"


def _position_range_state(position: dict[str, Any], protection: dict[str, Any]) -> str:
    mark = _decimal(position.get("mark_price"))
    sl = (
        protection.get("fallback_sl")
        if fallback_is_active(protection)
        else protection.get("native_sl")
    )
    tp = (
        protection.get("fallback_tp")
        if fallback_is_active(protection)
        else protection.get("native_tp")
    )
    if isinstance(tp, str) and "," in tp:
        tp = tp.split(",", 1)[0].strip()
    stop = _decimal(sl)
    target = _decimal(tp)
    if mark is None or stop is None or target is None:
        return "N/A"
    direction = str(position.get("direction", "LONG")).upper()
    if direction == "SHORT":
        if mark >= stop:
            return "AT/ABOVE SL"
        if mark <= target:
            return "AT/BELOW TP"
    else:
        if mark <= stop:
            return "AT/BELOW SL"
        if mark >= target:
            return "AT/ABOVE TP"
    return "BETWEEN SL / TP"


def _position_risk_lines(position: dict[str, Any], protection: dict[str, Any]) -> list[str]:
    mark = position.get("mark_price")
    liquidation = position.get("liquidation_price")
    sl = (
        protection.get("fallback_sl")
        if fallback_is_active(protection)
        else protection.get("native_sl")
    )
    tp = (
        protection.get("fallback_tp")
        if fallback_is_active(protection)
        else protection.get("native_tp")
    )
    if isinstance(tp, str) and "," in tp:
        tp = tp.split(",", 1)[0].strip()
    lines = [
        f"Range      {_position_range_state(position, protection)}",
        f"Liq gap    {fmt_gap(mark, liquidation)}",
    ]
    if sl not in (None, "", "N/A"):
        lines.append(f"SL gap     {fmt_gap(mark, sl)}")
    if tp not in (None, "", "N/A"):
        lines.append(f"TP gap     {fmt_gap(mark, tp)}")
    return lines


def fmt_pnl(val) -> tuple[str, str]:
    if val is None or val == "N/A":
        return "➖", "N/A"
    try:
        d = Decimal(str(val))
        s = f"{d:,.6f}".rstrip("0").rstrip(".")
        if d > 0:
            return "🟢", f"+{s}"
        if d < 0:
            return "🔴", s
        return "➖", s
    except (InvalidOperation, TypeError, ValueError):
        return "➖", str(val)


def _format_timestamp(iso_string: str) -> str:
    if not iso_string:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_string.replace(" ", "T").replace("+00", "+00:00"))
        jakarta_tz = timezone(timedelta(hours=7))
        return dt.astimezone(jakarta_tz).strftime("%d %b %Y, %H:%M WIB")
    except (TypeError, ValueError):
        return str(iso_string)


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
    except Exception:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            return {"status": "STALE", **cached}
        except Exception:
            return {
                "status": "N/A",
                "plan": "N/A",
                "5h": "N/A",
                "7d": "N/A",
                "reset": "N/A",
                "refreshed": "never",
            }


# ── Format ────────────────────────────────────────────────────────────────


def format_report(
    positions,
    pending_orders,
    sltp,
    pnl,
    messages,
    metrics,
    account,
    modes,
    codex,
    services: dict[str, Any] | None = None,
):
    jakarta_tz = timezone(timedelta(hours=7))
    now = datetime.now(UTC).astimezone(jakarta_tz).strftime("%d %b %Y, %H:%M WIB")
    metrics = metrics or {}
    account = account or {}
    modes = modes or {}
    codex = codex or {}
    services = services or {}

    provider_known = positions is not None and pending_orders is not None
    service_unhealthy = _count(services.get("unhealthy")) > 0
    overall = "🟢 ONLINE" if provider_known and not service_unhealthy else "⚠️ DEGRADED"
    execution_raw = str(modes.get("execution_enabled", "UNKNOWN"))
    execution = {"1": "ENABLED", "0": "DISABLED"}.get(execution_raw, execution_raw)
    provider_count = len(positions) if positions is not None else "UNKNOWN"
    pending_count = len(pending_orders) if pending_orders is not None else "UNKNOWN"
    db_position_count = metrics.get("open_positions", "0")
    db_pending_count = metrics.get("pending_orders", "0")
    provider_summary = _provider_summary(positions, pending_orders)
    provider_reconciliation = _reconciliation_summary(
        positions, {**metrics, "provider_positions": provider_count}
    )

    kill_state = str(metrics.get("kill_switch", "UNKNOWN")).upper()
    if kill_state == "ACTIVE":
        kill_display = "🔴 ACTIVE"
    elif kill_state == "RELEASED":
        kill_display = "✅ INACTIVE · released"
    elif kill_state == "INACTIVE":
        kill_display = "✅ INACTIVE"
    else:
        kill_display = "⚠️ UNKNOWN"

    if services.get("status") == "OK":
        service_line = (
            f"{services.get('running', '?')}/{services.get('total', '?')} running · "
            f"{services.get('healthy', '?')} healthy"
        )
        if _count(services.get("starting")):
            service_line += f" · {services['starting']} starting"
        if _count(services.get("unhealthy")):
            service_line += f" · {services['unhealthy']} unhealthy"
    else:
        service_line = "UNKNOWN"

    def status_mark(value: str) -> str:
        return "✅" if value.startswith("OK") else "⚠️"

    L = [
        "📡 <b>Fatty Signal Relay</b>  <code>LIVE HEALTH</code>",
        f"<i>{now}</i>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"<b>{overall}  SYSTEM</b>",
        "<pre>Runtime    "
        f"{_html(modes.get('mode', '?'))} · Bitget {_html(modes.get('venue_mode', '?'))}\n"
        f"Execution  {_html(execution)}\n"
        f"Host       fspmi-hostinger\n"
        f"Services   {_html(service_line)}\n"
        f"Provider   {status_mark(provider_summary)} {_html(provider_summary)}\n"
        f"Reconcile  {status_mark(provider_reconciliation)} {_html(provider_reconciliation)}\n"
        f"Kill switch {_html(kill_display)}</pre>",
        "",
        f"<b>🤖 CODEX QUOTA</b> <code>{_html(codex.get('status', 'N/A'))} · "
        f"{_html(codex.get('plan', 'N/A'))}</code>",
        "<pre>5h        "
        f"{_html(codex.get('5h'))}\n"
        f"7d        {_html(codex.get('7d'))}\n"
        f"Reset     {_html(codex.get('reset'))}\n"
        f"Updated   {_html(codex.get('refreshed'))}</pre>",
        "",
        "<b>💰 ACCOUNT</b> <code>Bitget LIVE</code>",
    ]

    if account:
        account_icon, account_upnl = fmt_pnl(account.get("unrealized_pl"))
        L.append(
            "<pre>Equity     "
            f"{_html(fmt_amount(account.get('equity')))} USDT\n"
            f"Available  {_html(fmt_amount(account.get('available')))} USDT\n"
            f"Open uPnL   {account_icon} {_html(account_upnl)} USDT</pre>"
        )
    else:
        L.append("<pre>UNKNOWN (provider account read failed)</pre>")
    L.append("")

    # ── Provider positions ─────────────────────────────────────────────
    L.append(f"<b>📈 OPEN POSITIONS</b> <code>PROVIDER · {provider_count}</code>")
    if positions is None:
        L.append("<pre>UNKNOWN — provider position read failed</pre>")
    elif not positions:
        L.append("<pre>0 open positions · provider read OK</pre>")
    else:
        for index, position in enumerate(positions[:5], 1):
            symbol = str(position.get("symbol", "?"))
            protection = sltp.get(symbol, {})
            upnl_icon, upnl_value = fmt_pnl(position.get("unrealized_pl"))
            protection_label = protection_state(protection)
            protection_icon = (
                "🟢"
                if protection_label == "NATIVE OK"
                else "🟡"
                if fallback_is_active(protection)
                else "🔴"
            )
            native_sl = protection.get("native_sl") if protection.get("has_sl") else "MISSING"
            native_tp = protection.get("native_tp") if protection.get("has_tp") else "MISSING"
            if fallback_is_active(protection):
                fallback_state = _html(protection.get("fallback_state", "UNKNOWN"))
                fallback_entry = _html(protection.get("fallback_entry"))
                fallback_quantity = _html(protection.get("fallback_quantity"))
                fallback_levels = (
                    f"SL {_html(protection.get('fallback_sl'))} · "
                    f"TP {_html(protection.get('fallback_tp'))}"
                )
                fallback_line = (
                    f"{fallback_state} · qty {fallback_quantity} · entry {fallback_entry}"
                )
            else:
                fallback_levels = "N/A"
                fallback_line = "none"
            position_lines = [
                f"<b>{_html(symbol, limit=32)}</b>  <code>"
                f"{_html(position.get('direction', '?'), limit=12)}</code>",
                "<pre>"
                f"Size        {_html(position.get('qty'), limit=24)}\n"
                f"Entry       {_html(position.get('entry_price'), limit=24)}\n"
                f"Mark        {_html(position.get('mark_price'), limit=24)}\n"
                f"uPnL        {upnl_icon} {_html(upnl_value)} USDT\n"
                f"Leverage    {_html(position.get('leverage'), limit=12)}x · "
                f"{_html(position.get('margin_mode'), limit=16)}\n"
                f"Liquidation {_html(position.get('liquidation_price'), limit=24)}\n"
                f"Protection  {protection_icon} {_html(protection_label)}\n"
                f"Native      SL {_html(native_sl, limit=24)} · TP {_html(native_tp, limit=24)}\n"
                f"Fallback    {_html(fallback_line, limit=120)}\n"
                f"Levels      {_html(fallback_levels, limit=80)}\n"
                + "\n".join(_position_risk_lines(position, protection))
                + "</pre>",
            ]
            L.extend(position_lines)
            if index < min(len(positions), 5):
                L.append("")
        if len(positions) > 5:
            L.append(f"<i>… {len(positions) - 5} more provider positions not shown</i>")
    L.append("")

    # ── Provider pending orders ────────────────────────────────────────
    L.append(f"<b>🧾 PENDING ORDERS</b> <code>PROVIDER · {pending_count}</code>")
    if pending_orders is None:
        L.append("<pre>UNKNOWN — provider open-order read failed</pre>")
    elif not pending_orders:
        L.append("<pre>0 pending orders · provider read OK</pre>")
    else:
        for index, order in enumerate(pending_orders[:5], 1):
            L.append(
                f"<pre>{index}. {_html(order.get('symbol', '?'), limit=32)} · "
                f"{_html(order.get('side', '?'), limit=12)} "
                f"{_html(order.get('role', 'ORDER'), limit=16)}\n"
                f"   Qty        {_html(order.get('qty'), limit=24)}\n"
                f"   Price      {_html(order.get('price'), limit=24)}\n"
                f"   State      {_html(order.get('state'), limit=24)}</pre>"
            )
        if len(pending_orders) > 5:
            L.append(f"<i>… {len(pending_orders) - 5} more provider orders not shown</i>")
    L.append("")

    # ── PnL ────────────────────────────────────────────────────────────
    L.append("<b>📊 PNL</b> <code>Bitget · realized + open</code>")
    fill_count = str(pnl.get("fill_n", "0")) if pnl else "0"
    if pnl and fill_count not in {"0", "N/A", ""}:
        total_realized = _decimal(pnl.get("total_pnl")) or Decimal("0")
        fees = _decimal(pnl.get("total_fees")) or Decimal("0")
        net_realized = total_realized - fees
        net_icon, net_value = fmt_pnl(net_realized)
        if positions is None:
            open_upnl = "UNKNOWN"
        elif not positions:
            open_upnl = "➖ 0"
        else:
            position_values = [_decimal(p.get("unrealized_pl")) for p in positions]
            position_values = [value for value in position_values if value is not None]
            if position_values:
                open_icon, open_value = fmt_pnl(sum(position_values, Decimal("0")))
                open_upnl = f"{open_icon} {open_value}"
            else:
                open_upnl = "N/A"
        L.append(
            "<pre>Fills        "
            f"{_html(fill_count)}\n"
            f"Gross profit {_html(fmt_amount(pnl.get('gross_profit')))} USDT\n"
            f"Gross loss   {_html(fmt_amount(pnl.get('gross_loss')))} USDT\n"
            f"Fees paid    {_html(fmt_amount(fees))} USDT\n"
            f"Net realized {net_icon} {_html(net_value)} USDT\n"
            f"Open uPnL    {open_upnl} USDT</pre>"
        )
    else:
        L.append("<pre>0 fills · no realized PNL yet</pre>")
    L.append("")

    # ── Ledger and reconciliation ──────────────────────────────────────
    provider_pending_value = pending_count
    position_recon_icon = "✅" if str(provider_count) == str(db_position_count) else "⚠️"
    pending_recon_icon = "✅" if str(provider_pending_value) == str(db_pending_count) else "⚠️"
    L.append("<b>🗄 LEDGER &amp; RECONCILIATION</b>")
    L.append(
        "<pre>Messages       "
        f"{_html(metrics.get('messages', '0'))}\n"
        f"Signals        {_html(metrics.get('signals', '0'))}\n"
        f"Positions      DB {_html(db_position_count)} · "
        f"provider {_html(provider_count)} {position_recon_icon}\n"
        f"Pending orders DB {_html(db_pending_count)} · "
        f"provider {_html(provider_pending_value)} {pending_recon_icon}\n"
        f"Orders total   {_html(metrics.get('total_orders', '0'))}\n"
        f"Active intents {_html(metrics.get('active_intents', '0'))}\n"
        f"Fallback mon.  {_html(metrics.get('fallback_positions', '0'))} active</pre>"
    )
    L.append("")

    # ── Risk controls ──────────────────────────────────────────────────
    L.append("<b>🛡 RISK CONTROLS</b>")
    if positions is None:
        L.append(
            "<pre>Provider read  UNKNOWN\n"
            "Margin         UNKNOWN\n"
            "Leverage       UNKNOWN\n"
            "SL-before-liq  UNKNOWN (provider read failed)\n"
            f"Kill switch    {_html(kill_display)}</pre>"
        )
    elif positions:
        margin_modes = sorted({str(p.get("margin_mode", "N/A")) for p in positions})
        leverage_values = sorted({str(p.get("leverage", "?")) for p in positions})
        monitored = []
        missing = []
        for position in positions:
            protection = sltp.get(position["symbol"], {})
            if fallback_is_active(protection):
                monitored.append(position["symbol"])
            elif not protection.get("has_sl"):
                missing.append(position["symbol"])
        if missing:
            guard = f"🔴 MISSING {', '.join(missing)}"
        elif monitored:
            guard = f"🟡 MONITORED {', '.join(monitored)}"
        else:
            guard = "🟢 NATIVE OK"
        L.append(
            "<pre>Provider read  ✅ OK\n"
            f"Margin         {_html(', '.join(margin_modes))}\n"
            f"Leverage       {_html(', '.join(leverage_values))}x\n"
            f"SL-before-liq  {_html(guard)}\n"
            f"Kill switch    {_html(kill_display)}</pre>"
        )
    else:
        L.append(
            "<pre>Provider read  ✅ OK · flat\n"
            "Margin         N/A\n"
            "Leverage       N/A\n"
            "SL-before-liq  N/A · no open positions\n"
            f"Kill switch    {_html(kill_display)}</pre>"
        )
    L.append("")

    # ── Latest signal ──────────────────────────────────────────────────
    if messages:
        message = messages[0]
        msg_id = _html(message.get("msg_id", "?"), limit=64)
        received = _html(_format_timestamp(str(message.get("received_at", ""))), limit=64)
        preview_text = str(message.get("preview", "(empty)")).replace("\n", " ").strip()
        L.append(f"<b>🛰 LATEST SIGNAL</b> <code>#{msg_id}</code>")
        L.append(f"<i>Received {received}</i>")
        L.append(f"<pre>{_html(preview_text, '(empty)', limit=240)}</pre>")
    else:
        L.append("<b>🛰 LATEST SIGNAL</b>\n<i>No source message stored yet.</i>")

    report = "\n".join(L)
    if len(report) > 3900:
        # The normal position/order caps keep the report below Telegram's
        # limit. If an unusually long value still exceeds it, omit the
        # optional source preview while preserving valid HTML structure.
        marker = "<b>🛰 LATEST SIGNAL</b>"
        marker_index = report.find(marker)
        if marker_index >= 0:
            report = (
                report[:marker_index]
                + marker
                + "\n<i>Source preview omitted to fit Telegram limit.</i>"
            )
    return report[:4000]


# ── Delivery ─────────────────────────────────────────────────────────────


def send_direct(html: str) -> bool:
    BOT_TOKEN = "8535529687:AAHxftfVgdTjwbiRh1F9QVAYwTWc4ioy0-c"
    CHAT_ID = "5894116684"
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = json.dumps(
            {
                "chat_id": CHAT_ID,
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
