"""Late-signal re-admission guards: a stale entry is only taken on the right side."""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

from fatty_trader.domain.enums import DispatchState
from fatty_trader.domain.state_machines import TransitionError, transition_dispatch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "requeue_late_signal.py"
SPEC = importlib.util.spec_from_file_location("requeue_late_signal", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
late_signal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(late_signal)


def _guards(**overrides: object) -> tuple[bool, str]:
    values: dict[str, object] = {
        "state": "EXPIRED",
        "attempts": 0,
        "direction": "SHORT",
        "rejection": "stale-source-message",
        "last": Decimal("4.899"),
        "stop_loss": Decimal("5.05"),
        "take_profit": Decimal("4.575"),
    }
    values.update(overrides)
    return late_signal.evaluate_readmission(**values)


def test_late_short_is_re_admittable_while_the_stop_holds() -> None:
    allowed, reason = _guards()

    assert allowed is True
    assert "4.899" in reason


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"state": "FILLED"}, "not re-admittable"),
        ({"state": "QUEUED", "attempts": 1}, "attempt"),
        ({"rejection": "edited-source-message"}, "vetoed"),
        ({"last": None}, "unavailable"),
        ({"last": Decimal("5.06")}, "stop already breached"),
        ({"last": Decimal("4.50")}, "target already reached"),
        ({"direction": "SIDEWAYS"}, "not tradable"),
    ],
    ids=[
        "settled-dispatch",
        "already-attempted",
        "superseded-revision",
        "no-price",
        "stop-breached",
        "target-reached",
        "unknown-direction",
    ],
)
def test_late_entry_is_refused_when_unsafe(overrides: dict[str, object], needle: str) -> None:
    allowed, reason = _guards(**overrides)

    assert allowed is False
    assert needle in reason


def test_long_guards_mirror_the_short_rules() -> None:
    allowed, _ = _guards(
        direction="LONG", last=Decimal("100"), stop_loss=Decimal("95"), take_profit=Decimal("110")
    )
    assert allowed is True
    refused, reason = _guards(
        direction="LONG", last=Decimal("94"), stop_loss=Decimal("95"), take_profit=Decimal("110")
    )
    assert refused is False
    assert "breached" in reason


def test_expiry_only_returns_to_the_queue_via_readmission() -> None:
    assert transition_dispatch(DispatchState.EXPIRED, DispatchState.QUEUED) is DispatchState.QUEUED

    with pytest.raises(TransitionError):
        transition_dispatch(DispatchState.EXPIRED, DispatchState.PREFLIGHT)
