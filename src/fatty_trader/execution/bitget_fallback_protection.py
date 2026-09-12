"""Bot-managed TP/SL fallback for symbols that reject native SL/TP (Bitget 43011).

When place-pos-tpsl fails with 43011, instead of emergency-closing, we hold the
position and monitor the mark price ourselves. When the mark price crosses the
intended TP or SL threshold, we submit a market close and reconcile immediately.

Polling interval: every monitor cycle (default 30s). Uses mark price to avoid
false triggers from wicks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ── DB helpers via docker exec ────────────────────────────────────────────

def _db_query(sql: str) -> list[list[str]]:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "fatty_app",
         "-d", "fatty_trader", "-At", "-F", "|", "-c", sql],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        return []
    return [line.split("|") for line in result.stdout.strip().split("\n") if line]


def _db_exec(sql: str) -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "fatty_app",
         "-d", "fatty_trader", "-c", sql],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )


# ── Schema ─────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fallback_protection (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    exchange text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    entry_price numeric NOT NULL,
    stop_loss numeric NOT NULL,
    take_profits jsonb NOT NULL DEFAULT '[]'::jsonb,
    quantity numeric NOT NULL,
    state text NOT NULL DEFAULT 'active',
    close_price numeric,
    close_reason text,
    close_order_id text,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fallback_protection_active
    ON fallback_protection (exchange, symbol) WHERE state = 'active';
"""


def ensure_schema() -> None:
    """Idempotent schema creation."""
    _db_exec(_SCHEMA_SQL)


# ── State management ────────────────────────────────────────────────────────

def register_fallback(
    exchange: str,
    symbol: str,
    direction: str,
    entry_price: Decimal,
    stop_loss: Decimal,
    take_profits: list[Decimal],
    quantity: Decimal,
) -> str:
    """Register a position for fallback TP/SL monitoring."""
    ensure_schema()
    tp_json = json.dumps([str(t) for t in take_profits])
    _db_exec(f"""
        INSERT INTO fallback_protection (exchange, symbol, direction, entry_price, stop_loss, take_profits, quantity, state)
        VALUES ('{exchange}', '{symbol}', '{direction}', {entry_price}, {stop_loss}, '{tp_json}'::jsonb, {quantity}, 'active')
    """)
    logger.info(f"Fallback protection registered: {symbol} {direction} entry={entry_price} sl={stop_loss} tp={tp_json}")
    return f"{exchange}:{symbol}"


def load_active() -> list[dict]:
    """Load all active fallback protection entries."""
    ensure_schema()
    rows = _db_query("SELECT id, exchange, symbol, direction, entry_price, stop_loss, take_profits, quantity FROM fallback_protection WHERE state = 'active'")
    return [
        {
            "id": r[0],
            "exchange": r[1],
            "symbol": r[2],
            "direction": r[3],
            "entry_price": Decimal(r[4]),
            "stop_loss": Decimal(r[5]),
            "take_profits": [Decimal(t) for t in json.loads(r[6])],
            "quantity": Decimal(r[7]),
        }
        for r in rows
    ]


def mark_triggered(fallback_id: str, close_price: Decimal, reason: str, close_order_id: str | None = None) -> None:
    """Mark a fallback entry as triggered and closed."""
    _db_exec(f"""
        UPDATE fallback_protection
        SET state = 'triggered', close_price = {close_price}, close_reason = '{reason}',
            close_order_id = '{close_order_id or ''}', updated_at = now()
        WHERE id = '{fallback_id}'
    """)


def mark_position_flat(fallback_id: str) -> None:
    """Position was already flat on Bitget (e.g. manual close)."""
    _db_exec(f"""
        UPDATE fallback_protection
        SET state = 'cancelled', close_reason = 'position-already-flat', updated_at = now()
        WHERE id = '{fallback_id}'
    """)


# ── Price monitoring ────────────────────────────────────────────────────────

def get_mark_price(symbol: str) -> Decimal | None:
    """Read mark price from Bitget ticker."""
    try:
        import urllib.request
        url = f"https://api.bitget.com/api/v2/mix/market/ticker?symbol={symbol}&productType=USDT-FUTURES"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return Decimal(data["data"][0]["markPrice"])
    except Exception:
        return None


def check_thresholds(direction: str, mark_price: Decimal, entry: Decimal, sl: Decimal, tps: list[Decimal]) -> tuple[bool, str]:
    """Check if mark price has hit TP or SL. Returns (should_close, reason)."""
    if direction == "LONG":
        # For longs: TP is above entry, SL is below
        if tps and mark_price >= min(tps):  # hit nearest TP
            return True, "tp_hit"
        if mark_price <= sl:
            return True, "sl_hit"
    elif direction == "SHORT":
        # For shorts: TP is below entry, SL is above
        if tps and mark_price <= max(tps):  # hit nearest TP
            return True, "tp_hit"
        if mark_price >= sl:
            return True, "sl_hit"
    return False, ""


# ── Close submission ─────────────────────────────────────────────────────────

