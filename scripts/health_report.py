#!/usr/bin/env python3
"""Periodic health report for Fatty Bitget.

Rich Telegram HTML report modeled after the original telegram_health_report.sh format.
Runs on host, uses docker compose exec for DB + Bitget API access.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── Docker exec helpers (matches original shell script pattern) ──────────

def query_one(sql: str):
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "fatty_app", "-d", "fatty_trader", "-At", "-F", "|", "-c", sql],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        return {}
    line = result.stdout.strip()
    if not line:
        return {}
    return {f"col{i}": v for i, v in enumerate(line.split("|"))}


def query_all(sql: str):
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "fatty_app", "-d", "fatty_trader", "-At", "-F", "|", "-c", sql],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        return []
    return [line.split("|") for line in result.stdout.strip().split("\n") if line]


def docker_exec_bitget(cmd: str) -> str:
    """Run a command inside the dispatcher-bitget container."""
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "dispatcher-bitget", "sh", "-lc", cmd],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    return result.stdout.strip()


# ── Runtime ──────────────────────────────────────────────────────────────

def get_runtime_modes() -> dict:
    raw = docker_exec_bitget('printf "%s|%s|%s" "$TRADER_MODE" "$BITGET_MODE" "$BITGET_EXECUTION_ENABLED"')
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
    raw = docker_exec_bitget('cd /app && . .venv/bin/activate && python3 scripts/_probe_account.py')
    if not raw or "|" not in raw:
        return {}
    parts = raw.split("|")
    if len(parts) >= 3:
        return {"equity": parts[0], "available": parts[1], "unrealized_pl": parts[2]}
    return {}


def get_single_position(symbol: str) -> list:
    raw = docker_exec_bitget(f'cd /app && . .venv/bin/activate && python3 scripts/_probe_position.py {symbol}')
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


# ── Data ─────────────────────────────────────────────────────────────────

def load_positions() -> list[dict]:
    raw = query_all("SELECT p.exchange, p.symbol, p.direction, p.quantity::text, p.protection_state, p.opened_at FROM positions p WHERE p.closed_at IS NULL ORDER BY p.opened_at DESC")
    if not raw:
        return []
    positions = []
    for row in raw:
        pos = {"exchange": row[0], "symbol": row[1], "direction": row[2], "qty": row[3], "protection_state": row[4], "opened_at": row[5]}
        try:
            detail = get_single_position(pos["symbol"])
            price = get_current_price(pos["symbol"])
            pos["current_price"] = fmt_price(price) if price else "N/A"
            if detail and isinstance(detail, list) and len(detail) > 0:
                r = detail[0]
                pos["entry_price"] = fmt_price(r.get("openPriceAvg"))
                pos["mark_price"] = fmt_price(r.get("markPrice"))
                pos["unrealized_pl"] = r.get("unrealizedPL", "N/A")
                pos["leverage"] = r.get("leverage", "?")
                pos["margin_mode"] = r.get("margin_mode", "N/A")
                pos["liquidation_price"] = fmt_price(r.get("liquidationPrice"))
            else:
                pos["entry_price"] = "N/A"
                pos["mark_price"] = "N/A"
                pos["unrealized_pl"] = "N/A"
                pos["leverage"] = "?"
                pos["margin_mode"] = "N/A"
                pos["liquidation_price"] = "N/A"
        except Exception:
            pos["current_price"] = "ERR"
            pos["entry_price"] = "N/A"
            pos["mark_price"] = "N/A"
            pos["unrealized_pl"] = "N/A"
            pos["leverage"] = "?"
            pos["margin_mode"] = "N/A"
            pos["liquidation_price"] = "N/A"
        positions.append(pos)
    return positions


def load_pending_orders() -> list[dict]:
    raw = query_all("SELECT li.exchange, li.symbol, li.role, li.state, li.requested_qty::text, li.requested_price::text, li.filled_qty::text FROM live_order_intents li WHERE li.state NOT IN ('filled','rejected','cancelled','reconciled') AND li.role = 'ENTRY' ORDER BY li.created_at DESC")
    return [{"exchange": r[0], "symbol": r[1], "role": r[2], "state": r[3], "qty": r[4], "price": r[5], "filled": r[6]} for r in raw]


def load_sltp_status() -> dict:
    raw = query_all("SELECT symbol, bool_or(role = 'SL' AND state IN ('requested','acknowledged','submitted')) AS has_sl, bool_or(role = 'TP' AND state IN ('requested','acknowledged','submitted')) AS has_tp FROM live_order_intents WHERE exchange = 'bitget' GROUP BY symbol")
    return {r[0]: {"has_sl": r[1] == "true", "has_tp": r[2] == "true"} for r in raw}


def load_pnl() -> dict:
    row = query_one("SELECT count(*)::text, coalesce(sum(realized_pnl),0)::text, coalesce(sum(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END),0)::text, coalesce(sum(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END),0)::text, coalesce(sum(fee),0)::text FROM fills WHERE exchange = 'bitget'")
    if not row:
        return {}
    return {"fill_n": row.get("col0", "0"), "total_pnl": row.get("col1", "0"), "gross_profit": row.get("col2", "0"), "gross_loss": row.get("col3", "0"), "total_fees": row.get("col4", "0")}


def load_latest_messages(n: int = 1) -> list[dict]:
    raw = query_all(f"SELECT message_id::text, received_at, LEFT(raw_text, 500) FROM telegram_messages ORDER BY received_at DESC, message_id DESC LIMIT {n}")
    return [{"msg_id": r[0], "received_at": r[1], "preview": r[2]} for r in raw]


def load_db_metrics() -> dict:
    row = query_one("SELECT (SELECT count(*)::text FROM telegram_messages), (SELECT count(*)::text FROM canonical_signals), (SELECT count(*)::text FROM positions WHERE closed_at IS NULL), (SELECT count(*)::text FROM orders WHERE state NOT IN ('FILLED','CANCELLED','REJECTED','CLOSED')), (SELECT count(*)::text FROM orders)")
    return {"messages": row.get("col0", "0"), "signals": row.get("col1", "0"), "open_positions": row.get("col2", "0"), "pending_orders": row.get("col3", "0"), "total_orders": row.get("col4", "0")}


# ── Helpers ──────────────────────────────────────────────────────────────

def fmt_price(val) -> str:
    if val is None or val == "N/A":
        return "N/A"
    try:
        d = Decimal(str(val))
        return f"{d:.6f}".rstrip("0").rstrip(".")
    except Exception:
        return str(val)


def fmt_pnl(val) -> tuple[str, str]:
    if val is None or val == "N/A":
        return "➖", "N/A"
    try:
        d = Decimal(str(val))
        s = f"{d:,.6f}".rstrip("0").rstrip(".")
        if d > 0:
            return "🟢", f"+{s}"
        elif d < 0:
            return "🔴", s
        return "➖", s
    except Exception:
        return "➖", str(val)


def _format_timestamp(iso_string: str) -> str:
    if not iso_string:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_string.replace(" ", "T").replace("+00", "+00:00"))
        jakarta_tz = timezone(timedelta(hours=7))
        return dt.astimezone(jakarta_tz).strftime("%d %b %Y, %H:%M WIB")
    except Exception:
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
        for path in (Path.home() / ".pi" / "agent" / "auth.json", Path.home() / ".codex" / "auth.json"):
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
        now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=7))).strftime("%m-%d %H:%M WIB")
        plan = str(data.get("plan_type") or "N/A")
        fresh = {
            "5h": f"{primary['used_percent']}% used / {100 - primary['used_percent']}% left",
            "7d": f"{secondary['used_percent']}% used / {100 - secondary['used_percent']}% left",
            "reset": f"5h {fmt_reset(primary.get('reset_after_seconds'))}; 7d {fmt_reset(secondary.get('reset_after_seconds'))}",
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
            return {"status": "N/A", "plan": "N/A", "5h": "N/A", "7d": "N/A", "reset": "N/A", "refreshed": "never"}


# ── Format ────────────────────────────────────────────────────────────────

def format_report(positions, pending_orders, sltp, pnl, messages, metrics, account, modes, codex):
    jakarta_tz = timezone(timedelta(hours=7))
    now = datetime.now(timezone.utc).astimezone(jakarta_tz).strftime("%d %b %Y, %H:%M WIB")

    L = []
    L.append("<b>Fatty Signal Relay</b>  <i>Laporan Kesehatan LIVE</i>")
    L.append(f"<i>{now}</i>")
    L.append("")

    # ── Status ─────────────────────────────────────────────────────────
    L.append("<b>Status</b>")
    L.append(f"<pre>Overall  🟢 ONLINE")
    L.append(f"Mode     {modes.get('mode', '?')} · Bitget {modes.get('venue_mode', '?')}")
    L.append(f"Host     fspmi-hostinger")
    L.append(f"Eksekusi {modes.get('execution_enabled', '?')}</pre>")
    L.append("")

    # ── Codex Usage ────────────────────────────────────────────────────
    L.append(f"<b>Codex Usage</b> <code>{codex['status']}</code>")
    L.append(f"<pre>Plan     {codex['plan']}")
    L.append(f"Window   Used / Left")
    L.append(f"5h       {codex['5h']}")
    L.append(f"7d       {codex['7d']}")
    L.append(f"Reset    {codex['reset']}")
    L.append(f"Updated  {codex['refreshed']}</pre>")
    L.append("")

    # ── Balance ────────────────────────────────────────────────────────
    L.append("<b>Balance</b> <code>bitget LIVE</code>")
    if account:
        L.append(f"<pre>Equity     {account.get('equity', 'N/A')} USDT")
        L.append(f"Available  {account.get('available', 'N/A')} USDT")
        L.append(f"Unrealized {account.get('unrealized_pl', 'N/A')} USDT</pre>")
    else:
        L.append("<pre>N/A (no account snapshot)</pre>")
    L.append("")

    # ── Positions ──────────────────────────────────────────────────────
    L.append("<b>Positions</b> <code>open · bitget</code>")
    if not positions:
        L.append("<pre>N/A (no open positions)</pre>")
    else:
        L.append("<pre>SYMBOL       SIDE  QTY        ENTRY      MARK       LEV  MARGIN   SL   TP   UPNL")
        for p in positions:
            sym = p["symbol"][:12]
            side = p["direction"][:5]
            qty = p.get("qty", "?")[:10]
            entry = p.get("entry_price", "N/A")[:10]
            mark = p.get("mark_price", "N/A")[:10]
            lev = p.get("leverage", "?")[:3]
            margin = p.get("margin_mode", "N/A")[:8]
            sl = "OK" if sltp.get(p["symbol"], {}).get("has_sl") else "MISS"
            tp = "OK" if sltp.get(p["symbol"], {}).get("has_tp") else "--"
            upnl_icon, upnl_str = fmt_pnl(p.get("unrealized_pl"))
            L.append(f"{sym:<12} {side:<5} {qty:<10} {entry:<10} {mark:<10} {lev:<3} {margin:<8} {sl:<4} {tp:<4} {upnl_icon}{upnl_str}</pre>")
            L.append("")
    L.append("")

    # ── Pending Orders ─────────────────────────────────────────────────
    L.append("<b>Orders</b> <code>pending · bitget</code>")
    if not pending_orders:
        L.append("<pre>N/A (no pending orders)</pre>")
    else:
        L.append("<pre>SYMBOL       SIDE  ROLE  QTY        PRICE      STATE")
        for o in pending_orders:
            sym = o["symbol"][:12]
            side = "BUY" if o["role"] == "ENTRY" else "SELL"
            role = o["role"][:5]
            qty = o.get("qty", "?")[:10]
            price = o.get("price", "N/A")[:10]
            state = o.get("state", "?")[:10]
            L.append(f"{sym:<12} {side:<5} {role:<5} {qty:<10} {price:<10} {state}</pre>")
            L.append("")
    L.append("")

    # ── PnL ────────────────────────────────────────────────────────────
    L.append("<b>PNL</b> <code>bitget · realized</code>")
    if pnl and pnl.get("fill_n") != "0":
        total_pnl = Decimal(pnl["total_pnl"])
        fees = Decimal(pnl["total_fees"])
        net = total_pnl - fees
        pnl_icon = "🟢" if net > 0 else "🔴" if net < 0 else "➖"
        L.append(f"<pre>Fills      {pnl['fill_n']}")
        L.append(f"Gross +    {pnl['gross_profit']}")
        L.append(f"Gross -    {pnl['gross_loss']}")
        L.append(f"Fees       {pnl['total_fees']}")
        L.append(f"Net        {pnl_icon}{net:,.6f}".rstrip("0").rstrip("."))
        if positions:
            total_unreal = sum(
                Decimal(str(p.get("unrealized_pl", 0)))
                for p in positions
                if p.get("unrealized_pl") not in ("N/A", None, "")
            )
            L.append(f"Unrealized {total_unreal:,.6f}".rstrip("0").rstrip("."))
        L.append("</pre>")
    else:
        L.append("<pre>N/A (no fills yet)</pre>")
    L.append("")

    # ── Database ───────────────────────────────────────────────────────
    L.append("<b>Database</b>")
    L.append(f"<pre>Messages      {metrics.get('messages', '0')}")
    L.append(f"Signals       {metrics.get('signals', '0')}")
    L.append(f"Open pos      {metrics.get('open_positions', '0')}")
    L.append(f"Pending ord   {metrics.get('pending_orders', '0')}")
    L.append(f"Total orders  {metrics.get('total_orders', '0')}</pre>")
    L.append("")

    # ── Safety ─────────────────────────────────────────────────────────
    L.append(f"<b>Safety</b> <code>{modes.get('mode', '?')} · Bitget {modes.get('venue_mode', '?')} · EXECUTION {modes.get('execution_enabled', '?')}</code>")
    if positions:
        modes_set = set(p.get("margin_mode", "N/A") for p in positions)
        levs_set = set(str(p.get("leverage", "?")) for p in positions)
        iso = "yes" if modes_set == {"isolated"} else "mixed" if "isolated" in modes_set else "no"
        missing_sl = [p["symbol"] for p in positions if not sltp.get(p["symbol"], {}).get("has_sl")]
        sliq = "OK" if not missing_sl else f"MISSING {', '.join(missing_sl)}"
        L.append(f"<pre>Isolated     {iso} [{', '.join(modes_set)}]")
        L.append(f"Leverage     {', '.join(levs_set)}")
        L.append(f"SL-before-liq {sliq}</pre>")
    else:
        L.append("<pre>Isolated     N/A")
        L.append("Leverage     N/A")
        L.append("SL-before-liq N/A (no positions)</pre>")
    L.append("")

    # ── Latest Signal ──────────────────────────────────────────────────
    L.append("<b>Latest Signal</b>")
    if messages:
        m = messages[0]
        msg_id = m.get("msg_id", "?")
        received = _format_timestamp(str(m.get("received_at", "")))
        preview = m.get("preview", "(empty)").replace("\n", " ").strip()
        if len(preview) > 300:
            preview = preview[:297] + "..."
        L.append(f"Referensi <code>#{msg_id}</code>")
        L.append(f"Diterima  <code>{received}</code>")
        L.append(f"<pre>{preview}</pre>")
    else:
        L.append("<i>No source message stored yet.</i>")

    return "\n".join(L)[:4000]


# ── Delivery ─────────────────────────────────────────────────────────────

def send_direct(html: str) -> bool:
    BOT_TOKEN = "8535529687:AAHxftfVgdTjwbiRh1F9QVAYwTWc4ioy0-c"
    CHAT_ID = "5894116684"
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id": CHAT_ID,
            "text": html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("ok", False)
    except Exception as e:
        print(f"Direct send failed: {e}")
        return False


if __name__ == "__main__":
    modes = get_runtime_modes()
    account = get_account()
    positions = load_positions()
    pending = load_pending_orders()
    sltp = load_sltp_status()
    pnl = load_pnl()
    messages = load_latest_messages(1)
    metrics = load_db_metrics()
    codex = get_codex_usage()

    report = format_report(positions, pending, sltp, pnl, messages, metrics, account, modes, codex)
    print(report)
    print("\n---")

    if send_direct(report):
        print("SENT")
    else:
        print("FAILED")
        sys.exit(1)
