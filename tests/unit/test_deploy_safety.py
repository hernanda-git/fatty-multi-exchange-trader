"""Deploy gate and missed-signal accounting: a rollout may cost nothing silently."""

from __future__ import annotations

import pytest

from fatty_trader.ops.deploy_safety import (
    close_maintenance_window,
    evaluate_deploy_gate,
    open_maintenance_window,
    sweep_missed_signals,
)

_GATE_FIELDS = (
    "in_flight_dispatches",
    "open_entry_intents",
    "open_reservations",
    "recent_messages",
    "open_entry_windows",
)


def _gate_row(**overrides: str) -> tuple[str, ...]:
    values = dict.fromkeys(_GATE_FIELDS, "0")
    values.update(overrides)
    return tuple(values[field] for field in _GATE_FIELDS)


def test_gate_is_open_only_on_clean_evidence() -> None:
    verdict = evaluate_deploy_gate(lambda _sql: _gate_row(), quiet_minutes=5)

    assert verdict["allowed"] is True
    assert verdict["blocked_by"] == []
    assert verdict["evidence"]["quiet_minutes"] == 5


@pytest.mark.parametrize("field", _GATE_FIELDS)
def test_gate_blocks_on_every_busy_fact(field: str) -> None:
    verdict = evaluate_deploy_gate(lambda _sql: _gate_row(**{field: "1"}), quiet_minutes=5)

    assert verdict["allowed"] is False
    assert verdict["blocked_by"] == [field]


@pytest.mark.parametrize(
    "reader",
    [
        lambda _sql: None,
        lambda _sql: ("0", "0"),
        lambda _sql: (_ for _ in ()).throw(TimeoutError("offline")),
    ],
    ids=["missing-row", "short-row", "unreadable"],
)
def test_gate_never_opens_without_evidence(reader) -> None:
    verdict = evaluate_deploy_gate(reader, quiet_minutes=5)

    assert verdict["allowed"] is False
    assert verdict["blocked_by"]


def test_gate_rejects_a_negative_quiet_window() -> None:
    with pytest.raises(ValueError):
        evaluate_deploy_gate(lambda _sql: _gate_row(), quiet_minutes=-1)


def test_window_open_and_close_are_idempotent_statements() -> None:
    statements: list[str] = []
    window_id = open_maintenance_window(
        statements.append, revision="abc1234", reason="staged-rollout"
    )

    assert statements[0].startswith("INSERT INTO maintenance_windows")
    assert "abc1234" in statements[0]
    close_maintenance_window(statements.append, window_id)
    assert statements[1].startswith("UPDATE maintenance_windows SET ended_at")
    assert window_id in statements[1]

    with pytest.raises(ValueError):
        close_maintenance_window(statements.append, "not-a-uuid")


def test_sweep_records_each_missed_signal_once_and_notifies() -> None:
    window = "33333333-3333-3333-3333-333333333333"
    rows = [
        ("11111111-1111-1111-1111-111111111111", 16330, "2026-10-18 02:00:00+00", None, window),
        ("22222222-2222-2222-2222-222222222222", 16331, "2026-10-18 02:01:00+00", None, window),
    ]
    statements: list[str] = []
    recorded = sweep_missed_signals(lambda _sql: rows, statements.append, window_id=window)

    assert [row["message_id"] for row in recorded] == [16330, 16331]
    inserts = [sql for sql in statements if sql.startswith("INSERT INTO missed_signals")]
    assert len(inserts) == 2
    outbox = [sql for sql in statements if "notifications_outbox" in sql]
    assert len(outbox) == 1
    assert f"missed-signal-window:{window}" in outbox[0]
    assert '"count": 2' in outbox[0]

    # A second sweep over the same window finds nothing new to record.
    assert sweep_missed_signals(lambda _sql: [], statements.append, window_id=window) == []


def test_sweep_requires_a_validated_window_id() -> None:
    with pytest.raises(ValueError):
        sweep_missed_signals(lambda _sql: [], lambda _sql: None, window_id="' OR 1=1 --")
