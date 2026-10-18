"""Deployment-safety gates and signal accounting for the live lane.

Two guarantees, both observability-only — no trading path reads this module:

* :func:`evaluate_deploy_gate` refuses a rollout while the lane is busy (an
  in-flight dispatch, an open entry intent or reservation, a signal still inside
  its entry window, or a message that just arrived), so a maintenance window can
  never straddle a live trade.
* :func:`sweep_missed_signals` records every channel message that lapsed while a
  recorded maintenance window was open and produced no canonical signal, then
  enqueues one notification per window. A missed signal becomes visible instead
  of silently expiring.

Adapters receive plain SQL callables, matching the rest of the ops surface
(``query_one(sql)``, ``query_all(sql)``, ``execute(sql)``); every value that is
interpolated is validated first, never caller-supplied free text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from uuid import uuid4

# Terminal dispatch states: anything else is still work in flight.
_TERMINAL_DISPATCH_STATES = (
    "FILLED",
    "REJECTED",
    "EXPIRED",
    "CANCELLED",
    "RECONCILED",
    "FAILED",
    "CLOSED",
)
_ID_PATTERN = re.compile(r"^[0-9a-fA-F-]{36}$")

_GATE_SQL = """
SELECT
  (SELECT count(*) FROM dispatches
    WHERE state NOT IN ({terminal}) OR lease_until > clock_timestamp())
    AS in_flight_dispatches,
  (SELECT count(*) FROM live_order_intents i
    WHERE i.exchange='bitget' AND i.role='ENTRY'
      AND NOT EXISTS (SELECT 1 FROM bitget_baseline_records b
          WHERE b.record_kind='intent' AND b.record_id=i.id)
      AND (i.filled_qty > 0 OR i.state NOT IN ('rejected','cancelled','reconciled')))
    AS open_entry_intents,
  (SELECT count(*) FROM bitget_margin_reservations m
    WHERE m.exchange='bitget' AND m.state IN ('reserved','unknown','consumed')
      AND NOT EXISTS (SELECT 1 FROM bitget_baseline_records b
          WHERE b.record_kind='reservation' AND b.record_id=m.id))
    AS open_reservations,
  (SELECT count(*) FROM telegram_messages
    WHERE received_at > clock_timestamp() - interval '{quiet} minutes')
    AS recent_messages,
  (SELECT count(*) FROM telegram_messages
    WHERE entry_expires_at IS NOT NULL AND entry_expires_at > clock_timestamp())
    AS open_entry_windows
