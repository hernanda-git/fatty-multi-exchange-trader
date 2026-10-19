"""Pure Telegram HTML renderer for cron and on-demand health observations.

Core financial/ledger semantics are shared by loaders in operator.health.
Host-only probes legitimately differ; cards are immutable point-in-time snapshots,
not byte-identical observations collected at different times.
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
    value = _decimal(val)
    return f"{value:.8f}".rstrip("0").rstrip(".") if value is not None else "UNKNOWN"


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "", "N/A", "?"):
        return None
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def fmt_amount(value: Any) -> str:
    decimal = _decimal(value)
    if decimal is None:
        return "UNKNOWN"
    return f"{decimal:,.6f}".rstrip("0").rstrip(".") or "0"


def fmt_gap(mark: Any, level: Any) -> str:
    mark_decimal = _decimal(mark)
    level_decimal = _decimal(level)
    if mark_decimal is None or level_decimal is None or mark_decimal == 0:
        return "N/A"
    return f"{abs(mark_decimal - level_decimal) / abs(mark_decimal) * 100:.2f}%"


def fallback_is_registered(protection: dict[str, Any]) -> bool:
    return bool(protection.get("fallback"))


def protection_state(protection: dict[str, Any]) -> str:
    if protection.get("has_sl") and protection.get("has_tp"):
        return "NATIVE REGISTERED · enforcement unprobed"
    if fallback_is_registered(protection):
        stop = _decimal(protection.get("fallback_sl"))
        if stop is None or stop <= 0:
            return "MISSING LOSS STOP · invalid/TP-only fallback"
        state = str(protection.get("fallback_state", "UNKNOWN")).upper()
        return f"FALLBACK {state} · registration only"
    if protection.get("native_sl_id_reported"):
        return "NATIVE ID REPORTED · stop level UNKNOWN"
    if protection.get("has_sl") or protection.get("has_tp"):
        return "PARTIAL · enforcement unprobed"
    if protection.get("fallback_read_status") == "UNKNOWN":
        return "UNKNOWN · fallback read failed"
    return "MISSING"


def _html(value: Any, default: str = "UNKNOWN", limit: int = 500) -> str:
    text = default if value is None or value == "" else str(value)
    return escape(text[:limit], quote=False)


def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _known_count(value: Any) -> int | None:
    parsed = _decimal(value)
    if parsed is None or parsed < 0 or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def _provider_summary(
    positions: list[dict[str, Any]] | None,
    pending_orders: list[Any] | None,
) -> str:
    if positions is None or pending_orders is None:
        return "UNKNOWN · provider read failed"
    return f"OK · {len(positions)} pos · {len(pending_orders)} orders"


def _reconciliation_summary(positions: list[dict[str, Any]] | None, metrics: dict[str, Any]) -> str:
    provider_count = _known_count(metrics.get("provider_positions"))
    db_count = _known_count(metrics.get("open_positions"))
    if positions is None or provider_count is None or db_count is None:
        return "UNKNOWN · provider or ledger read unavailable"
    if provider_count == db_count:
        return f"OK · provider {provider_count} = DB {db_count}"
    return f"DRIFT · provider {provider_count} vs DB {db_count}"


def _position_range_state(position: dict[str, Any], protection: dict[str, Any]) -> str:
    mark = _decimal(position.get("mark_price"))
    sl = (
        protection.get("fallback_sl")
        if fallback_is_registered(protection) and not protection.get("has_sl")
        else protection.get("native_sl")
    )
    tp = (
        protection.get("fallback_tp")
        if fallback_is_registered(protection) and not protection.get("has_tp")
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


def _signed_amount(value: Decimal) -> str:
    text = f"{value:,.4f}".rstrip("0").rstrip(".")
    return f"+{text}" if value > 0 else text


def _scenario_lines(position: dict[str, Any], protection: dict[str, Any]) -> list[str]:
    """Estimated PnL at each protective level, calibrated by the venue's own uPnL.

    Provider quantities are contracts, not guaranteed to equal base units, so the
    per-price slope comes from the venue uPnL instead of an assumed contract size.
    No line is emitted when entry/mark/uPnL are unknown or mark equals entry.
    """
    entry = _decimal(position.get("entry_price"))
    mark = _decimal(position.get("mark_price"))
    upnl = _decimal(position.get("unrealized_pl"))
    if entry is None or mark is None or upnl is None or mark == entry:
        return []
    slope = upnl / (mark - entry)
    sl = protection.get("native_sl") if protection.get("has_sl") else protection.get("fallback_sl")
    tp = protection.get("native_tp") if protection.get("has_tp") else protection.get("fallback_tp")
    if isinstance(tp, str) and "," in tp:
        tp = tp.split(",", 1)[0].strip()
    lines: list[str] = []
    for label, raw in (
        ("At SL", sl),
        ("At TP", tp),
        ("At liq", position.get("liquidation_price")),
    ):
        level = _decimal(raw)
        if level is None:
            continue
        pnl = slope * (level - entry)
        lines.append(f"{label:<10} {_signed_amount(pnl)} USDT (Δ {_signed_amount(pnl - upnl)})")
    return lines


def _position_risk_lines(position: dict[str, Any], protection: dict[str, Any]) -> list[str]:
    mark = position.get("mark_price")
    liquidation = position.get("liquidation_price")
    sl = (
        protection.get("fallback_sl")
        if fallback_is_registered(protection) and not protection.get("has_sl")
        else protection.get("native_sl")
    )
    tp = (
        protection.get("fallback_tp")
        if fallback_is_registered(protection) and not protection.get("has_tp")
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
    lines.extend(_scenario_lines(position, protection))
    return lines


def fmt_pnl(val: Any) -> tuple[str, str]:
    value = _decimal(val)
    if value is None:
        return "➖", "UNKNOWN"
    text = f"{value:,.6f}".rstrip("0").rstrip(".")
    if value > 0:
        return "🟢", f"+{text}"
    if value < 0:
        return "🔴", text
    return "➖", text


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
    return sum(known, Decimal("0")) if len(known) == len(account_values) else None


def _format_timestamp(iso_string: str) -> str:
    if not iso_string:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        jakarta_tz = timezone(timedelta(hours=7))
        return dt.astimezone(jakarta_tz).strftime("%d %b %Y, %H:%M WIB")
    except (TypeError, ValueError):
        return str(iso_string)


def format_report(
    positions: list[dict[str, Any]] | None,
    pending_orders: list[Any] | None,
    sltp: dict[str, Any],
    pnl: dict[str, Any],
    messages: list[Any] | None,
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
    latch_evidence = metrics.get("active_kill_switches", "UNKNOWN")
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
            and account.get("read_status") == "OK"
            and not service_unhealthy
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
    db_position_count = metrics.get("open_positions", "UNKNOWN")
    db_pending_count = metrics.get("pending_orders", "UNKNOWN")
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
        kill_display = f"🔴 ACTIVE · {len(active_latches)} durable latch(es)"

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
        f"<i>Rendered {now} · read-only point-in-time card</i>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"<b>{overall}  SYSTEM</b>",
        "<pre>Runtime    "
        f"{_html(modes.get('mode', '?'))} · Bitget {_html(modes.get('venue_mode', '?'))}\n"
        f"Execution  {_html(execution)} · permission only, not fill evidence\n"
        f"Lifecycle  {_html(lifecycle)}\n"
        f"Dispatcher {_html(services.get('dispatcher_state', 'UNKNOWN'))}\n"
        f"Host probes {_html(service_line)}\n"
        + (
            f"Reason     {_html(services.get('error'), limit=80)}\n"
            if services.get("error")
            else ""
        )
        + f"Services   {_html(service_line)}\n"
        f"Provider   {status_mark(provider_summary)} {_html(provider_summary)}\n"
        f"Reconcile  {status_mark(provider_reconciliation)} {_html(provider_reconciliation)}\n"
        f"Kill switch {_html(kill_display)}</pre>",
        "",
        "",
        "<b>💰 ACCOUNT</b> <code>Bitget "
        f"{_html(account.get('environment', modes.get('venue_mode', 'UNKNOWN')))}</code>",
    ]

    L.append(
        f"<pre>Scope      {_html(account.get('product_type', 'UNKNOWN'))} · "
        f"{_html(account.get('margin_coin', 'UNKNOWN'))}\n"
        f"Source     {_html(account.get('source', 'UNKNOWN'), limit=100)}\n"
        f"Observed   {_html(account.get('observed_at', 'UNKNOWN'), limit=40)}\n"
        f"Read       {_html(account.get('read_status', 'UNKNOWN'))}</pre>"
    )
    if account and account.get("read_status") == "OK":
        account_icon, account_upnl = fmt_pnl(account.get("unrealized_pl", "UNKNOWN"))
        L.append(
            "<pre>Equity     "
            f"{_html(fmt_amount(account.get('equity', 'UNKNOWN')))} USDT\n"
            f"Available  {_html(fmt_amount(account.get('available', 'UNKNOWN')))} USDT\n"
            f"Margin used {_html(fmt_amount(committed_margin))} USDT\n"
            f"Open uPnL   {account_icon} {_html(account_upnl)} USDT</pre>"
        )
    else:
        L.append(
            "<pre>UNKNOWN · "
            f"{_html(account.get('error', 'provider account read failed'), limit=80)}"
            "</pre>"
        )
    L.extend(
        [
            "",
            "<b>🛡 SAFETY EVIDENCE</b>",
            f"<pre>Monitor    {_html(metrics.get('monitor_condition', 'UNKNOWN'), limit=80)}\n"
            "Stream now "
            f"{_html(metrics.get('stream_condition', 'UNKNOWN · not probed'), limit=80)}\n"
            f"Recovery   {_html(metrics.get('recovery_readiness', 'UNKNOWN'), limit=90)}\n"
            "Fallback gate "
            f"{_html(metrics.get('fallback_mutations_enabled', 'UNKNOWN'), limit=16)}\n"
            "Admission  Bitget/global/stream latches block entries; no release is performed.\n"
            "Protection Registrations do not prove runtime enforcement.</pre>",
        ]
    )
    for latch in active_latches:
        L.append(
            f"<pre>{_html(latch.get('scope', 'UNKNOWN'), limit=40)}: "
            f"{_html(latch.get('reason', 'UNKNOWN'), limit=80)}\n"
            f"Latched    {_html(latch.get('latched_at', 'UNKNOWN'), limit=40)}\n"
            f"Updated    {_html(latch.get('updated_at', 'UNKNOWN'), limit=40)}\n"
            f"Age        {_html(latch.get('age_seconds', 'UNKNOWN'), limit=24)} sec</pre>"
        )
    if not isinstance(latch_evidence, list):
        L.append("<pre>Durable latches UNKNOWN · ledger read unavailable</pre>")
    streams = metrics.get("stream_evidence")
    if streams:
        for evidence in streams[:1]:
            L.append(
                f"<pre>Stored stream {_html(evidence.get('symbol'), limit=24)} "
                f"{_html(evidence.get('state'), limit=16)}\n"
                f"Last event {_html(evidence.get('last_stream_at'), limit=40)}\n"
                f"Recorded   {_html(evidence.get('updated_at'), limit=40)}</pre>"
            )
        if len(streams) > 1:
            L.append(f"<i>{len(streams) - 1} more stored observations · use /protection.</i>")
    else:
        L.append("<pre>Stored stream evidence UNKNOWN · no observation loaded</pre>")
    L.append("")
    detail_start = len(L)

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
                if protection.get("has_sl") and protection.get("has_tp")
                else "🟡"
                if fallback_is_registered(protection)
                else "🔴"
            )
            native_sl = protection.get("native_sl") if protection.get("has_sl") else "MISSING"
            native_tp = protection.get("native_tp") if protection.get("has_tp") else "MISSING"
            if not protection.get("has_sl") and protection.get("native_sl_id_reported"):
                native_sl = "ID REPORTED · price UNKNOWN"
            if not protection.get("has_tp") and protection.get("native_tp_id_reported"):
                native_tp = "ID REPORTED · price UNKNOWN"
            if fallback_is_registered(protection):
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
            if fallback_is_registered(protection) and (
                str(metrics.get("fallback_mutations_enabled")) == "0"
            ):
                protection_label += " · CANNOT CLOSE (gated)"
                protection_icon = "🔴"
            if protection_label.startswith("MISSING"):
                protection_icon = "🔴"
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

    detail_end = len(L)
    # ── PnL ────────────────────────────────────────────────────────────
    L.append("<b>📊 PNL</b> <code>Bitget · realized + open</code>")
    fill_count = _known_count(pnl.get("fill_n")) if pnl else None
    total_realized = _decimal(pnl.get("total_pnl")) if pnl else None
    fees = _decimal(pnl.get("total_fees")) if pnl else None
    shown_fees = fees if fill_count is not None and fees is not None else "UNKNOWN"
    net_realized = (
        total_realized - fees
        if fill_count is not None and total_realized is not None and fees is not None
        else "UNKNOWN"
    )
    net_icon, net_value = fmt_pnl(net_realized)
    values = [_decimal(p.get("unrealized_pl")) for p in (positions or [])]
    if positions is None or any(value is None for value in values):
        open_upnl = "UNKNOWN"
    else:
        open_icon, open_value = fmt_pnl(sum((v for v in values if v is not None), Decimal("0")))
        open_upnl = f"{open_icon} {open_value}"
    L.append(
        "<pre>Fills        "
        f"{_html(fill_count if fill_count is not None else 'UNKNOWN')}\n"
        f"Gross profit {_html(fmt_amount(pnl.get('gross_profit', 'UNKNOWN')))} USDT\n"
        f"Gross loss   {_html(fmt_amount(pnl.get('gross_loss', 'UNKNOWN')))} USDT\n"
        f"Fees paid    {_html(fmt_amount(shown_fees))} USDT\n"
        f"Net realized {net_icon} {_html(net_value)} USDT\n"
        f"Open uPnL    {open_upnl} USDT</pre>"
    )
    L.append("")

    # ── Ledger and reconciliation ──────────────────────────────────────
    provider_pending_value = pending_count
    position_recon_icon = (
        "✅"
        if _known_count(provider_count) is not None
        and _known_count(provider_count) == _known_count(db_position_count)
        else "⚠️"
    )
    pending_recon_icon = (
        "✅"
        if _known_count(provider_pending_value) is not None
        and _known_count(provider_pending_value) == _known_count(db_pending_count)
        else "⚠️"
    )
    L.append("<b>🗄 LEDGER &amp; RECONCILIATION</b>")
    L.append(
        "<pre>Messages       "
        f"{_html(metrics.get('messages', 'UNKNOWN'))}\n"
        f"Signals        {_html(metrics.get('signals', 'UNKNOWN'))}\n"
        f"Positions      DB {_html(db_position_count)} · "
        f"provider {_html(provider_count)} {position_recon_icon}\n"
        f"Pending orders DB {_html(db_pending_count)} · "
        f"provider {_html(provider_pending_value)} {pending_recon_icon}\n"
        f"Orders total   {_html(metrics.get('total_orders', 'UNKNOWN'))}\n"
        f"Active intents {_html(metrics.get('active_intents', 'UNKNOWN'))}\n"
        f"Reservations   {_html(metrics.get('reservation_totals', 'UNKNOWN'))}\n"
        f"Last admission balance evidence\n"
        f"Captured       {_html(metrics.get('last_admission_captured_at', 'UNKNOWN'))}\n"
        f"Age            {_html(metrics.get('last_admission_age_seconds', 'UNKNOWN'))} sec\n"
        f"Post-fill      {_html(metrics.get('latest_post_fill_reconciliation', 'UNKNOWN'))}\n"
        f"Fallback rows  {_html(metrics.get('fallback_positions', 'UNKNOWN'))} registered</pre>"
    )
    L.append("<i>Signals/analyzer output are not provider fills; triggers are not execution.</i>")
    L.append("<i>At SL/TP/liq is an estimate from the venue uPnL slope; fees excluded.</i>")
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
        unknown_stops = []
        for position in positions:
            protection = sltp.get(position["symbol"], {})
            if protection.get("has_sl"):
                continue
            stop = _decimal(protection.get("fallback_sl"))
            if fallback_is_registered(protection) and stop is not None and stop > 0:
                monitored.append(position["symbol"])
            elif (
                protection.get("native_sl_id_reported")
                or protection.get("fallback_read_status") == "UNKNOWN"
            ):
                unknown_stops.append(position["symbol"])
            else:
                missing.append(position["symbol"])
        if missing:
            guard = f"🔴 MISSING {', '.join(missing)}"
        elif unknown_stops:
            guard = f"UNKNOWN loss-stop level/registration {', '.join(unknown_stops)}"
        elif monitored:
            guard = f"⚠️ FALLBACK REGISTERED {', '.join(monitored)} · enforcement UNKNOWN"
            if str(metrics.get("fallback_mutations_enabled")) == "0":
                guard += " · CANNOT CLOSE (gated)"
        else:
            guard = "NATIVE REGISTERED · enforcement unprobed"
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
    elif messages is None:
        L.append("<b>🛰 LATEST SIGNAL</b>\n<i>UNKNOWN · message read unavailable.</i>")
    else:
        L.append("<b>🛰 LATEST SIGNAL</b>\n<i>No source message stored yet.</i>")
    L.extend(
        [
            "",
            f"<b>🤖 CODEX QUOTA</b> <code>{_html(codex.get('status', 'UNKNOWN'), limit=24)} · "
            f"{_html(codex.get('plan', 'UNKNOWN'), limit=24)}</code>",
            f"<pre>5h {_html(codex.get('5h'), limit=32)} · 7d {_html(codex.get('7d'), limit=32)}\n"
            f"Reset   {_html(codex.get('reset'), limit=40)}\n"
            f"Updated {_html(codex.get('refreshed'), limit=40)}\n"
            f"Reason  {_html(codex.get('error', 'N/A'), limit=80)}</pre>",
        ]
    )
    # Financial/safety summaries and the latest stored message take precedence over
    # verbose position/order details. Bound optional sections as whole HTML blocks.
    essentials = L[:detail_start] + L[detail_end:]
    details = L[detail_start:detail_end]
    report = "\n".join(essentials + details)
    if len(report.encode("utf-16-le")) // 2 > 3900:
        report = "\n".join(essentials) + (
            f"\n<i>Position/order details omitted · provider {provider_count} positions, "
            f"{pending_count} orders. Use /positions and /orders.</i>"
        )
    return bounded_html(report)