def submit_close(exchange: str, symbol: str, side: str, quantity: Decimal, client_oid: str) -> dict | None:
    """Submit a market close order via Bitget API."""
    try:
        import urllib.request
        import hmac
        import hashlib
        import base64
        import time

        api_key = os.environ.get("BITGET_API_KEY", "")
        api_secret = os.environ.get("BITGET_API_SECRET", "")
        passphrase = os.environ.get("BITGET_API_PASSPHRASE", "")

        base_url = "https://api.bitget.com"
        path = "/api/v2/mix/order/place-order"
        method = "POST"

        payload = json.dumps({
            "symbol": symbol.upper(),
            "productType": "USDT-FUTURES",
            "marginMode": "isolated",
            "marginCoin": "USDT",
            "size": str(quantity),
            "side": side.lower(),
            "orderType": "market",
            "reduceOnly": "YES",
            "clientOid": client_oid,
        }, separators=(",", ":"))

        timestamp = str(int(time.time() * 1000))
        prehash = timestamp + method + path + payload
        signature = base64.b64encode(
            hmac.new(api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
        ).decode()

        headers = {
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }

        req = urllib.request.Request(base_url + path, data=payload.encode(), headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.error(f"Fallback close failed for {symbol}: {e}")
        return None


def submit_close_via_container(exchange: str, symbol: str, direction: str, quantity: Decimal) -> str | None:
    """Submit market close via the authenticated container client."""
    side = "SELL" if direction == "LONG" else "BUY"
    client_oid = f"fallback-close-{symbol}-{int(datetime.now(timezone.utc).timestamp())}"
    raw = subprocess.run(
        ["docker", "compose", "exec", "-T", "dispatcher-bitget", "sh", "-lc",
         f'cd /app && . .venv/bin/activate && python3 -c "import os,asyncio,json; '
         f'from fatty_trader.exchanges.bitget.client import BitgetRestClient; '
         f'async def r(): '
         f'c=BitgetRestClient(os.environ[\\"BITGET_API_KEY\\"],os.environ[\\"BITGET_API_SECRET\\"],os.environ[\\"BITGET_API_PASSPHRASE\\"],mode=\\"LIVE\\"); '
         f'result=await c.place_market_close(symbol=\'{symbol}\',side=\'{side}\',quantity=\'{quantity}\',client_oid=\'{client_oid}\'); '
         f'print(json.dumps(result))" 2>/dev/null'],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    if raw.returncode != 0:
        return None
    try:
        result = json.loads(raw.stdout.strip())
        return result.get("orderId", "")
    except Exception:
        return None


# ── Main monitor function ────────────────────────────────────────────────────

def run_fallback_monitor() -> list[dict]:
    """Run one cycle of fallback TP/SL monitoring. Returns list of triggered entries."""
    triggered = []
    entries = load_active()

    for entry in entries:
        symbol = entry["symbol"]
        fallback_id = entry["id"]
        direction = entry["direction"]
        entry_price = entry["entry_price"]
        sl = entry["stop_loss"]
        tps = entry["take_profits"]
        quantity = entry["quantity"]

        # Check if position still open on Bitget
        pos_raw = subprocess.run(
            ["docker", "compose", "exec", "-T", "dispatcher-bitget", "sh", "-lc",
             f'cd /app && . .venv/bin/activate && python3 -c "import os,asyncio,json; '
             f'from fatty_trader.exchanges.bitget.client import BitgetRestClient; '
             f'async def r(): '
             f'c=BitgetRestClient(os.environ[\\"BITGET_API_KEY\\"],os.environ[\\"BITGET_API_SECRET\\"],os.environ[\\"BITGET_API_PASSPHRASE\\"],mode=\\"LIVE\\"); '
             f'p=await c.get_single_position(\\"{symbol}\\"); print(json.dumps(p))" 2>/dev/null'],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        )

        if pos_raw.returncode != 0:
            continue

        try:
            pos_data = json.loads(pos_raw.stdout.strip())
        except Exception:
            continue

        # Check if position is flat
        if not pos_data or (isinstance(pos_data, list) and len(pos_data) == 0):
            logger.info(f"Fallback: {symbol} position already flat, marking cancelled")
            mark_position_flat(fallback_id)
            continue

        # Get mark price
        mark_price = get_mark_price(symbol)
        if mark_price is None:
            continue

        should_close, reason = check_thresholds(direction, mark_price, entry_price, sl, tps)

        if should_close:
            logger.warning(f"Fallback triggered: {symbol} {direction} mark={mark_price} reason={reason}")

            # Submit close
            close_oid = f"fallback-{reason}-{symbol}-{int(datetime.now(timezone.utc).timestamp())}"
            result = submit_close("bitget", symbol, "SELL" if direction == "LONG" else "BUY", quantity, close_oid)

            order_id = None
            if result and isinstance(result, dict):
                order_id = result.get("orderId")

            mark_triggered(fallback_id, mark_price, reason, close_oid)
            triggered.append({
                "symbol": symbol,
                "direction": direction,
                "reason": reason,
                "mark_price": str(mark_price),
                "close_oid": close_oid,
                "order_id": order_id,
            })

            # Reconcile immediately
            if order_id:
                reconcile_close(symbol, close_oid, order_id)

    return triggered


def reconcile_close(symbol: str, client_oid: str, order_id: str) -> None:
    """Reconcile the close intent to filled after submission."""
    _db_exec(f"""
        UPDATE live_order_intents
        SET state = 'filled',
            filled_qty = requested_qty,
            provider_order_id = '{order_id}',
            updated_at = now()
        WHERE client_oid = '{client_oid}'
    """)
