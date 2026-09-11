#!/usr/bin/env python3
"""Periodic health report for Fatty Bitget.

Rich Telegram HTML report modeled after the original telegram_health_report.sh format.
Queries actual Bitget LIVE data from DB tables + Bitget API.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

import psycopg
from fatty_trader.exchanges.bitget.client import BitgetRestClient


def db():
    return psycopg.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "fatty_trader"),
        user=os.environ.get("PGUSER", "fatty_app"),
        password=os.environ.get("PGPASSWORD", ""),
    )


def query_one(sql: str):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [desc[0] for desc in cur.description]
            row = cur.fetchone()
            return dict(zip(cols, row)) if row else {col: None for col in cols}


def query_all(sql: str):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [desc[0] for desc in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Runtime ──────────────────────────────────────────────────────────────

def get_runtime_modes() -> dict:
    return {
        "mode": os.environ.get("TRADER_MODE", "UNKNOWN"),
        "venue_mode": os.environ.get("BITGET_MODE", "UNKNOWN"),
        "execution_enabled": os.environ.get("BITGET_EXECUTION_ENABLED", "UNKNOWN"),
    }


def get_bitget_client():
    required = ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE")
    if any(not os.environ.get(k, "").strip() for k in required):
        return None
    return BitgetRestClient(
        api_key=os.environ["BITGET_API_KEY"],
        api_secret=os.environ["BITGET_API_SECRET"],
        passphrase=os.environ["BITGET_API_PASSPHRASE"],
        mode="LIVE",
    )


def get_current_price(symbol: str) -> Decimal | None:
    try:
        url = f"https://api.bitget.com/api/v2/mix/market/ticker?symbol={symbol}&productType=USDT-FUTURES"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return Decimal(data["data"][0]["lastPr"])
    except Exception:
        return None


def get_account() -> dict:
    client = get_bitget_client()
    if not client:
        return {}
    try:
        async def read():
            acct = await client.get_account("BTCUSDT")
            return {
                "equity": acct.get("accountEquity", "N/A"),
                "available": acct.get("available", "N/A"),
                "unrealized_pl": acct.get("unrealizedPL", "N/A"),
            }
        return asyncio.run(read())
    except Exception:
        return {}


# ── Data ─────────────────────────────────────────────────────────────────

def load_positions() -> list[dict]:
    """Load active positions enriched with live Bitget data."""
    rows = query_all("""
        SELECT
            p.exchange, p.symbol, p.direction,
            p.quantity::text AS qty,
            p.protection_state,
            p.opened_at
        FROM positions p
        WHERE p.closed_at IS NULL
        ORDER BY p.opened_at DESC
    """)
    client = get_bitget_client()
    if not client or not rows:
        return rows

    async def enrich():
        results = []
        for pos in rows:
            symbol = pos["symbol"]
            try:
                detail = await client.get_single_position(symbol)
                price = get_current_price(symbol)
                pos["current_price"] = fmt_price(price) if price else "N/A"
                if detail and isinstance(detail, list) and len(detail) > 0:
                    row = detail[0]
                    pos["entry_price"] = fmt_price(row.get("openPriceAvg"))
                    pos["mark_price"] = fmt_price(row.get("markPrice"))
                    pos["unrealized_pl"] = row.get("unrealizedPL", "N/A")
                    pos["leverage"] = row.get("leverage", "?")
                    pos["margin_mode"] = row.get("margin_mode", "N/A")
                    pos["liquidation_price"] = fmt_price(row.get("liquidationPrice"))
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
            results.append(pos)
        return results

    try:
        return asyncio.run(enrich())
    except Exception:
        return rows


def load_pending_orders() -> list[dict]:
    """Load pending entry orders (not backed by active position)."""
    return query_all("""
        SELECT
            li.exchange, li.symbol, li.role, li.state,
            li.requested_qty::text AS qty,
            li.requested_price::text AS price,
            li.filled_qty::text AS filled
        FROM live_order_intents li
        WHERE li.state NOT IN ('filled', 'rejected', 'cancelled', 'reconciled')
          AND li.role = 'ENTRY'
        ORDER BY li.created_at DESC
    """)


def load_sltp_status() -> dict:
    """Check which symbols have SL/TP orders placed."""
    rows = query_all("""
        SELECT
            symbol,
            bool_or(role = 'SL' AND state IN ('requested', 'acknowledged', 'submitted')) AS has_sl,
            bool_or(role = 'TP' AND state IN ('requested', 'acknowledged', 'submitted')) AS has_tp
        FROM live_order_intents
        WHERE exchange = 'bitget'
        GROUP BY symbol
    """)
    return {r["symbol"]: r for r in rows}


def load_pnl() -> dict:
    """Load realized P&L from fills."""
    return query_one("""
        SELECT
            count(*)::text AS fill_n,
            coalesce(sum(realized_pnl), 0)::text AS total_pnl,
            coalesce(sum(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 0)::text AS gross_profit,
            coalesce(sum(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END), 0)::text AS gross_loss,
            coalesce(sum(fee), 0)::text AS total_fees
        FROM fills WHERE exchange = 'bitget'
    """)


def load_latest_messages(n: int = 1) -> list[dict]:
    return query_all(f"""
        SELECT
            message_id::text AS msg_id,
            received_at,
            LEFT(raw_text, 500) AS preview
        FROM telegram_messages
        ORDER BY received_at DESC, message_id DESC
        LIMIT {n}
    """)


def load_db_metrics() -> dict:
    return query_one("""
        SELECT
            (SELECT count(*)::text FROM telegram_messages) AS messages,
            (SELECT count(*)::text FROM canonical_signals) AS signals,
            (SELECT count(*)::text FROM positions WHERE closed_at IS NULL) AS open_positions,
            (SELECT count(*)::text FROM orders WHERE state NOT IN ('FILLED','CANCELLED','REJECTED','CLOSED')) AS pending_orders,
            (SELECT count(*)::text FROM orders) AS total_orders
    """)


# ── Helpers ──────────────────────────────────────────────────────────────

def fmt_price(val) -> str:
    if val is None or val == "N/A":
        return "N/A"
    try:
        d = Decimal(str(val))
        s = f"{d:.6f}".rstrip("0").rstrip(".")
        return s
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


# ── Codex Usage ──────────────────────────────────────────────────────────

def get_codex_usage() -> dict:
    """Read cached Codex usage data."""
    cache_path = Path.home() / ".cache" / "fatty" / "codex_usage.json"
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        refreshed = data.get("refreshed", "unknown")
        return {
            "status": "LIVE",
            "plan": data.get("plan", "N/A"),
            "5h": data.get("5h", "N/A"),
            "7d": data.get("7d", "N/A"),
            "reset": data.get("reset", "N/A"),
            "refreshed": refreshed,
        }
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
            L.append("")  # spacing between positions
    L.append("")

    # ── Pending Orders ─────────────────────────────────────────────────
    L.append("<b>Orders</b> <code>pending · bitget</code>")
    if not pending_orders:
        L.append("<pre>N/A (no pending orders)</pre>")
    else:
        L.append("<pre>SYMBOL       SIDE  ROLE  QTY        PRICE      STATE")
        for o in pending_orders:
            sym = o["symbol"][:12]
            side = "BUY" if o["role"] == "ENTRY" and o.get("state") != "filled" else "SELL"
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


def _format_timestamp(iso_string: str) -> str:
    if not iso_string:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_string.replace(" ", "T").replace("+00", "+00:00"))
        jakarta_tz = timezone(timedelta(hours=7))
        return dt.astimezone(jakarta_tz).strftime("%d %b %Y, %H:%M WIB")
    except Exception:
        return str(iso_string)


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
