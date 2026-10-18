"""On-demand, read-only operator health report for Telegram."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any

ACCOUNT_SOURCE = "Bitget authenticated GET /api/v2/mix/account/account"


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "", "N/A", "?"):
        return None
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _valid_loss_stop(value: Any) -> bool:
    parsed = _decimal(value)
    return parsed is not None and parsed.is_finite() and parsed > 0


def _safe(value: Any, limit: int = 500) -> str:
    return escape(str(value)[:limit], quote=False)


def _query_one(connection_factory: Callable[[], Any], sql: str) -> tuple[Any, ...] | None:
    with connection_factory() as connection, connection.cursor() as cursor:
        cursor.execute(sql)
        row = cursor.fetchone()
        return tuple(row) if row is not None else None


def _query_all(connection_factory: Callable[[], Any], sql: str) -> list[tuple[Any, ...]]:
    with connection_factory() as connection, connection.cursor() as cursor:
        cursor.execute(sql)
        return [tuple(row) for row in cursor.fetchall()]


def build_operator_diagnostic(
    kind: str,
    gateway: Any,
    connection_factory: Callable[[], Any],
) -> str:
    """Render a bounded, read-only operator diagnostic card."""
    title = {
        "status": "STATUS",
        "reconcile": "RECONCILIATION",
        "protection": "PROTECTION",
        "fills": "RECENT FILLS",
        "intents": "RECENT INTENTS",
        "dispatches": "RECENT DISPATCHES",
        "signals": "RECENT SIGNALS",
    }.get(kind)
    if title is None:
        raise ValueError(f"unknown diagnostic: {kind}")
    lines = [f"<b>FATTY · {title}</b>", "━━━━━━━━━━━━━━━━━━━━"]
    try:
        positions = gateway.get_positions()
        orders = gateway.get_orders()
        provider_known = True
    except Exception as exc:
        positions, orders = [], []
        provider_known = False
        lines.append(f"Provider read 🔴 FAILED · {_safe(type(exc).__name__)}")
    provider_count = len(positions) if provider_known else "UNKNOWN"
    order_count = len(orders) if provider_known else "UNKNOWN"
    if kind == "status":
        lines.extend(
            [
                f"Provider positions: <code>{provider_count}</code>",
                f"Pending orders: <code>{order_count}</code>",
                "Scope: read-only provider snapshot.",
            ]
        )
        return "\n".join(lines)
    queries = {
        "reconcile": """
            SELECT (SELECT count(*) FROM positions WHERE closed_at IS NULL),
                   (SELECT count(*) FROM live_order_intents WHERE exchange='bitget'
                    AND state NOT IN ('filled','rejected','cancelled','reconciled')),
                   (SELECT count(*) FROM fallback_protection WHERE exchange='bitget'
                    AND state IN ('active','closing'))
        """,
        "protection": """
            SELECT symbol, native_state, fallback_allowed, stream_state, last_error
            FROM bitget_protection_capabilities WHERE environment='LIVE'
            ORDER BY updated_at DESC LIMIT 10
        """,
        "fills": """
            SELECT f.symbol, coalesce(i.side, '?'), coalesce(i.role, '?'),
                   f.quantity::text, f.price::text, f.fee::text,
                   f.realized_pnl::text, f.filled_at
            FROM fills f LEFT JOIN live_order_intents i ON i.client_order_id=f.client_order_id
            ORDER BY f.filled_at DESC LIMIT 10
        """,
        "intents": """
            SELECT symbol, side, role, state, requested_qty::text, filled_qty::text,
                   provider_order_id FROM live_order_intents
            ORDER BY updated_at DESC LIMIT 10
        """,
        "dispatches": """
            SELECT d.state, d.terminal_reason, d.updated_at, tm.message_id
            FROM dispatches d LEFT JOIN canonical_signals cs ON cs.id=d.source_id
            LEFT JOIN telegram_messages tm ON tm.id=cs.message_id
            ORDER BY d.updated_at DESC LIMIT 10
        """,
        "signals": """
            SELECT tm.message_id, tm.intake_state, left(replace(tm.raw_text, E'\\n', ' '), 120),
                   cs.pair_token, cs.direction
            FROM telegram_messages tm LEFT JOIN canonical_signals cs ON cs.message_id=tm.id
            ORDER BY tm.received_at DESC, tm.message_id DESC LIMIT 10
        """,
    }
    try:
        rows = _query_all(connection_factory, queries[kind])
    except Exception as exc:
        lines.append(f"Database read 🔴 FAILED · {_safe(type(exc).__name__)}")
        return "\n".join(lines)
    if kind == "reconcile":
        db_positions, active_intents, fallback = rows[0] if rows else ("?", "?", "?")
        lines.extend(
            [
                f"Provider positions: <code>{provider_count}</code>",
                f"DB open positions: <code>{db_positions}</code>",
                f"""State: {
                    (
                        "⚠️ UNKNOWN"
                        if not provider_known or not rows
                        else "⚠️ DRIFT"
                        if str(db_positions) != str(provider_count)
                        else "✅ MATCH"
                    )
                }""",
                f"Active intents: <code>{active_intents}</code>",
                f"Fallback monitors: <code>{fallback}</code>",
                "Provider is authoritative; this command never rewrites the ledger.",
            ]
        )
        return "\n".join(lines)
    for row in rows:
        lines.append(" · ".join(_safe(value) for value in row))
    if not rows:
        lines.append("No records.")
    return "\n".join(lines)


def create_shared_health_reader(snapshot_loader: Callable[[], dict[str, Any]]) -> Callable[[], str]:
    """Wire slash health to the cron renderer without host/docker dependencies."""
    from fatty_trader.operator import health_report_format

    def read() -> str:
        return health_report_format.format_report(**snapshot_loader())

    return read


def normalize_account_snapshot(
    payload: Mapping[str, Any],
    *,
    environment: str,
    observed_at: datetime | None = None,
    product_type: str = "USDT-FUTURES",
    margin_coin: str = "USDT",
) -> dict[str, Any]:
    """Allowlist account telemetry; observation time is successful GET completion."""
    aliases = {
        "equity": ("equity", "accountEquity", "usdtEquity"),
        "available": ("available", "availableBalance"),
        "unrealized_pl": ("unrealized_pl", "unrealizedPL"),
        "locked": ("locked",),
        "isolated_margin": ("isolated_margin", "isolatedMargin"),
        "crossed_margin": ("crossed_margin", "crossedMargin"),
    }
    account: dict[str, Any] = {
        "source": ACCOUNT_SOURCE,
        "read_status": "OK",
        "environment": environment,
        "product_type": product_type,
        "margin_coin": margin_coin,
        "observed_at": (observed_at or datetime.now(UTC)).isoformat(),
    }
    for field, keys in aliases.items():
        value = next((_decimal(payload[key]) for key in keys if payload.get(key) is not None), None)
        account[field] = (
            str(value) if value is not None and (field == "unrealized_pl" or value >= 0) else None
        )
    margin_mode = payload.get("margin_mode", payload.get("marginMode"))
    account["margin_mode"] = (
        margin_mode
        if isinstance(margin_mode, str)
        and margin_mode in {"isolated", "crossed", "ISOLATED", "CROSS"}
        else None
    )
    return account


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


_STREAM_FRESH_SECONDS = 120.0

_LIFECYCLE_ENVIRONMENTS = ("LIVE", "DEMO")
_ACCOUNT_ID_PATTERN = re.compile(r"^[0-9]{1,20}$")

# Exactly the predicates the dispatcher refuses entries on, minus baselined history:
# an open ownership/protection issue is what makes lifecycle recovery not ready.
_LIFECYCLE_ISSUES_SQL = """
SELECT coalesce(json_agg(issue ORDER BY issue), '[]'::json)::text FROM (
    SELECT 'orphan-entry-intent'::text AS issue WHERE EXISTS (
        SELECT 1 FROM live_order_intents i
        LEFT JOIN bitget_margin_reservations m ON m.exchange=i.exchange
            AND (m.client_order_id=i.client_order_id OR m.id=i.margin_reservation_id)
        LEFT JOIN dispatches d ON d.id=m.dispatch_id AND d.exchange=i.exchange
        WHERE i.exchange='bitget' AND i.role='ENTRY'
          AND NOT EXISTS (SELECT 1 FROM bitget_baseline_records b
              WHERE b.record_kind='intent' AND b.record_id=i.id)
          AND (i.filled_qty>0 OR i.state NOT IN ('rejected','cancelled','reconciled'))
          AND (d.id IS NULL OR m.environment IS DISTINCT FROM '{environment}'
               OR (i.dispatch_id IS NOT NULL AND i.dispatch_id <> d.id))
    )
    UNION ALL
    SELECT 'reservation-without-entry-intent'::text WHERE EXISTS (
        SELECT 1 FROM bitget_margin_reservations m
        LEFT JOIN live_order_intents i ON i.exchange=m.exchange
            AND i.client_order_id=m.client_order_id AND i.role='ENTRY'
        WHERE m.exchange='bitget' AND m.state IN ('reserved','unknown','consumed')
          AND NOT EXISTS (SELECT 1 FROM bitget_baseline_records b
              WHERE b.record_kind='reservation' AND b.record_id=m.id)
          AND (m.environment='{environment}' OR m.environment IS NULL)
          AND i.client_order_id IS NULL
    )
    UNION ALL
    SELECT 'unresolved-protection'::text WHERE EXISTS (
        SELECT 1 FROM dispatches d
        WHERE lower(d.exchange)='bitget'
          AND NOT EXISTS (SELECT 1 FROM bitget_baseline_records b
              WHERE b.record_kind='dispatch' AND b.record_id=d.id)
          AND (d.state='UNPROTECTED'
               OR d.terminal_reason='missing-protection-escalated'
               OR d.terminal_reason LIKE 'recovery-missing-protection:%'
               OR d.terminal_reason LIKE 'recovery-filled-protection-unverified%')
    )
    UNION ALL
    SELECT 'baseline-account-environment-mismatch'::text WHERE EXISTS (
        SELECT 1 FROM bitget_audited_baselines
        WHERE account_id IS DISTINCT FROM '{account_id}'
           OR environment IS DISTINCT FROM '{environment}'
    )
) issues
"""


def load_lifecycle_recovery(
    query_one: Callable[[str], Any], *, venue_mode: str, account_id: str | None
) -> str:
    """Report durable dispatch-recovery readiness from the dispatcher's own predicates.

    A container state cannot prove that entries are admitted again, and the dispatcher
    refuses them while any ownership/protection issue is open, so the card reads the
    same durable evidence. Unreadable or unbindable inputs report UNKNOWN, never READY.
    """
    environment = str(venue_mode or "").upper()
    if environment not in _LIFECYCLE_ENVIRONMENTS:
        return "UNKNOWN · venue mode unavailable"
    if not isinstance(account_id, str) or not _ACCOUNT_ID_PATTERN.fullmatch(account_id):
        return "UNKNOWN · account binding unevaluated"
    sql = _LIFECYCLE_ISSUES_SQL.format(environment=environment, account_id=account_id)
    try:
        row = query_one(sql)
        issues = _json(row[0]) if row is not None else None
        if not isinstance(issues, list):
            raise ValueError("lifecycle issue read is incomplete")
    except Exception as exc:  # noqa: BLE001 - unreadable evidence is never READY
        return f"UNKNOWN · {type(exc).__name__}"
    if not issues:
        return "READY"
    return "BLOCKED · " + ", ".join(str(issue) for issue in issues)


def _observation_age_seconds(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - observed).total_seconds())


def stream_condition(evidence: Any) -> str:
    """Stored capability observations are durable evidence, not a live worker probe.

    The value is always labelled as stored: a persisted HEALTHY row says nothing about
    the stream right now, and it never releases a latch.
    """
    if not isinstance(evidence, list) or not evidence or not isinstance(evidence[0], dict):
        return "UNKNOWN · no stored observation"
    newest = evidence[0]
    symbol = str(newest.get("symbol") or "UNKNOWN")
    state = str(newest.get("state") or "UNKNOWN").upper()
    age = _observation_age_seconds(newest.get("last_stream_at") or newest.get("updated_at"))
    suffix = f" · {int(age)}s" if age is not None else ""
    if state == "HEALTHY" and age is not None and age > _STREAM_FRESH_SECONDS:
        return f"STORED-STALE · {symbol} HEALTHY{suffix}"
    return f"STORED · {symbol} {state}{suffix}"


def _latch_recovery_readiness(latch_evidence: Any) -> str:
    """Approved latch recovery is only meaningful while a durable latch is active."""
    if not isinstance(latch_evidence, list):
        return "UNKNOWN · latch ledger read unavailable"
    if not latch_evidence:
        return "CLEAR · no active latch"
    try:
        from fatty_trader.storage.bitget_baseline import BaselineRefused
        from fatty_trader.storage.bitget_live_latch_recovery import validate_latches

        validate_latches([{**row, "active": True} for row in latch_evidence])
    except BaselineRefused:
        return "REFUSED · scope/reason/timestamp outside approved policy"
    except Exception as exc:  # noqa: BLE001 - an unreadable verdict stays UNKNOWN
        return f"UNKNOWN · {type(exc).__name__}"
    return "RELEASABLE · approved recovery permitted"


def load_health_ledger(
    query_one: Callable[[str], Any],
    query_all: Callable[[str], Any],
    *,
    venue_mode: str,
) -> dict[str, Any]:
    """Shared ledger meanings; adapters raise on errors, never return fake empty results.

    Queries are independent so an unavailable section cannot erase a successful one.
    No admission evidence is refreshed and no latch/fallback row is mutated.
    """
    metrics: dict[str, Any] = {"source": "ledger", "errors": {}}

    def fields(names: tuple[str, ...], sql: str) -> None:
        try:
            row = query_one(sql)
            if row is None or len(row) != len(names):
                raise ValueError("ledger result is incomplete")
            metrics.update(zip(names, row, strict=True))
        except Exception as exc:
            metrics.update(dict.fromkeys(names, "UNKNOWN"))
            metrics["errors"][names[0]] = type(exc).__name__

    fields(
        (
            "messages",
            "signals",
            "open_positions",
            "pending_orders",
            "total_orders",
            "active_intents",
            "fallback_positions",
        ),
        """
        SELECT (SELECT count(*) FROM telegram_messages) AS messages,
               (SELECT count(*) FROM canonical_signals) AS signals,
               (SELECT count(*) FROM positions WHERE exchange='bitget'
                AND closed_at IS NULL) AS open_positions,
               (SELECT count(*) FROM orders WHERE exchange='bitget'
                AND state NOT IN ('FILLED','CANCELLED','REJECTED','CLOSED')) AS pending_orders,
               (SELECT count(*) FROM orders WHERE exchange='bitget') AS total_orders,
               (SELECT count(*) FROM live_order_intents WHERE exchange='bitget'
                AND state NOT IN ('filled','rejected','cancelled','reconciled')) AS active_intents,
               (SELECT count(*) FROM fallback_protection WHERE exchange='bitget'
                AND state IN ('active','closing')) AS fallback_positions
        """,
    )
    fields(
        ("kill_switch", "kill_reason", "active_kill_switches"),
        """
        SELECT (SELECT CASE WHEN active THEN 'ACTIVE'
                       WHEN reason LIKE 'released:%' THEN 'RELEASED' ELSE 'INACTIVE' END
                FROM venue_kill_switches WHERE scope='bitget') AS kill_switch,
               (SELECT reason FROM venue_kill_switches WHERE scope='bitget') AS kill_reason,
               (SELECT coalesce(json_agg(json_build_object(
                   'scope', scope, 'reason', reason, 'latched_at', latched_at,
                   'updated_at', updated_at, 'age_seconds',
                   extract(epoch FROM (CURRENT_TIMESTAMP-latched_at))) ORDER BY scope),
                   '[]'::json)
                FROM venue_kill_switches WHERE active) AS active_kill_switches
        """,
    )
    try:
        latches = _json(metrics["active_kill_switches"])
        if not isinstance(latches, list) or any(not isinstance(row, dict) for row in latches):
            raise ValueError("invalid latch rows")
        metrics["active_kill_switches"] = latches
    except (TypeError, ValueError):
        metrics["active_kill_switches"] = "UNKNOWN"
    fields(
        ("reservation_totals",),
        """
        SELECT coalesce(json_object_agg(state, count), '{}'::json)
        FROM (SELECT state, count(*) FROM bitget_margin_reservations
              WHERE exchange='bitget' GROUP BY state) r
        """,
    )
    fields(
        ("last_admission_captured_at", "last_admission_age_seconds"),
        """
        SELECT max(captured_at) AS captured_at,
               extract(epoch FROM (CURRENT_TIMESTAMP-max(captured_at))) AS age_seconds
        FROM balance_snapshots WHERE exchange='bitget'
        """,
    )
    fields(
        ("latest_post_fill_reconciliation",),
        """
        SELECT (SELECT status FROM bitget_post_fill_reconciliations WHERE exchange='bitget'
                ORDER BY created_at DESC LIMIT 1)
        """,
    )
    pnl_names = ("fill_n", "total_pnl", "gross_profit", "gross_loss", "total_fees")
    try:
        row = query_one("""
            SELECT count(*) AS fill_n, coalesce(sum(realized_pnl),0) AS total_pnl,
                   coalesce(sum(CASE WHEN realized_pnl>0 THEN realized_pnl ELSE 0 END),0)
                       AS gross_profit,
                   coalesce(sum(CASE WHEN realized_pnl<0 THEN realized_pnl ELSE 0 END),0)
                       AS gross_loss,
                   coalesce(sum(fee),0) AS total_fees FROM fills WHERE exchange='bitget'
        """)
        if row is None or len(row) != len(pnl_names):
            raise ValueError("incomplete PnL result")
        pnl = dict(zip(pnl_names, row, strict=True))
    except Exception as exc:
        pnl = dict.fromkeys(pnl_names, "UNKNOWN")
        metrics["errors"]["pnl"] = type(exc).__name__
    try:
        rows = query_all("""
            SELECT message_id, received_at, left(raw_text,500) FROM telegram_messages
            ORDER BY received_at DESC, message_id DESC LIMIT 1
        """)
        messages: list[dict[str, Any]] | None = [
            dict(zip(("msg_id", "received_at", "preview"), row, strict=True)) for row in rows
        ]
    except Exception as exc:
        messages = None
        metrics["errors"]["latest_message"] = type(exc).__name__
    try:
        fallback_rows = query_all("""
            SELECT symbol, direction, entry_price::text, stop_loss::text,
                   take_profits::text, quantity::text, state, position_key, updated_at::text
            FROM fallback_protection WHERE exchange='bitget' AND state IN ('active','closing')
            ORDER BY updated_at DESC
        """)
    except Exception as exc:
        fallback_rows = None
        metrics["errors"]["fallback"] = type(exc).__name__
    try:
        # Persisted capability observations are not a live worker probe.
        environment = venue_mode if venue_mode in {"LIVE", "DEMO"} else "UNKNOWN"
        rows = query_all(f"""
            SELECT symbol, stream_state, last_stream_at, updated_at
            FROM bitget_protection_capabilities
            WHERE exchange='bitget' AND environment='{environment}'
            ORDER BY updated_at DESC LIMIT 5
        """)
        metrics["stream_evidence"] = [
            dict(zip(("symbol", "state", "last_stream_at", "updated_at"), row, strict=True))
            for row in rows
        ]
    except Exception as exc:
        metrics["stream_evidence"] = None
        metrics["errors"]["stream_evidence"] = type(exc).__name__
    metrics["monitor_condition"] = "UNKNOWN · not probed"
    metrics["stream_condition"] = stream_condition(metrics.get("stream_evidence"))
    metrics["recovery_readiness"] = _latch_recovery_readiness(metrics.get("active_kill_switches"))
    return {"metrics": metrics, "pnl": pnl, "messages": messages, "fallback_rows": fallback_rows}


def load_health_protection(
    positions: list[dict[str, Any]] | None, fallback_rows: Any
) -> dict[str, Any]:
    """Report registrations only; levels/rows do not prove runtime enforcement."""
    statuses = {
        p["symbol"]: {
            "has_sl": _valid_loss_stop(p.get("stop_loss")),
            "has_tp": _valid_loss_stop(p.get("take_profit")),
            "native_sl_id_reported": bool(p.get("stop_loss_id")),
            "native_tp_id_reported": bool(p.get("take_profit_id")),
            "native_sl": p.get("stop_loss"),
            "native_tp": p.get("take_profit"),
            "fallback_read_status": "UNKNOWN" if fallback_rows is None else "OK",
        }
        for p in (positions or [])
    }
    for row in fallback_rows or []:
        if len(row) != 9:
            continue
        symbol, direction, entry, stop, targets, quantity, state, key, updated = row
        status = statuses.setdefault(str(symbol).upper(), {})
        try:
            tps = _json(targets)
            target = ", ".join(map(str, tps)) if isinstance(tps, list) else None
        except (TypeError, ValueError):
            target = None
        status.update(
            {
                "fallback": True,
                "fallback_direction": direction,
                "fallback_entry": entry,
                "fallback_sl": stop,
                "fallback_tp": target,
                "fallback_quantity": quantity,
                "fallback_state": str(state).upper(),
                "fallback_position_key": key,
                "fallback_updated_at": updated,
                "fallback_read_status": "OK",
                "enforcement": "UNKNOWN",
            }
        )
    return statuses


def load_operator_health_snapshot(
    gateway: Any,
    connection_factory: Callable[[], Any],
    *,
    mode: str,
    venue_mode: str,
    execution_enabled: bool | None,
    fallback_mutations_enabled: str = "UNKNOWN",
) -> dict[str, Any]:
    """Load one read per provider section and independent shared ledger sections."""

    def read_provider(method: Callable[[], Any], expected: type, unknown: Any) -> Any:
        try:
            value = method()
            if not isinstance(value, expected):
                return unknown
            if isinstance(value, list) and any(not isinstance(row, dict) for row in value):
                return unknown
            return value
        except Exception:
            return unknown

    raw_positions = read_provider(gateway.get_positions, list, None)
    raw_orders = read_provider(gateway.get_orders, list, None)
    try:
        raw_account = gateway.get_account_snapshot()
        if not isinstance(raw_account, dict) or not raw_account:
            raise ValueError("invalid account response")
        account = (
            normalize_account_snapshot(raw_account, environment=venue_mode)
            if "read_status" not in raw_account
            else raw_account
        )
    except Exception as exc:
        account = {
            "read_status": "UNKNOWN",
            "error": type(exc).__name__,
            "source": ACCOUNT_SOURCE,
            "observed_at": None,
            "environment": venue_mode,
            "product_type": "USDT-FUTURES",
            "margin_coin": "USDT",
        }
    positions = (
        None
        if raw_positions is None
        else [
            {
                **p,
                "direction": p.get("side"),
                "qty": p.get("size"),
                "entry_price": p.get("entry"),
                "mark_price": p.get("mark"),
            }
            for p in raw_positions
        ]
    )
    orders = (
        None
        if raw_orders is None
        else [{**o, "qty": o.get("qty", o.get("size"))} for o in raw_orders]
    )
    ledger = load_health_ledger(
        lambda sql: _query_one(connection_factory, sql),
        lambda sql: _query_all(connection_factory, sql),
        venue_mode=venue_mode,
    )
    ledger["metrics"]["fallback_mutations_enabled"] = fallback_mutations_enabled
    lifecycle_recovery = load_lifecycle_recovery(
        lambda sql: _query_one(connection_factory, sql),
        venue_mode=venue_mode,
        account_id=os.environ.get("CONSOLE_ACCOUNT_ID"),
    )
    return {
        "positions": positions,
        "pending_orders": orders,
        "sltp": load_health_protection(raw_positions, ledger["fallback_rows"]),
        "pnl": ledger["pnl"],
        "messages": ledger["messages"],
        "metrics": ledger["metrics"],
        "account": account,
        "modes": {
            "mode": mode,
            "venue_mode": venue_mode,
            "execution_enabled": "UNKNOWN"
            if execution_enabled is None
            else "1"
            if execution_enabled
            else "0",
        },
        "codex": {"status": "UNKNOWN", "error": "Not probed in operator container"},
        "services": {
            "status": "UNKNOWN",
            "error": "Not probed in operator container",
            "lifecycle_recovery": lifecycle_recovery,
        },
    }
