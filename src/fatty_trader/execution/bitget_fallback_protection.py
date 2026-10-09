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

from fatty_trader.storage.fallback_schema import FALLBACK_OWNERSHIP_SCHEMA_SQL

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
    except Exception as exc:
        logger.error(f"_db_query failed: {type(exc).__name__}: {exc}")
        raise


def _db_exec(sql: str, params: tuple[Any, ...] = ()) -> None:
    """Execute a parameterized database command."""
    try:
        with _psycopg_connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
    except Exception as exc:
        logger.error(f"_db_exec failed: {type(exc).__name__}: {exc}")
        raise


# ── Schema ─────────────────────────────────────────────────────────────────


_SCHEMA_SQL = FALLBACK_OWNERSHIP_SCHEMA_SQL


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
    position_key: str | None = None,
    *,
    provider_position_epoch: str | None = None,
    environment: str | None = None,
) -> str:
    """Persist proven identity; legacy registrations remain close-ineligible.

    The caller must obtain the epoch from the canonical provider position after
    reconciling its entry intent. Never synthesize it from a local timestamp.
    """
    if provider_position_epoch is not None or environment is not None:
        import re

        if (
            not isinstance(provider_position_epoch, str)
            or re.fullmatch(r"[1-9][0-9]*", provider_position_epoch) is None
            or environment not in {"DEMO", "LIVE"}
            or not position_key
            or direction not in {"LONG", "SHORT"}
        ):
            raise ValueError(
                "fallback identity must include canonical epoch, environment and entry key"
            )
    ensure_schema()
    tp_json = json.dumps([str(t) for t in take_profits])
    with _psycopg_connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO fallback_protection
                (exchange, symbol, direction, entry_price, stop_loss,
                 take_profits, quantity, position_key, provider_position_epoch, environment, state)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, 'active')
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (
                exchange,
                symbol,
                direction,
                entry_price,
                stop_loss,
                tp_json,
                quantity,
                position_key,
                provider_position_epoch,
                environment,
            ),
        )
        if cur.fetchone() is None:
            cur.execute(
                """SELECT exchange, symbol, direction, provider_position_epoch, environment,
                          quantity, entry_price, stop_loss, take_profits
                   FROM fallback_protection WHERE position_key = %s
                   AND state IN ('active', 'closing') FOR UPDATE""",
                (position_key,),
            )
            existing = cur.fetchone()
            expected = (
                exchange,
                symbol,
                direction,
                provider_position_epoch,
                environment,
                quantity,
                entry_price,
                stop_loss,
                json.loads(tp_json),
            )
            if existing is None or tuple(existing) != expected:
                raise ValueError("fallback registration identity or protection conflict")
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
               take_profits, quantity, state, close_order_id, position_key,
               provider_position_epoch, environment
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
            "position_key": r[10],
            "provider_position_epoch": r[11],
            "environment": r[12],
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


