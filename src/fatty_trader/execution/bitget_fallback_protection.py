"""Bot-managed TP/SL fallback for symbols that reject native SL/TP (Bitget 43011).

When place-pos-tpsl fails with 43011, instead of emergency-closing, we hold the
position and monitor the mark price ourselves. When the mark price crosses the
intended TP or SL threshold, we submit a market close and reconcile immediately.

Polling interval: every monitor cycle (default 30s). Uses mark price to avoid
false triggers from wicks.
"""

from __future__ import annotations

import json
import logging
import os
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

logger = logging.getLogger(__name__)


# ── DB helpers ────────────────────────────────────────────────────────────


def _psycopg_connect() -> Any:
    """Connect to postgres using psycopg (direct TCP, works inside container)."""
    import psycopg

    return psycopg.connect(
        host=os.environ.get("PGHOST", "postgres"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "fatty_trader"),
        user=os.environ.get("PGUSER", "fatty_app"),
        password=os.environ.get("PGPASSWORD", ""),
    )


def _db_query(sql: str, params: tuple[Any, ...] = ()) -> list[list[Any]]:
    """Query database. Uses psycopg directly."""
    try:
        with _psycopg_connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [list(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"_db_query failed: {type(e).__name__}: {e}")
        return []


def _db_exec(sql: str, params: tuple[Any, ...] = ()) -> None:
    """Execute a parameterized database command."""
    try:
        with _psycopg_connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
    except Exception as e:
        logger.error(f"_db_exec failed: {type(e).__name__}: {e}")


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
    _db_exec(
        """
        INSERT INTO fallback_protection
            (exchange, symbol, direction, entry_price, stop_loss, take_profits, quantity, state)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, 'active')
        """,
        (exchange, symbol, direction, entry_price, stop_loss, tp_json, quantity),
    )
    logger.info(
        "Fallback protection registered: %s %s entry=%s sl=%s tp=%s",
        symbol,
        direction,
        entry_price,
        stop_loss,
        tp_json,
    )
    return f"{exchange}:{symbol}"


def load_active() -> list[dict[str, Any]]:
    """Load fallback entries that still need provider reconciliation."""
    ensure_schema()
    rows = _db_query(
        """
        SELECT id, exchange, symbol, direction, entry_price, stop_loss,
               take_profits, quantity, state, close_order_id
        FROM fallback_protection
        WHERE state IN ('active', 'closing')
        """
    )
    return [
        {
            "id": r[0],
            "exchange": r[1],
            "symbol": r[2],
            "direction": r[3],
            "entry_price": Decimal(r[4]),
            "stop_loss": Decimal(r[5]),
            # psycopg returns JSONB as already-parsed Python list
            "take_profits": [Decimal(t) for t in r[6]]
            if isinstance(r[6], list)
            else [Decimal(t) for t in json.loads(r[6])],
            "quantity": Decimal(r[7]),
            "state": r[8],
            "close_order_id": r[9],
        }
        for r in rows
    ]


def mark_triggered(
    fallback_id: str, close_price: Decimal, reason: str, close_order_id: str | None = None
) -> None:
    """Mark a fallback entry as triggered and closed."""
    _db_exec(
        """
        UPDATE fallback_protection
        SET state = 'triggered', close_price = %s, close_reason = %s,
            close_order_id = %s, updated_at = now()
        WHERE id = %s
        """,
        (close_price, reason, close_order_id, fallback_id),
    )


def mark_closing(fallback_id: str, client_oid: str, order_id: str | None = None) -> None:
    """Persist an acknowledged close before waiting for provider flat read-back."""
    _db_exec(
        """
        UPDATE fallback_protection
        SET state = 'closing', close_reason = 'close-submitted',
            close_order_id = COALESCE(%s, close_order_id), updated_at = now()
        WHERE id = %s
        """,
        (order_id or client_oid, fallback_id),
    )


def mark_position_flat(fallback_id: str) -> None:
    """Position was already flat on Bitget (e.g. manual close)."""
    _db_exec(
        """
        UPDATE fallback_protection
        SET state = 'cancelled', close_reason = 'position-already-flat', updated_at = now()
        WHERE id = %s
        """,
        (fallback_id,),
    )


# ── Price monitoring ────────────────────────────────────────────────────────


def check_thresholds(
    direction: str, mark_price: Decimal, entry: Decimal, sl: Decimal, tps: list[Decimal]
) -> tuple[bool, str]:
    """Check if mark price has hit TP or SL. Returns (should_close, reason)."""
    if direction == "LONG":
        if tps and mark_price >= min(tps):
            return True, "tp_hit"
        if mark_price <= sl:
            return True, "sl_hit"
    elif direction == "SHORT":
        if tps and mark_price <= max(tps):
            return True, "tp_hit"
        if mark_price >= sl:
            return True, "sl_hit"
    return False, ""


# ── Main monitor function ────────────────────────────────────────────────────


def _fallback_client_oid(fallback_id: str) -> str:
    return f"fb-{str(fallback_id).replace('-', '')[:20]}-close"


def _ensure_close_intent(client_oid: str, symbol: str, side: str, quantity: Decimal) -> None:
    intent_id = uuid5(NAMESPACE_URL, f"fatty-fallback-intent:{client_oid}")
    _db_exec(
        """
        INSERT INTO live_order_intents
            (id, exchange, client_order_id, symbol, side, role, state, requested_qty, margin_mode)
        VALUES (%s, 'bitget', %s, %s, %s, 'CLOSE', 'requested', %s, 'ISOLATED')
        ON CONFLICT (exchange, client_order_id) DO NOTHING
        """,
        (intent_id, client_oid, symbol, side, quantity),
    )


def _update_close_intent(
    client_oid: str,
    state: str,
    provider_order_id: str | None = None,
) -> None:
    _db_exec(
        """
        UPDATE live_order_intents
        SET state = %s,
            provider_order_id = COALESCE(%s, provider_order_id),
            updated_at = now()
        WHERE exchange = 'bitget' AND client_order_id = %s
        """,
        (state, provider_order_id, client_oid),
    )


def _open_quantity(value: Any) -> Decimal | None:
    if isinstance(value, list) and not value:
        return Decimal("0")
    rows = value if isinstance(value, list) else [value]
    total = Decimal("0")
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = row.get("total", row.get("size", row.get("quantity", "0")))
        try:
            quantity = abs(Decimal(str(raw or "0")))
        except Exception:
            continue
        found = True
        total += quantity
    return total if found else None


async def run_fallback_monitor_async(client: Any) -> list[dict[str, Any]]:
    """Reconcile fallback protection with provider state before any close POST."""
    try:
        entries = load_active()
    except Exception as exc:
        logger.error("Fallback monitor: failed to load active entries: %s", exc)
        return []

    triggered: list[dict[str, Any]] = []
    for entry in entries:
        symbol = str(entry["symbol"])
        fallback_id = str(entry["id"])
        direction = str(entry["direction"]).upper()
        try:
            positions = await client.get_single_position(symbol)
            open_quantity = _open_quantity(positions)
        except Exception as exc:
            logger.warning("Fallback position read failed for %s: %s", symbol, type(exc).__name__)
            continue
        if open_quantity is None:
            continue
        client_oid = _fallback_client_oid(fallback_id)
        if open_quantity <= 0:
            if entry.get("state") == "closing":
                mark_triggered(
                    fallback_id,
                    entry["entry_price"],
                    "close-confirmed",
                    entry.get("close_order_id") or client_oid,
                )
                _update_close_intent(client_oid, "filled")
            else:
                mark_position_flat(fallback_id)
            continue

        if entry.get("state") == "closing":
            # No second POST: the deterministic intent is reconciled by the next read.
            continue

        try:
            ticker = await client.get_ticker(symbol)
            mark_price = _ticker_mark_price(ticker)
        except Exception as exc:
            logger.warning("Fallback ticker read failed for %s: %s", symbol, type(exc).__name__)
            continue
        should_close, reason = check_thresholds(
            direction,
            mark_price,
            entry["entry_price"],
            entry["stop_loss"],
            entry["take_profits"],
        )
        if not should_close:
            continue

        quantity = min(entry["quantity"], open_quantity)
        side = "SELL" if direction == "LONG" else "BUY"
        _ensure_close_intent(client_oid, symbol, side, quantity)
        try:
            result = await client.place_market_close(
                symbol=symbol,
                side=side,
                quantity=str(quantity),
                client_oid=client_oid,
            )
        except Exception as exc:
            _update_close_intent(client_oid, "unknown")
            logger.error("Fallback close result unknown for %s: %s", symbol, type(exc).__name__)
            continue

        provider_order_id = (
            str(result.get("orderId"))
            if isinstance(result, dict) and result.get("orderId")
            else None
        )
        _update_close_intent(client_oid, "submitted", provider_order_id)
        mark_closing(fallback_id, client_oid, provider_order_id)
        try:
            after_close = await client.get_single_position(symbol)
            flat_after_close = (_open_quantity(after_close) or Decimal("0")) <= 0
        except Exception:
            flat_after_close = False
        if flat_after_close:
            mark_triggered(fallback_id, mark_price, reason, provider_order_id or client_oid)
            _update_close_intent(client_oid, "filled", provider_order_id)
            triggered.append(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "reason": reason,
                    "mark_price": str(mark_price),
                    "close_oid": client_oid,
                    "order_id": provider_order_id,
                }
            )
    return triggered


def _ticker_mark_price(value: Any) -> Decimal:
    rows = value if isinstance(value, list) else [value]
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = row.get("markPrice", row.get("lastPr"))
        if raw is not None:
            price = Decimal(str(raw))
            if price > 0:
                return price
    raise ValueError("Bitget ticker has no positive mark price")


def run_fallback_monitor() -> list[dict[str, Any]]:
    """Do not run fallback closes without the authenticated async client boundary."""
    logger.error("Synchronous fallback monitor disabled; use run_fallback_monitor_async")
    return []


def reconcile_close(symbol: str, client_oid: str, order_id: str) -> None:
    """Reconcile a provider-acknowledged close intent."""
    del symbol
    _db_exec(
        """
        UPDATE live_order_intents
        SET state = 'filled',
            filled_qty = requested_qty,
            provider_order_id = %s,
            updated_at = now()
        WHERE exchange = 'bitget' AND client_order_id = %s
        """,
        (order_id, client_oid),
    )