"""

_GATE_FIELDS = (
    "in_flight_dispatches",
    "open_entry_intents",
    "open_reservations",
    "recent_messages",
    "open_entry_windows",
)


def _quoted(value: str) -> str:
    """Return a single-quoted SQL literal for an already-validated value."""
    return "'" + value.replace("'", "''") + "'"


def evaluate_deploy_gate(
    query_one: Callable[[str], Any], *, quiet_minutes: int = 5
) -> dict[str, Any]:
    """Return ``{"allowed", "blocked_by", "evidence"}`` for a rollout.

    Missing or unreadable evidence blocks the deploy: a gate that cannot see the
    lane is not permission to stop it.
    """
    if quiet_minutes < 0:
        raise ValueError("quiet_minutes must not be negative")
    terminal = ", ".join(_quoted(state) for state in _TERMINAL_DISPATCH_STATES)
    try:
        row = query_one(_GATE_SQL.format(terminal=terminal, quiet=max(0, quiet_minutes)))
        if row is None or len(row) != len(_GATE_FIELDS):
            raise ValueError("deploy gate read is incomplete")
        evidence = dict(zip(_GATE_FIELDS, (int(value) for value in row), strict=True))
    except Exception as exc:  # noqa: BLE001 - unreadable evidence is not permission
        return {
            "allowed": False,
            "blocked_by": [f"gate-read-failed:{type(exc).__name__}"],
            "evidence": {"quiet_minutes": quiet_minutes},
        }
    blocked_by = [name for name, count in evidence.items() if count]
    return {
        "allowed": not blocked_by,
        "blocked_by": blocked_by,
        "evidence": {"quiet_minutes": quiet_minutes, **evidence},
    }


def open_maintenance_window(
    execute: Callable[[str], Any],
    *,
    revision: str,
    reason: str,
    opened_by: str = "rollout",
) -> str:
    """Record an open window before anything is stopped; returns its id."""
    window_id = str(uuid4())
    execute(
        "INSERT INTO maintenance_windows (id, revision, reason, opened_by) VALUES ("
        f"{_quoted(window_id)}, {_quoted(revision)}, {_quoted(reason)}, {_quoted(opened_by)})"
    )
    return window_id


def close_maintenance_window(execute: Callable[[str], Any], window_id: str) -> None:
    if not _ID_PATTERN.fullmatch(window_id):
        raise ValueError("maintenance window id is invalid")
    execute(
        "UPDATE maintenance_windows SET ended_at = clock_timestamp() "
        f"WHERE id = {_quoted(window_id)}"
    )


def open_windows_sql() -> str:
    """SQL listing open windows, so a crashed rollout is visible next run."""
    return (
        "SELECT id, started_at, revision, reason FROM maintenance_windows "
        "WHERE ended_at IS NULL ORDER BY started_at DESC"
    )


def sweep_missed_signals(
    query_all: Callable[[str], Any],
    execute: Callable[[str], Any],
    *,
    window_id: str | None = None,
) -> list[dict[str, Any]]:
    """Record channel messages lost inside a maintenance window, once each.

    A message counts as missed when it arrived (or its entry window lapsed) inside
    a recorded window and no canonical signal was ever produced from it. Recording
    is idempotent per message; one notification per window tells the owner what to
    review manually.
    """
    window_filter = ""
    if window_id is not None:
        if not _ID_PATTERN.fullmatch(window_id):
            raise ValueError("maintenance window id is invalid")
        window_filter = f"AND w.id = {_quoted(window_id)}"
    rows = query_all(
        f"""
        SELECT tm.id::text, tm.message_id, tm.received_at, tm.entry_expires_at, w.id
        FROM telegram_messages tm
        JOIN maintenance_windows w
          ON (tm.received_at BETWEEN w.started_at AND coalesce(w.ended_at, clock_timestamp())
              OR (tm.entry_expires_at IS NOT NULL
                  AND tm.entry_expires_at BETWEEN w.started_at
                      AND coalesce(w.ended_at, clock_timestamp())))
        WHERE NOT EXISTS (SELECT 1 FROM canonical_signals cs WHERE cs.message_id = tm.id)
          AND NOT EXISTS (SELECT 1 FROM missed_signals ms WHERE ms.message_id = tm.id)
          {window_filter}
        ORDER BY tm.received_at
        """
    )
    recorded: list[dict[str, Any]] = []
    for row in rows:
        message_uuid, message_ref, received_at, expires_at, matched_window = tuple(row)
        execute(
            "INSERT INTO missed_signals (message_id, window_id, message_ref, received_at,"
            " entry_expires_at, reason) VALUES ("
            f"{_quoted(str(message_uuid))}, {_quoted(str(matched_window))}, {int(message_ref)}, "
            f"{_quoted(str(received_at)) if received_at else 'NULL'}, "
            f"{_quoted(str(expires_at)) if expires_at else 'NULL'}, "
            "'maintenance-window') ON CONFLICT (message_id) DO NOTHING"
        )
        recorded.append(
            {
                "message_id": message_ref,
                "window_id": str(matched_window),
                "received_at": str(received_at) if received_at else None,
                "entry_expires_at": str(expires_at) if expires_at else None,
            }
        )
    if recorded:
        first_window = recorded[0]["window_id"]
        payload = _notification_payload(recorded)
        execute(
            "INSERT INTO notifications_outbox (id, dedup_key, payload) VALUES ("
            f"{_quoted(str(uuid4()))}, {_quoted(f'missed-signal-window:{first_window}')}, "
            f"$json${payload}$json$::jsonb) ON CONFLICT (dedup_key) DO NOTHING"
        )
    return recorded


def _notification_payload(recorded: list[dict[str, Any]]) -> str:
    refs = ", ".join(f"#{row['message_id']}" for row in recorded[:10])
    suffix = "" if len(recorded) <= 10 else f" (+{len(recorded) - 10} more)"
    return json.dumps(
        {
            "kind": "missed-signal",
            "count": len(recorded),
            "messages": refs + suffix,
            "summary": (
                f"{len(recorded)} channel message(s) lapsed during a maintenance window "
                "and produced no signal; review them manually."
            ),
        },
        sort_keys=True,
    )