def _ensure_close_intent(client_oid: str, symbol: str, side: str, quantity: Decimal) -> bool:
    """Claim and transition the owning fallback in one transaction before POST.

    Legacy split claims are adopted as closing, never granted a second POST.
    The row lock serializes REST/stream contenders. A conflicting/colliding OID
    cannot select a different fallback or change its lifecycle.
    """
    intent_id = uuid5(NAMESPACE_URL, f"fatty-fallback-intent:{client_oid}")
    with _psycopg_connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, state FROM fallback_protection
               WHERE exchange = 'bitget' AND symbol = %s
                 AND direction = %s AND quantity >= %s AND quantity > 0
                 AND ('fb-' || left(replace(id::text, '-', ''), 20) || '-close') = %s
               FOR UPDATE""",
            (symbol, "LONG" if side == "SELL" else "SHORT", quantity, client_oid),
        )
        owners = cur.fetchall()
        if len(owners) != 1 or owners[0][1] != "active":
            return False
        fallback_id = owners[0][0]
        cur.execute(
            (
                "INSERT INTO live_order_intents\n"
                "                 (id, exchange, client_order_id, symbol, side, role, "
                "state, requested_qty, margin_mode)\n"
                "               VALUES (%s, 'bitget', %s, %s, %s, 'CLOSE', "
                "'requested', %s, 'ISOLATED')\n"
                "               ON CONFLICT (exchange, client_order_id) DO NOTHING\n"
                "               RETURNING client_order_id"
            ),
            (intent_id, client_oid, symbol, side, quantity),
        )
        claimed = cur.fetchone() is not None
        if not claimed:
            cur.execute(
                """SELECT client_order_id FROM live_order_intents
                   WHERE exchange = 'bitget' AND client_order_id = %s AND symbol = %s
                     AND side = %s AND role = 'CLOSE' AND requested_qty = %s""",
                (client_oid, symbol, side, quantity),
            )
            if cur.fetchone() is None:
                return False
        cur.execute(
            """UPDATE fallback_protection SET state = 'closing',
                 close_reason = 'close-claimed', close_order_id = %s, updated_at = now()
               WHERE id = %s""",
            (client_oid, fallback_id),
        )
        return claimed


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
    """Only a wholly valid snapshot can establish exposure (including flat)."""
    if isinstance(value, dict):
        if "code" in value and value["code"] != "00000":
            return None
        if "data" in value and "positionList" in value:
            return None
        value = value.get("data", value.get("positionList"))
    if not isinstance(value, list):
        return None
    total = Decimal("0")
    for row in value:
        if not isinstance(row, dict):
            return None
        raw_values = [row[field] for field in ("total", "size", "quantity") if field in row]
        if not raw_values:
            return None
        try:
            quantities = [Decimal(str(raw)) for raw in raw_values]
        except (ArithmeticError, TypeError, ValueError):
            return None
        if any(not qty.is_finite() or qty < 0 for qty in quantities):
            return None
        if any(qty != quantities[0] for qty in quantities):
            return None
        total += quantities[0]
    return total


def _owned_quantity(value: Any, entry: dict[str, Any]) -> Decimal | None:
    """Never attribute a symbol's aggregate exposure to a legacy fallback row.

    A durable entry key alone cannot distinguish a same-side replacement.
    Legacy rows remain mutation-ineligible: never infer the provider epoch or
    environment from entry price, local created_at, fill times or process defaults.
    """
    if (
        not entry.get("position_key")
        or not entry.get("provider_position_epoch")
        or entry.get("environment") not in {"DEMO", "LIVE"}
    ):
        logger.warning("Fallback ownership unavailable for %s; close blocked", entry["id"])
        return None
    rows = value.get("data", value.get("positionList")) if isinstance(value, dict) else value
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    direction = entry.get("direction")
    if direction not in {"LONG", "SHORT"}:
        return None
    if (
        row.get("symbol") != entry["symbol"]
        or row.get("holdSide") != ("long" if direction == "LONG" else "short")
        or str(row.get("cTime")) != str(entry["provider_position_epoch"])
        or row.get("posMode") != "one_way_mode"
        or row.get("marginMode") != "isolated"
    ):
        return None
    quantity = _open_quantity(rows)
    expected = Decimal(str(entry["quantity"]))
    if quantity is None or not expected.is_finite() or expected <= 0 or quantity != expected:
        return None
    owners = _db_query(
        (
            "SELECT client_order_id FROM live_order_intents\n"
            "           WHERE exchange = 'bitget' AND client_order_id = %s AND "
            "symbol = %s\n"
            "             AND side = %s AND role = 'ENTRY' AND state IN ('filled', "
            "'partially_filled', 'reconciled')\n"
            "             AND filled_qty = %s"
        ),
        (
            entry["position_key"],
            entry["symbol"],
            "BUY" if direction == "LONG" else "SELL",
            quantity,
        ),
    )
    return quantity if len(owners) == 1 else None


async def submit_fallback_close_async(
    client: Any,
    entry: dict[str, Any],
    *,
    mark_price: Decimal,
    reason: str,
    environment: str | None = None,
) -> dict[str, Any]:
    """Submit one idempotent reduce-only fallback close and reconcile it."""
    symbol = str(entry["symbol"])
    fallback_id = str(entry["id"])
    direction = str(entry["direction"]).upper()
    client_oid = _fallback_client_oid(fallback_id)
    if environment not in {"DEMO", "LIVE"} or entry.get("environment") != environment:
        return {
            "submitted": False,
            "reason": "position-environment-unverified",
            "client_oid": client_oid,
        }
    try:
        positions = await client.get_single_position(symbol)
        open_quantity = _open_quantity(positions)
    except Exception:
        return {
            "submitted": False,
            "reason": "provider-position-read-failed",
            "client_oid": client_oid,
        }
    if open_quantity is None:
        return {"submitted": False, "reason": "provider-position-invalid", "client_oid": client_oid}
    if entry.get("state") == "closing":
        confirmed = await _reconcile_close_evidence(client, entry, flat=open_quantity == 0)
        return {
            "submitted": False,
            "reason": "close-confirmed" if confirmed else "close-fill-evidence-pending",
            "client_oid": client_oid,
        }
    if open_quantity <= 0:
        mark_position_flat(fallback_id)
        return {"submitted": False, "reason": "position-already-flat", "client_oid": client_oid}

    owned_quantity = _owned_quantity(positions, entry)
    if owned_quantity is None:
        return {
            "submitted": False,
            "reason": "position-ownership-unverified",
            "client_oid": client_oid,
        }
    requested_quantity = Decimal(str(entry["quantity"]))
    quantity = min(requested_quantity, owned_quantity)
    if quantity <= 0:
        return {"submitted": False, "reason": "close-quantity-invalid", "client_oid": client_oid}
    side = "SELL" if direction == "LONG" else "BUY"
    claimed = _ensure_close_intent(client_oid, symbol, side, quantity)
    if claimed is False:
        return {"submitted": False, "reason": "close-already-claimed", "client_oid": client_oid}
    mark_closing(fallback_id, client_oid)
    try:
        result = await client.place_market_close(
            symbol=symbol,
            side=side,
            quantity=str(quantity),
            client_oid=client_oid,
        )
    except Exception:
        _update_close_intent(client_oid, "unknown")
        return {"submitted": False, "reason": "close-result-unknown", "client_oid": client_oid}
    if not isinstance(result, dict):
        _update_close_intent(client_oid, "unknown")
        return {
            "submitted": False,
            "reason": "close-acknowledgement-invalid",
            "client_oid": client_oid,
        }
    provider_order_id = str(result.get("orderId")) if result.get("orderId") is not None else None
    _update_close_intent(client_oid, "submitted", provider_order_id)
    mark_closing(fallback_id, client_oid, provider_order_id)

    try:
        after_close = await client.get_single_position(symbol)
        remaining_quantity = _open_quantity(after_close)
    except Exception:
        remaining_quantity = None
    if remaining_quantity is None:
        _update_close_intent(client_oid, "unknown", provider_order_id)
        return {
            "submitted": True,
            "reason": "close-readback-unknown",
            "client_oid": client_oid,
            "order_id": provider_order_id,
        }
    if remaining_quantity > 0:
        _update_close_intent(client_oid, "submitted", provider_order_id)
        return {
            "submitted": True,
            "reason": "submitted",
            "client_oid": client_oid,
            "order_id": provider_order_id,
        }
    confirmed = await _reconcile_close_evidence(client, entry, flat=True)
    return {
        "submitted": True,
        "reason": "close-confirmed" if confirmed else "close-fill-evidence-pending",
        "client_oid": client_oid,
        "order_id": provider_order_id,
    }


async def _reconcile_close_evidence(client: Any, entry: dict[str, Any], *, flat: bool) -> bool:
    """Recover only explicitly matched provider fills; never retry a close POST."""
    client_oid = _fallback_client_oid(str(entry["id"]))
    try:
        value = await client.get_fills(str(entry["symbol"]))
    except Exception:
        _update_close_intent(client_oid, "unknown")
        return False
    if isinstance(value, dict):
        if "code" in value and value["code"] != "00000":
            return False
        value = value.get("data", value)
        if isinstance(value, dict):
            value = value.get("fillList")
    if not isinstance(value, list):
        return False
    return _persist_close_fills(entry, value, flat=flat)


def _persist_close_fills(entry: dict[str, Any], rows: list[Any], *, flat: bool) -> bool:
    """Commit real ledger, intent accounting and lifecycle together under row locks."""
    from fatty_trader.exchanges.bitget.live import LiveIntentRecord, normalize_fill
    from fatty_trader.storage.live_intents import insert_provider_fills

    client_oid = _fallback_client_oid(str(entry["id"]))
    with _psycopg_connect() as conn, conn.cursor() as cur:
        cur.execute(
            (
                "SELECT symbol, direction, state FROM fallback_protection WHERE id = "
                "%s AND exchange = 'bitget' FOR UPDATE"
            ),
            (entry["id"],),
        )
        owner = cur.fetchone()
        if (
            owner is None
            or owner[0] != entry["symbol"]
            or owner[2] not in {"active", "closing", "triggered"}
        ):
            return False
        cur.execute(
            """SELECT symbol, side, requested_qty, provider_order_id FROM live_order_intents
               WHERE exchange = 'bitget' AND client_order_id = %s AND role = 'CLOSE' FOR UPDATE""",
            (client_oid,),
        )
        intent = cur.fetchone()
        if (
            intent is None
            or intent[0] != owner[0]
            or intent[1] != ("SELL" if owner[1] == "LONG" else "BUY")
        ):
            return False
        symbol, side, requested, provider_id = intent
        fills = []
        fingerprints: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            try:
                # Validate source evidence BEFORE the shared normalizer's zero defaults.
                fee_detail = raw.get("feeDetail")
                if isinstance(fee_detail, str):
                    fee_detail = json.loads(fee_detail)
                if fee_detail is not None:
                    details = [fee_detail] if isinstance(fee_detail, dict) else fee_detail
                    if (
                        not isinstance(details, list)
                        or not details
                        or any(not isinstance(d, dict) for d in details)
                    ):
                        continue
                    fees = [
                        Decimal(str(d.get("totalFee", d.get("fee", d.get("feeAmount")))))
                        for d in details
                    ]
                else:
                    fees = [Decimal(str(raw.get("fee", raw.get("fillFee", raw.get("feeAmount")))))]
                if any(not fee.is_finite() for fee in fees):
                    continue
            except (ArithmeticError, TypeError, ValueError):
                continue
            fill = normalize_fill(raw)
            fill_oid, order_id = fill.get("clientOid"), fill.get("orderId")
            # Both identifiers, when supplied, must agree. Symbol/side are not optional.
            if not (
                fill_oid == client_oid or (provider_id is not None and str(order_id) == provider_id)
            ):
                continue
            if fill_oid is not None and fill_oid != client_oid:
                continue
            if order_id is None or (provider_id is not None and str(order_id) != provider_id):
                continue
            if fill.get("symbol") != symbol or str(fill.get("side")).upper() != side:
                continue
            if fill.get("tradeSide") not in {None, "close"}:
                continue
            fill_id = fill.get("fillId", fill.get("tradeId", fill.get("id")))
            try:
                qty = Decimal(
                    str(
                        fill.get(
                            "quantity",
                            fill.get("size", fill.get("fillQty", fill.get("baseVolume"))),
                        )
                    )
                )
                price = Decimal(str(fill.get("price", fill.get("fillPrice", fill.get("priceAvg")))))
                fee = Decimal(str(fill.get("fee", "0") or "0"))
                timestamp = Decimal(str(fill.get("cTime", fill.get("uTime"))))
                if (
                    not all(v.is_finite() for v in (qty, price, fee, timestamp))
                    or min(qty, price, timestamp) <= 0
                ):
                    continue
            except (ArithmeticError, TypeError, ValueError):
                continue
            if (
                fill_id is None
                or not str(fill_id).strip()
                or str(fill_id).startswith("status-derived:")
            ):
                continue
            fingerprint = (qty, price, abs(fee))
            key = str(fill_id)
            if key in fingerprints and fingerprints[key] != fingerprint:
                raise ValueError("fallback duplicate fill contradicts provider evidence")
            fingerprints[key] = fingerprint
            cur.execute(
                (
                    "SELECT client_order_id, quantity, price, fee FROM fills WHERE "
                    "exchange = 'bitget' AND provider_fill_id = %s"
                ),
                (key,),
            )
            existing = cur.fetchone()
            if existing is not None and existing[0] != client_oid:
                raise ValueError("fallback fill belongs to another intent")
            if existing is not None and tuple(existing[1:]) != fingerprint:
                raise ValueError("fallback duplicate fill contradicts durable evidence")
            if provider_id is None:
                provider_id = str(order_id)
            fills.append(fill)
        record = LiveIntentRecord(
            exchange="bitget",
            client_oid=client_oid,
            symbol=symbol,
            side=side,
            role="CLOSE",
            state="unknown",
            requested_qty=requested,
        )
        insert_provider_fills(cur, record, tuple(fills))
        cur.execute(
            """SELECT provider_fill_id, quantity, price, fee FROM fills
               WHERE exchange = 'bitget' AND client_order_id = %s ORDER BY provider_fill_id""",
            (client_oid,),
        )
        ledger = cur.fetchall()
        if not ledger:
            return False
        if any(str(row[0]).startswith("status-derived:") for row in ledger):
            raise ValueError("legacy synthetic fallback ledger requires evidence repair")
        quantity = sum((row[1] for row in ledger), Decimal("0"))
        if quantity <= 0 or quantity > requested:
            raise ValueError("fallback close fill quantity contradicts intent")
        price = sum((row[1] * row[2] for row in ledger), Decimal("0")) / quantity
        fee = sum((row[3] for row in ledger), Decimal("0"))
        state = "filled" if quantity == requested else "partially_filled"
        cur.execute(
            """UPDATE live_order_intents SET state = %s, provider_order_id = %s,
                 filled_qty = %s, filled_price = %s, fee = %s, provider_fill_ids = %s::jsonb,
                 updated_at = now() WHERE exchange = 'bitget' AND client_order_id = %s""",
            (
                state,
                provider_id,
                quantity,
                price,
                fee,
                json.dumps([row[0] for row in ledger]),
                client_oid,
            ),
        )
        confirmed = state == "filled" and flat
        cur.execute(
            """UPDATE fallback_protection SET state = %s, close_reason = %s,
                 close_price = %s, close_order_id = %s, updated_at = now() WHERE id = %s""",
            (
                "triggered" if confirmed else "closing",
                "close-confirmed" if confirmed else "close-evidence-pending",
                price if confirmed else None,
                provider_id or client_oid,
                entry["id"],
            ),
        )
        return confirmed


async def run_fallback_monitor_async(
    client: Any, *, environment: str | None = None
) -> list[dict[str, Any]]:
    """Reconcile fallback protection with provider state before any close POST."""
    try:
        entries = load_active()
    except Exception as exc:
        logger.error("Fallback monitor: failed to load active entries: %s", exc)
        return []

    triggered: list[dict[str, Any]] = []
    for entry in entries:
        if environment not in {"DEMO", "LIVE"} or entry.get("environment") != environment:
            continue
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
                await _reconcile_close_evidence(client, entry, flat=True)
            else:
                mark_position_flat(fallback_id)
            continue

        if entry.get("state") == "closing":
            # Recovery reads matched execution evidence; it never repeats a POST.
            await _reconcile_close_evidence(client, entry, flat=False)
            continue

        owned_quantity = _owned_quantity(positions, entry)
        if owned_quantity is None:
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
        claimed = _ensure_close_intent(client_oid, symbol, side, quantity)
        if claimed is False:
            logger.info("Fallback close already claimed for %s", symbol)
            continue
        mark_closing(fallback_id, client_oid)
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
            remaining_quantity = _open_quantity(after_close)
        except Exception:
            remaining_quantity = None
        if remaining_quantity is None:
            _update_close_intent(client_oid, "unknown", provider_order_id)
            continue
        if remaining_quantity <= 0:
            if not await _reconcile_close_evidence(client, entry, flat=True):
                continue
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
    """Require the mark; a last-trade wick is not a native mark-price trigger."""
    if isinstance(value, dict) and "data" in value:
        if value.get("code", "00000") != "00000":
            raise ValueError("Bitget ticker reports provider failure")
        value = value["data"]
    rows = value if isinstance(value, list) else [value]
    for row in rows:
        if not isinstance(row, dict) or row.get("markPrice") is None:
            continue
        price = Decimal(str(row["markPrice"]))
        if price.is_finite() and price > 0:
            return price
    raise ValueError("Bitget ticker has no finite positive mark price")


def run_fallback_monitor() -> list[dict[str, Any]]:
    """Do not run fallback closes without the authenticated async client boundary."""
    logger.error("Synchronous fallback monitor disabled; use run_fallback_monitor_async")
    return []


def reconcile_close(symbol: str, client_oid: str, order_id: str) -> None:
    """Legacy acknowledgement-only API cannot establish a fill; perform no writes."""
    logger.warning(
        "Acknowledgement-only fallback reconciliation refused for %s %s %s",
        symbol,
        client_oid,
        order_id,
    )
