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


def _valid_loss_stop(value: Any) -> bool:
    parsed = _decimal(value)
    return parsed is not None and parsed.is_finite() and parsed > 0


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

    # A fallback row is inert unless its mutation gate is on and the venue
    # kill switch is explicitly inactive; unknown DB state fails closed.
    fallback_can_close = str(fallback_mutations_enabled).strip() == "1" and kill_active is False

    lines = [
        "🩺 <b>FATTY HEALTH · ON-DEMAND</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "<i>Read-only snapshot. No order, cancel, close, or protection mutation.</i>",
        "",
        "<b>SYSTEM</b>",
        f"Runtime     <code>{_safe(mode)}</code> · venue <code>{_safe(venue_mode)}</code>",
        "Policy      "
        + (
            "✅ LIVE-only runtime"
            if mode == "LIVE" and venue_mode == "LIVE"
            else "⚠️ DEGRADED · runtime does not satisfy LIVE-only policy"
        ),
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
                f"""Provider positions  <code>{
                    (provider_count if provider_count is not None else "UNKNOWN")
                }</code>""",
                f"DB open positions   <code>{db_count}</code>",
                f"""State               {
                    ("⚠️ UNKNOWN" if provider_count is None else "⚠️ DRIFT" if drift else "✅ MATCH")
                }""",
                f"Active intents      <code>{metrics[4]}</code>",
                f"Fallback monitors   <code>{metrics[5]}</code>",
                f"Messages/signals    <code>{metrics[0]}/{metrics[1]}</code>",
                "Meaning             Provider is source of truth; DB drift needs reconciliation.",
                "Note               A position opened outside the bot (or before a "
                "restart) will show DB drift with no intents. Provider still governs.",
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
            # A TP-only row cannot supply downside protection either.
            if fallback and not _valid_loss_stop(fallback[3]):
                fallback = None
            if _valid_loss_stop(native_sl):
                protection = "🟢 NATIVE"
                why = "Provider reports a native loss stop; runtime execution is not probed."
                action = "No operator action needed."
            elif fallback and not fallback_can_close:
                protection = "🔴 GATED OFF · CANNOT CLOSE"
                why = (
                    "Fallback levels are registered but the fallback mutation "
                    "gate is OFF. The position has NO working auto-close."
                )
                action = (
                    "Enable BITGET_FALLBACK_MUTATIONS_ENABLED (kill switch must "
                    "be INACTIVE) or close manually. It is not protected now."
                )
            elif fallback:
                protection = "🟡 FALLBACK"
                why = "Native protection is absent; local fallback monitor owns the levels."
                action = "Verify stream freshness before relying on auto-close."
            else:
                protection = "🔴 MISSING LOSS STOP"
                why = (
                    "No native loss stop or fallback loss stop is registered. TP alone "
                    "does not limit loss."
                )
                action = "Do not treat this position as protected."
            if fallback and not fallback_can_close:
                fallback_line = f"{_safe(fallback[6])} (registered, INERT)"
            elif fallback:
                fallback_line = f"{_safe(fallback[6])} (can close)"
            else:
                fallback_line = "NONE"
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
                    f"Fallback   {fallback_line}"
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


def load_operator_health_snapshot(
    gateway: Any,
    connection_factory: Callable[[], Any],
    *,
    mode: str,
    venue_mode: str,
    execution_enabled: bool,
) -> dict[str, Any]:
    """Load read-only renderer inputs; failed reads are None, not flat lists.

    Native fields show registered protection, not worker readiness. Host-only
    Codex/docker probes and fallback runtime readiness are not inferred here.
    """

    def read_provider(method: Callable[[], Any], expected: type, unknown: Any) -> Any:
        try:
            value = method()
            return value if isinstance(value, expected) else unknown
        except Exception:
            return unknown

    raw_positions = read_provider(gateway.get_positions, list, None)
    raw_orders = read_provider(gateway.get_orders, list, None)
    account = read_provider(gateway.get_account_snapshot, dict, {})
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
    sltp = {
        p["symbol"]: {
            "has_sl": _valid_loss_stop(p.get("stop_loss")),
            "has_tp": _valid_loss_stop(p.get("take_profit")),
            "native_sl": p.get("stop_loss"),
            "native_tp": p.get("take_profit"),
        }
        for p in (raw_positions or [])
    }
    names = (
        "messages",
        "signals",
        "open_positions",
        "pending_orders",
        "active_intents",
        "fallback_positions",
        "kill_switch",
        "kill_reason",
        "active_kill_switches",
    )
    metrics = dict.fromkeys(names, "UNKNOWN")
    try:
        row = _query_one(
            connection_factory,
            (
                "\n"
                "            SELECT\n"
                "              (SELECT count(*) FROM telegram_messages),\n"
                "              (SELECT count(*) FROM canonical_signals),\n"
                "              (SELECT count(*) FROM positions WHERE closed_at IS "
                "NULL),\n"
                "              (SELECT count(*) FROM orders WHERE state NOT IN "
                "('FILLED','CANCELLED','REJECTED','CLOSED')),\n"
                "              (SELECT count(*) FROM live_order_intents WHERE "
                "exchange='bitget'\n"
                "                 AND state NOT IN ('filled','rejected','cancelled','re"
                "conciled')),\n"
                "              (SELECT count(*) FROM fallback_protection WHERE "
                "exchange='bitget' AND state IN ('active','closing')),\n"
                "              (SELECT active FROM venue_kill_switches WHERE "
                "scope='bitget'),\n"
                "              (SELECT reason FROM venue_kill_switches WHERE "
                "scope='bitget'),\n"
                "              (SELECT coalesce(json_agg(json_build_object('scope', scope, "
                "'reason', reason) ORDER BY scope), '[]'::json) "
                "FROM venue_kill_switches WHERE active AND scope IN "
                "('global', 'bitget', 'bitget-protection-stream'))\n"
                "        "
            ),
        )
        if row is not None:
            metrics.update(zip(names, row, strict=False))
            metrics["kill_switch"] = (
                "ACTIVE" if row[6] is True else "INACTIVE" if row[6] is False else "UNKNOWN"
            )
    except Exception:
        pass
    return {
        "positions": positions,
        "pending_orders": orders,
        "sltp": sltp,
        "pnl": {"fill_n": "UNKNOWN"},
        "messages": [],
        "metrics": metrics,
        "account": {**account, "equity": account.get("equity", account.get("usdtEquity"))}
        if account
        else {},
        "modes": {
            "mode": mode,
            "venue_mode": venue_mode,
            "execution_enabled": "1" if execution_enabled else "0",
        },
        "codex": {"status": "N/A", "error": "Not probed in operator container"},
        "services": {"status": "UNKNOWN"},
    }
