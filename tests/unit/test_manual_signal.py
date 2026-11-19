"""Manual instructions must become ordinary dispatches, never venue orders."""

import argparse
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "manual_signal.py"
SPEC = importlib.util.spec_from_file_location("manual_signal", SCRIPT)
manual = importlib.util.module_from_spec(SPEC)
sys.modules["manual_signal"] = manual
SPEC.loader.exec_module(manual)

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


def _args(**overrides):
    values = {
        "action": "enter",
        "symbol": "SOLUSDT",
        "direction": "LONG",
        "entry": "132.5",
        "stop": "128.1",
        "target": ["142"],
        "order_type": "MARKET",
        "expires_minutes": 5,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_long_instruction_is_accepted_and_planned():
    instruction = manual.parse_instruction(_args())

    assert instruction.symbol == "SOLUSDT"
    assert len(instruction.revision) == 64
    plan = instruction.plan()
    # risk 4.4, reward 9.5 -> ~2.16R
    assert plan["risk_per_unit"] == "4.4"
    assert plan["reward_risk"][0].startswith("2.15")


@pytest.mark.parametrize(
    "overrides",
    [
        {"symbol": "SOL"},
        {"symbol": "SOLUSDTX"},
        {"direction": "SIDEWAYS"},
        {"stop": "133"},          # LONG stop above entry
        {"target": ["120"]},      # LONG target below entry
        {"target": []},           # no target
        {"entry": "0"},
        {"expires_minutes": 60},
    ],
)
def test_invalid_instructions_are_refused(overrides):
    with pytest.raises(ValueError):
        manual.parse_instruction(_args(**overrides))


def test_short_instruction_measures_reward_downward():
    instruction = manual.parse_instruction(
        _args(direction="SHORT", entry="100", stop="104", target=["90"])
    )

    plan = instruction.plan()
    assert plan["risk_per_unit"] == "4"
    assert plan["reward_risk"][0] == "2.5"


def test_same_instruction_has_a_stable_revision_and_text_changes_it():
    first = manual.parse_instruction(_args())
    second = manual.parse_instruction(_args())
    changed = manual.parse_instruction(_args(target=["143"]))

    assert first.revision == second.revision
    assert first.revision != changed.revision
    assert "TRADE ENTRY: 132.5" in first.text
    assert "STOPLOSS: 128.1" in first.text


class _Cursor:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []

    def execute(self, statement, params=None):
        self.executed.append((" ".join(str(statement).split()), params))

    def fetchone(self):
        return self.results.pop(0) if self.results else None


def test_entry_writes_a_signal_and_dispatch_never_a_venue_order():
    message_id, dispatch_id = uuid4(), uuid4()
    cursor = _Cursor([(message_id,), (dispatch_id,)])
    instruction = manual.parse_instruction(_args())

    result = manual.write_instruction(cursor, instruction, now=NOW)

    statements = "\n".join(statement for statement, _ in cursor.executed)
    assert "INSERT INTO telegram_messages" in statements
    assert "'ANALYZED'" in statements  # the analyzer must never re-read it
    assert "INSERT INTO canonical_signals" in statements
    assert "INSERT INTO dispatches" in statements
    assert "INSERT INTO source_management_updates" not in statements
    assert "ORDER" not in statements.upper().replace("ORDER_TYPE", "")
    assert result == {"message_id": str(message_id), "dispatch_id": str(dispatch_id)}

    signal_params = next(
        params
        for statement, params in cursor.executed
        if "INSERT INTO canonical_signals" in statement
    )
    assert signal_params[3] == "SOL"
    assert signal_params[4] == "LONG"
    assert signal_params[5] == Decimal("132.5")
    assert signal_params[7] == '["142"]'


def test_message_expiry_matches_the_requested_window():
    cursor = _Cursor([(uuid4(),), (uuid4(),)])
    instruction = manual.parse_instruction(_args(expires_minutes=3))

    manual.write_instruction(cursor, instruction, now=NOW)

    values = cursor.executed[0][1]
    assert values["entry_expires_at"] == NOW + timedelta(minutes=3)
    assert values["channel_id"] == manual.MANUAL_CHANNEL_ID
    assert values["revision_hash"] == instruction.revision


def test_close_goes_through_the_management_lane():
    cursor = _Cursor([(uuid4(),)])
    instruction = manual.parse_instruction(_args(action="close", direction=None, entry=None,
                                                 stop=None, target=None))

    manual.write_instruction(cursor, instruction, now=NOW)

    statements = "\n".join(statement for statement, _ in cursor.executed)
    assert "INSERT INTO source_management_updates" in statements
    assert "INSERT INTO dispatches" not in statements
    params = next(
        params
        for statement, params in cursor.executed
        if "INSERT INTO source_management_updates" in statement
    )
    assert params[3] == "SOLUSDT"
