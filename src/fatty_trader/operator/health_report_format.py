"""Canonical health-report formatting, shared by the cron and Telegram /health.

This module is the SINGLE renderer for the Fatty health card. It was
extracted verbatim from scripts/health_report.py so the 6-hourly cron and
the on-demand /health slash command cannot drift apart.

It is deliberately PURE: no database, no docker, no network. Callers supply
already-loaded data. That is what makes it usable inside the operator-bot
container, which has neither a docker socket nor host Codex credentials.

Anything that performs I/O (loaders, probes, send_direct) must stay in
scripts/health_report.py and must NOT be imported here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any

from fatty_trader.telegram_html import bounded_html

try:
    from datetime import UTC
except ImportError:  # pragma: no cover - Python < 3.11
    UTC = timezone.utc  # noqa: UP017


def fmt_price(val: Any) -> str:
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


def _provider_summary(
    positions: list[dict[str, Any]] | None,
    pending_orders: list[Any] | None,
) -> str:
    if positions is None or pending_orders is None:
        return "UNKNOWN · provider read failed"
    return f"OK · {len(positions)} pos · {len(pending_orders)} orders"


def _reconciliation_summary(positions: list[dict[str, Any]] | None, metrics: dict[str, Any]) -> str:
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


def fmt_pnl(val: Any) -> tuple[str, str]:
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


def margin_used(
    positions: list[dict[str, Any]] | None,
    account: dict[str, Any],
) -> Decimal | None | str:
    """Return provider margin committed by open positions."""
    if positions is None:
        return "UNKNOWN"
    if not positions:
        return Decimal("0")
    values = [_decimal(position.get("margin_used")) for position in positions]
    known_position = [value for value in values if value is not None]
    if len(known_position) == len(values):
        return sum(known_position, Decimal("0"))
    account_values = [
        _decimal(account.get("isolated_margin")),
        _decimal(account.get("crossed_margin")),
    ]
    known = [value for value in account_values if value is not None]
    return sum(known, Decimal("0")) if known else None


def _format_timestamp(iso_string: str) -> str:
    if not iso_string:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_string.replace(" ", "T").replace("+00", "+00:00"))
        jakarta_tz = timezone(timedelta(hours=7))
        return dt.astimezone(jakarta_tz).strftime("%d %b %Y, %H:%M WIB")
    except (TypeError, ValueError):
        return str(iso_string)


def format_report(
    positions: list[dict[str, Any]] | None,
    pending_orders: list[Any] | None,
    sltp: dict[str, Any],
    pnl: dict[str, Any],
    messages: list[Any],
    metrics: dict[str, Any],
    account: dict[str, Any],
    modes: dict[str, Any],
    codex: dict[str, Any],
    services: dict[str, Any] | None = None,
) -> str:
    jakarta_tz = timezone(timedelta(hours=7))
    now = datetime.now(UTC).astimezone(jakarta_tz).strftime("%d %b %Y, %H:%M WIB")
    metrics = metrics or {}
    account = account or {}
    modes = modes or {}
    codex = codex or {}
    services = services or {}

    live_runtime = modes.get("mode") == "LIVE" and modes.get("venue_mode") == "LIVE"
    health_title = "LIVE HEALTH" if live_runtime else "NON-LIVE / UNKNOWN HEALTH"
    provider_known = positions is not None and pending_orders is not None
    service_unhealthy = (
        services.get("status") != "OK"
        or _count(services.get("unhealthy")) > 0
        or _count(services.get("starting")) > 0
        or _count(services.get("unexpected_runtime")) > 0
        or _count(services.get("running")) < _count(services.get("total"))
        or _count(services.get("healthy")) < _count(services.get("running"))
        or str(services.get("dispatcher_state", "UNKNOWN")).upper() != "RUNNING"
    )
    codex_unhealthy = codex.get("status") in {"AUTH_FAILED", "N/A"}
    latch_evidence = metrics.get("active_kill_switches", [])
    active_latches = latch_evidence if isinstance(latch_evidence, list) else []
    kill_unhealthy = (
        not isinstance(latch_evidence, list)
        or str(metrics.get("kill_switch", "UNKNOWN")).upper() not in {"INACTIVE", "RELEASED"}
        or bool(active_latches)
    )
    lifecycle = str(services.get("lifecycle_recovery", "UNKNOWN")).upper()
    execution_ready = str(modes.get("execution_enabled", "UNKNOWN")) == "1"
    overall = (
        "🟢 ONLINE"
        if (
            live_runtime
            and provider_known
            and not service_unhealthy
            and not codex_unhealthy
            and not kill_unhealthy
            and execution_ready
            and lifecycle == "READY"
        )
        else "⚠️ DEGRADED"
    )
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
    committed_margin = margin_used(positions, account)

    kill_state = str(metrics.get("kill_switch", "UNKNOWN")).upper()
    if kill_state == "ACTIVE":
        kill_display = "🔴 ACTIVE"
    elif kill_state == "RELEASED":
        kill_display = "✅ INACTIVE · released"
    elif kill_state == "INACTIVE":
        kill_display = "✅ INACTIVE"
    else:
        kill_display = "⚠️ UNKNOWN"

    if active_latches:
        kill_display = "🔴 ACTIVE · " + "; ".join(
            f"{latch.get('scope', 'UNKNOWN')}: {latch.get('reason', 'UNKNOWN')}"
            for latch in active_latches
        )

    if services.get("status") == "OK":
        service_line = (
            f"{services.get('running', '?')}/{services.get('total', '?')} running · "
            f"{services.get('healthy', '?')} healthy"
        )
        if _count(services.get("starting")):
            service_line += f" · {services['starting']} starting"
        if _count(services.get("unhealthy")):
            service_line += f" · {services['unhealthy']} unhealthy"
        if _count(services.get("unexpected_runtime")):
            service_line += f" · {services['unexpected_runtime']} unexpected non-LIVE service(s)"
    else:
        service_line = "UNKNOWN"

    def status_mark(value: str) -> str:
        return "✅" if value.startswith("OK") else "⚠️"

    L = [
        f"📡 <b>Fatty Signal Relay</b>  <code>{health_title}</code>",
        f"<i>{now}</i>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"<b>{overall}  SYSTEM</b>",
        "<pre>Runtime    "
        f"{_html(modes.get('mode', '?'))} · Bitget {_html(modes.get('venue_mode', '?'))}\n"
        f"Execution  {_html(execution)} · permission only, not fill evidence\n"
        f"Lifecycle  {_html(lifecycle)}\n"
        f"Dispatcher {_html(services.get('dispatcher_state', 'UNKNOWN'))}\n"
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
        f"Updated   {_html(codex.get('refreshed'))}"
        + (f"\nReason    {_html(codex.get('error'))}" if codex.get("error") else "")
        + "</pre>",
        "",
        f"<b>💰 ACCOUNT</b> <code>Bitget {_html(modes.get('venue_mode', 'UNKNOWN'))}</code>",
    ]

    if account:
        account_icon, account_upnl = fmt_pnl(account.get("unrealized_pl"))
        L.append(
            "<pre>Equity     "
            f"{_html(fmt_amount(account.get('equity')))} USDT\n"
            f"Available  {_html(fmt_amount(account.get('available')))} USDT\n"
            f"Margin used {_html(fmt_amount(committed_margin))} USDT\n"
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
            position_values: list[Decimal] = [
                value
                for value in (_decimal(p.get("unrealized_pl")) for p in positions)
                if value is not None
            ]
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
        f"Reservations    {_html(metrics.get('reservation_totals', '{}'))}\n"
        f"Snapshot age    {_html(metrics.get('newest_balance_snapshot_age_seconds', 'N/A'))} sec\n"
        f"Post-fill       {_html(metrics.get('latest_post_fill_reconciliation', 'N/A'))}\n"
        f"Fallback mon.  {_html(metrics.get('fallback_positions', '0'))} active</pre>"
    )
    L.append("<i>Signals/analyzer output are not provider fills; triggers are not execution.</i>")
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
    return bounded_html(report)
