"""On-demand, read-only operator health report for Telegram."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "", "N/A", "?"):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _amount(value: Any) -> str:
    parsed = _decimal(value)
    if parsed is None:
        return "N/A"
    return f"{parsed:,.6f}".rstrip("0").rstrip(".") or "0"


def _pnl(value: Any) -> str:
    parsed = _decimal(value)
    if parsed is None:
        return "N/A"
    return f"{parsed:+,.6f}".rstrip("0").rstrip(".")


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


def build_operator_health_report(
    gateway: Any,
    connection_factory: Callable[[], Any],
    *,
    mode: str,
    venue_mode: str,
    execution_enabled: bool,
    fallback_mutations_enabled: str = "UNKNOWN",
    stream_enabled: str = "UNKNOWN",
    stream_mode: str = "UNKNOWN",
    stream_mutations_enabled: str = "UNKNOWN",
    capability_gate_enabled: str = "UNKNOWN",
) -> str:
    """Read provider/DB state and explain what each status means."""
    try:
        account = gateway.get_account_snapshot()
        positions = gateway.get_positions()
        orders = gateway.get_orders()
        provider_error = None
    except Exception as exc:
        account = {}
        positions = []
        orders = []
        provider_error = type(exc).__name__

    db_error: str | None = None
    try:
        metrics = _query_one(
            connection_factory,
            """
            SELECT
              (SELECT count(*) FROM telegram_messages),
              (SELECT count(*) FROM canonical_signals),
              (SELECT count(*) FROM positions WHERE closed_at IS NULL),
              (SELECT count(*) FROM orders
                 WHERE state NOT IN ('FILLED','CANCELLED','REJECTED','CLOSED')),
              (SELECT count(*) FROM live_order_intents
                 WHERE exchange = 'bitget'
                   AND state NOT IN ('filled','rejected','cancelled','reconciled')),
              (SELECT count(*) FROM fallback_protection
                 WHERE exchange = 'bitget' AND state IN ('active','closing')),
              (SELECT active FROM venue_kill_switches WHERE scope = 'bitget'),
              (SELECT reason FROM venue_kill_switches WHERE scope = 'bitget'),
              (SELECT message_id
                 FROM telegram_messages
                ORDER BY received_at DESC, message_id DESC LIMIT 1),
              (SELECT received_at
                 FROM telegram_messages
                ORDER BY received_at DESC, message_id DESC LIMIT 1),
              (SELECT raw_text
                 FROM telegram_messages
                ORDER BY received_at DESC, message_id DESC LIMIT 1)
            """,
        )
        fallback_rows = _query_all(
            connection_factory,
            """
            SELECT symbol, direction, entry_price::text, stop_loss::text,
                   take_profits::text, quantity::text, state
            FROM fallback_protection
            WHERE exchange = 'bitget' AND state IN ('active','closing')
            ORDER BY updated_at DESC
            """,
        )
    except Exception as exc:
        metrics = None
        fallback_rows = []
        db_error = type(exc).__name__
    else:
        db_error = None

    provider_count = len(positions) if provider_error is None else None
    db_count = int(metrics[2]) if metrics is not None else None
    drift = provider_count is not None and db_count is not None and provider_count != db_count
    kill_active = bool(metrics[6]) if metrics is not None and metrics[6] is not None else None
    kill_reason = metrics[7] if metrics is not None else None

    lines = [
        "🩺 <b>FATTY HEALTH · ON-DEMAND</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "<i>Read-only snapshot. No order, cancel, close, or protection mutation.</i>",
        "",
        "<b>SYSTEM</b>",
        f"Runtime     <code>{_safe(mode)}</code> · venue <code>{_safe(venue_mode)}</code>",
        f"Execution   <code>{'ENABLED' if execution_enabled else 'DISABLED'}</code>",
        f"Fallback mut <code>{_safe(fallback_mutations_enabled)}</code>",
        f"Stream      <code>{_safe(stream_enabled)} · {_safe(stream_mode)}</code>",
        f"Stream mut  <code>{_safe(stream_mutations_enabled)}</code>",
        f"Capability  <code>{_safe(capability_gate_enabled)}</code>",
        "Command    ✅ operator bot responding",
        "",
    ]

    if provider_error is None:
        equity_value = _amount(account.get("usdtEquity", account.get("equity")))
        lines.extend(
            [
                "<b>PROVIDER</b>",
                f"Read       ✅ OK · {provider_count} open position(s) · "
                f"{len(orders)} pending order(s)",
                f"Equity     <code>{_safe(equity_value)}</code> USDT",
                f"Available  <code>{_safe(_amount(account.get('available')))}</code> USDT",
            ]
        )
    else:
        lines.extend(
            [
                "<b>PROVIDER</b>",
                f"Read       🔴 FAILED · {_safe(provider_error)}",
                "Meaning    Provider state below is unknown, not flat.",
                "Action     Do not release gates or assume positions are safe.",
            ]
        )

    if metrics is None:
        lines.extend(
            [
                "",
                "<b>DATABASE</b>",
                f"Read       🔴 FAILED · {_safe(db_error)}",
                "Meaning    Ledger/reconciliation counts are unknown.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "<b>RECONCILIATION</b>",
                f"Provider positions  <code>{provider_count}</code>",
                f"DB open positions   <code>{db_count}</code>",
                f"State               {'⚠️ DRIFT' if drift else '✅ MATCH'}",
                f"Active intents      <code>{metrics[4]}</code>",
                f"Fallback monitors   <code>{metrics[5]}</code>",
                f"Messages/signals    <code>{metrics[0]}/{metrics[1]}</code>",
                "Meaning             Provider is source of truth; DB drift needs reconciliation.",
            ]
        )

    lines.append("")
    lines.append("<b>OPEN POSITIONS · PROVIDER AUTHORITATIVE</b>")
    if provider_error is not None or not positions:
        lines.append(
            "<pre>None confirmed by provider</pre>"
            if provider_error is None
            else "<pre>UNKNOWN · provider read failed</pre>"
        )
    else:
        fallback_by_symbol = {str(row[0]).upper(): row for row in fallback_rows if row}
        for position in positions[:5]:
            symbol = str(position.get("symbol", "?")).upper()
            side = str(position.get("side", "?")).upper()
            native_sl = position.get("stop_loss")
            native_tp = position.get("take_profit")
            fallback = fallback_by_symbol.get(symbol)
            if native_sl is not None or native_tp is not None:
                protection = "🟢 NATIVE"
                why = "Provider has native protection fields."
                action = "No operator action needed."
            elif fallback:
                protection = "🟡 FALLBACK"
                why = "Native protection is absent; local fallback monitor owns the levels."
                action = (
                    "Verify stream freshness and fallback mutation gate "
                    "before relying on auto-close."
                )
            else:
                protection = "🔴 MISSING"
                why = "Neither native nor fallback protection is registered."
                action = "Do not treat this position as protected."
            lines.extend(
                [
                    f"<b>{_safe(symbol)} · {_safe(side)}</b>",
                    "<pre>"
                    f"Size       {_safe(position.get('size'))}\n"
                    f"Entry      {_safe(position.get('entry'))}\n"
                    f"Mark       {_safe(position.get('mark'))}\n"
                    f"uPnL       {_safe(_pnl(position.get('unrealized_pl')))} USDT\n"
                    f"Leverage   {_safe(position.get('leverage'))}x · "
                    f"{_safe(position.get('margin_mode'))}\n"
                    f"Liq price  {_safe(position.get('liquidation_price'))}\n"
                    f"Protection {protection}\n"
                    f"Native SL  {_safe(native_sl or 'MISSING')}\n"
                    f"Native TP  {_safe(native_tp or 'MISSING')}\n"
                    f"Fallback   {_safe(fallback[6] if fallback else 'NONE')}"
                    "</pre>",
                    f"Why        {why}",
                    f"Action     {action}",
                    "",
                ]
            )

    kill_label = (
        "🔴 ACTIVE" if kill_active else "✅ INACTIVE" if kill_active is False else "⚠️ UNKNOWN"
    )
    lines.extend(
        [
            "<b>KILL SWITCH</b>",
            f"State      <code>{kill_label}</code>",
            f"Reason     <code>{_safe(kill_reason or 'N/A')}</code>",
            "Meaning    Active blocks new dispatches; it does not automatically "
            "close existing positions.",
            "Action     Release only after the root cause and provider state are verified.",
        ]
    )
    if metrics is not None and metrics[8] is not None:
        lines.extend(
            [
                "",
                "<b>LATEST SOURCE</b>",
                f"ID         <code>#{_safe(metrics[8])}</code>",
                f"Received   <code>{_safe(metrics[9])}</code>",
                f"Text       <code>{_safe(metrics[10], 300)}</code>",
            ]
        )
    return "\n".join(lines)[:3900]


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
    except Exception as exc:
        positions, orders = [], []
        lines.append(f"Provider read 🔴 FAILED · {_safe(type(exc).__name__)}")
    if kind == "status":
        lines.extend(
            [
                f"Provider positions: <code>{len(positions)}</code>",
                f"Pending orders: <code>{len(orders)}</code>",
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
            SELECT symbol, side, role, filled_qty::text, filled_price::text, updated_at
            FROM live_order_intents WHERE state='filled'
            ORDER BY updated_at DESC LIMIT 10
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
                f"Provider positions: <code>{len(positions)}</code>",
                f"DB open positions: <code>{db_positions}</code>",
                f"State: {'⚠️ DRIFT' if str(db_positions) != str(len(positions)) else '✅ MATCH'}",
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
