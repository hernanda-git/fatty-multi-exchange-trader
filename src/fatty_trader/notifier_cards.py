"""Premium, English Telegram cards for execution events.

Pure functions: no I/O, no provider access. Numbers are escaped; the caller bounds length.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any

RULE = "\u2500" * 30
WIB = timezone(timedelta(hours=7))

REASON_TEXT: dict[str, str] = {
    "protection-stream-latched": "protection stream down \u00b7 safety latch",
    "protection-stream-stale": "protection stream stale \u00b7 no recent ticks",
    "position-cap-exceeded": "position slots full \u00b7 5/5 in use",
    "stale-source-message": "signal expired \u00b7 source too old",
    "source-freshness-expired": "signal expired \u00b7 entry window closed",
    "recovery-closed-close-fill-verified": "verified against venue fill",
    "recovery-protection-verified": "protection verified at the venue",
    "cutover-gated": "execution gate closed \u00b7 no order sent",
    "missing-execution-client": "execution client unavailable",
    "entry-routing-unsupported": "routing unsupported by execution client",
    "approved-provider-flat-readback-no-order-or-intent": "reconciled \u00b7 venue flat, no order",
}

_STATE_TITLE = {
    "FILLED": "filled",
    "CLOSED": "closed",
    "REJECTED": "not traded",
    "EXPIRED": "expired",
    "CANCELLED": "cancelled",
    "RECONCILED": "reconciled",
}


def human_reason(code: Any) -> str:
    """Translate an internal reason code; never echo a raw enum."""
    text = str(code or "").strip()
    if not text or text == "none":
        return "no reason recorded"
    if text in REASON_TEXT:
        return REASON_TEXT[text]
    for prefix, label in (
        ("recovery-missing-protection:", "recovery blocked \u00b7 missing protection"),
        ("recovery-filled-protection-unverified", "recovery blocked \u00b7 protection unverified"),
        ("entry-routing-error:", "routing error \u00b7 entry refused"),
    ):
        if text.startswith(prefix):
            return label
    return text.replace("_", " ").replace("-", " ")[:80]


def _dec(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _money(value: Any, places: int = 3) -> str:
    parsed = _dec(value)
    return "?" if parsed is None else f"{parsed:,.{places}f}"


def _signed_money(value: Any) -> str:
    parsed = _dec(value)
    if parsed is None:
        return "?"
    sign = "\u2212" if parsed < 0 else "+"
    return sign + f"{abs(parsed):,.3f}"


def _price(value: Any, places: int | None = None) -> str:
    parsed = _dec(value)
    if parsed is None:
        return "?"
    if places is None:
        magnitude = abs(parsed)
        places = 2 if magnitude >= 100 else 4 if magnitude >= 1 else 6
    return f"{parsed:,.{places}f}"


def _percent(value: Any) -> str:
    parsed = _dec(value)
    if parsed is None:
        return "?"
    sign = "\u2212" if parsed < 0 else "+"
    return sign + f"{abs(parsed):.2f}%"


def _r_multiple(value: Any) -> str:
    parsed = _dec(value)
    if parsed is None:
        return "?"
    sign = "\u2212" if parsed < 0 else "+"
    return sign + f"{abs(parsed):.2f}R"


def _wib(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(WIB).strftime("%d %b %H:%M WIB")


def _display_symbol(symbol: str) -> str:
    upper = symbol.upper()
    return upper[:-4] + "/USDT" if upper.endswith("USDT") and len(upper) > 4 else upper


def _header(glyph: str, direction: str, symbol: str, state: str) -> str:
    side = " " + direction if direction else ""
    return f"{glyph} <b>{escape(_display_symbol(symbol))}{escape(side)}</b> \u00b7 <i>{escape(state)}</i>"


def _footer(*parts: str) -> str:
    return " \u00b7 ".join(part for part in parts if part)


def render_execution_event(payload: Mapping[str, Any]) -> str:
    evidence = payload.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    result = payload.get("result")
    result = result if isinstance(result, Mapping) else None
    symbol = str(evidence.get("symbol") or payload.get("symbol") or "unknown")
    direction = str(evidence.get("direction") or "").upper()
    to_state = str(payload.get("to_state") or "").upper()
    reason = str(payload.get("reason") or "")
    dispatch_id = str(payload.get("dispatch_id") or "")[:8]
    source_id = evidence.get("source_message_id")
    stamp = _wib(evidence.get("source_received_at") or evidence.get("observed_at"))
    if reason.startswith(("recovery-missing-protection:", "recovery-filled-protection-unverified")):
        return _render_recovery_blocked(symbol, reason, dispatch_id)
    if reason == "cutover-gated":
        return _render_gated()
    if reason == "approved-provider-flat-readback-no-order-or-intent":
        return _render_reconciled(symbol, dispatch_id)
    if to_state == "CLOSED" or (result and result.get("net") is not None):
        return _render_closed(symbol, direction, reason, result, dispatch_id, source_id, stamp)
    if to_state in {"REJECTED", "EXPIRED", "CANCELLED"}:
        return _render_rejected(symbol, direction, reason, dispatch_id, source_id, stamp)
    if to_state == "FILLED":
        return _render_filled(symbol, direction, evidence, dispatch_id, source_id, stamp)
    return _render_generic(symbol, direction, to_state, reason, dispatch_id)


def _render_closed(symbol, direction, reason, result, dispatch_id, source_id, stamp):
    footer = _footer("#" + escape(str(source_id)) if source_id else "", "dispatch " + escape(dispatch_id), stamp)
    if not result:
        return "\n".join([
            _header("\u26aa", direction, symbol, "closed"),
            RULE,
            "reason  " + escape(human_reason(reason)),
            RULE,
            footer,
        ])
    net = _dec(result.get("net"))
    glyph = "\u25b2" if net is not None and net > 0 else "\u25bc" if net is not None and net < 0 else "\u25cf"
    hero = "   " + glyph + " " + _signed_money(result.get("net")) + "   " + _percent(result.get("net_pct")) + "   " + _r_multiple(result.get("r"))
    return "\n".join([
        _header("\U0001f535" if glyph == "\u25b2" else "\U0001f534", direction, symbol, "closed \u00b7 " + human_reason(reason)),
        RULE,
        hero,
        RULE,
        "entry  " + _price(result.get("entry")) + "    exit   " + _price(result.get("exit")),
        "gross  " + _signed_money(result.get("gross")) + "   fees   " + _money(result.get("fees")),
        "net    " + _signed_money(result.get("net")) + "   held   " + escape(str(result.get("held") or "?")),
        RULE,
        footer,
    ])


def _render_rejected(symbol, direction, reason, dispatch_id, source_id, stamp):
    footer = _footer("#" + escape(str(source_id)) if source_id else "", stamp, "dispatch " + escape(dispatch_id))
    return "\n".join([
        _header("\u26aa", direction, symbol, "not traded"),
        RULE,
        "reason  " + escape(human_reason(reason)),
        footer,
        RULE,
        "<i>no order was sent \u00b7 the signal was skipped</i>",
    ])


def _render_filled(symbol, direction, evidence, dispatch_id, source_id, stamp):
    sizing = evidence.get("sizing")
    sizing = sizing if isinstance(sizing, Mapping) else {}
    quantity = sizing.get("quantity") or evidence.get("filled_quantity") or "?"
    margin = sizing.get("planned_margin_usdt")
    notional = sizing.get("planned_notional_usdt")
    leverage = sizing.get("effective_leverage")
    stop = sizing.get("planned_stop_loss")
    targets = sizing.get("planned_take_profits") or []
    target_text = ", ".join(str(item) for item in targets) if targets else "?"
    head = _header("\U0001f535" if direction == "LONG" else "\U0001f534", direction, symbol, "filled")
    size_line = "size    " + escape(str(quantity)) + (("     notional $" + _money(notional)) if notional else "")
    margin_line = (
        "margin  $" + f"{_dec(margin):.2f}" + " USDT"
        if _dec(margin) is not None
        else "margin  ?"
    )
    if leverage:
        margin_line += "   " + str(leverage) + "x"
    stop_line = "stop    " + _price(stop) + " native    target " + escape(target_text) + " native"
    footer = _footer("Bitget", "#" + escape(str(source_id)) if source_id else "", "dispatch " + escape(dispatch_id), stamp)
    return "\n".join([head, RULE, size_line, margin_line, stop_line, RULE, footer])


def _render_generic(symbol, direction, state, reason, dispatch_id):
    label = _STATE_TITLE.get(state, state.lower() or "update")
    return "\n".join([
        _header("\u26aa", direction, symbol, label),
        RULE,
        "reason  " + escape(human_reason(reason)),
        RULE,
        "dispatch " + escape(dispatch_id),
    ])


def _render_recovery_blocked(symbol: str, reason: str, dispatch_id: str) -> str:
    return "\n".join(
        [
            "\u26d4 <b>RECOVERY BLOCKED</b> \u00b7 <i>historical fill</i>",
            RULE,
            "Not a new entry. The position is a past fill, not a new trade.",
            "Close or protection is not verified; new entries stay blocked.",
            "reason  " + escape(human_reason(reason)),
            RULE,
            "dispatch " + escape(dispatch_id),
        ]
    )


def _render_gated() -> str:
    return "\n".join(
        [
            "\u26d4 <b>EXECUTION BLOCKED</b>",
            RULE,
            "No order was sent.",
            "reason  execution gate closed \u00b7 orders are not permitted",
        ]
    )


def _render_reconciled(symbol: str, dispatch_id: str) -> str:
    return "\n".join(
        [
            "\u26aa <b>RECONCILED</b> \u00b7 <i>venue flat, no order</i>",
            RULE,
            "Provider flat : no position, no open order",
            "Ledger        : no local order or intent",
            "Reservation   : released by the audit transition",
            "Entry         : no entry was repeated",
            RULE,
            "dispatch " + escape(dispatch_id),
        ]
    )
