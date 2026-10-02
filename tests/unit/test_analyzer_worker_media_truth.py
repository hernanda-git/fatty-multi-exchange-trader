"""Exercise the real worker/image parser through fake model results, never providers."""

from __future__ import annotations

import json

import pytest
from test_analyzer_worker import Connection, media_message_cursor

from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.postgres_worker import process_received_batch

CAPTION = "#WLD LONG ENTRY: 1 TARGET: 1.1 STOPLOSS: 0.95"
SETUP = {
    "action": "ENTER",
    "asset": "WLD",
    "side": "LONG",
    "entry": "1",
    "stop_loss": "0.95",
    "take_profits": ["1.1"],
}


def run_worker(tmp_path, caption, model_result):
    image = tmp_path / "chart.png"
    image.write_bytes(b"fake image; model runner does not read this")
    cursor = media_message_cursor(media_path=str(image))
    cursor.rows[0] = (*cursor.rows[0][:4], caption, *cursor.rows[0][5:])
    calls = []

    def runner(prompt, image_paths):
        calls.append((prompt, image_paths))
        if isinstance(model_result, Exception):
            raise model_result
        return model_result

    assert (
        process_received_batch(
            lambda: Connection(cursor),
            runner=runner,
            image_analysis_enabled=True,
            exchanges=("bitget",),
        )
        == 1
    )
    assert len(calls) == 1
    return cursor


def payload(cursor):
    return json.loads(
        next(
            params[2]
            for statement, params in cursor.executed
            if "INSERT INTO notifications_outbox" in statement
        )
    )


def writes(cursor, table):
    return [params for statement, params in cursor.executed if f"INSERT INTO {table}" in statement]


@pytest.mark.parametrize(
    "result",
    [
        CodexRunResult(False, True, False, 1, "timeout", "", ""),
        CodexRunResult(True, False, False, 0, None, "not JSON", ""),
        OSError("image runner unavailable"),
    ],
)
def test_failed_image_preserves_explicit_text_and_truthful_notification(tmp_path, result):
    cursor = run_worker(tmp_path, CAPTION, result)
    assert len(writes(cursor, "canonical_signals")) == 1
    assert len(writes(cursor, "dispatches")) == 1
    note = payload(cursor)
    assert note["status"] == "FALLBACK_ACCEPTED"
    assert note["failure_class"] != "none"
    assert note["canonical_signal"] is True
    assert note["dispatches"] == 1
    assert note["image_analysis_status"] == "FAILED"


def test_failed_chart_only_abstains_and_reports_review(tmp_path):
    cursor = run_worker(tmp_path, "", CodexRunResult(False, True, False, 1, "timeout", "", ""))
    assert not writes(cursor, "canonical_signals")
    assert not writes(cursor, "source_management_updates")
    note = payload(cursor)
    assert note["status"] == "MANUAL_REVIEW"
    assert note["image_analysis_status"] == "FAILED"
    assert note["dispatches"] == 0


@pytest.mark.parametrize(
    "action,caption",
    [
        ("ENTER", CAPTION + " wait for confirmation"),
        ("ENTER", CAPTION + " do not enter"),
        ("CLOSE", "#WLD wait, do not close all position"),
        ("CLOSE", "#WLD do not close all position"),
        ("CLOSE", "#WLD don't book TP1; don't move SL to entry"),
    ],
)
def test_original_caption_veto_cannot_be_erased_by_image_summary(tmp_path, action, caption):
    setup = {**SETUP, "action": action}
    result = CodexRunResult(
        True, False, False, 0, None, json.dumps({"message": "Confirmed trade", "setup": setup}), ""
    )
    cursor = run_worker(tmp_path, caption, result)
    assert not writes(cursor, "canonical_signals")
    assert not writes(cursor, "source_management_updates")
    assert not writes(cursor, "dispatches")
    assert payload(cursor)["canonical_signal"] is False
    assert payload(cursor)["management_action"] is None


def test_successful_image_reports_actual_fanout_and_keeps_summary(tmp_path):
    result = CodexRunResult(
        True, False, False, 0, None, json.dumps({"message": "Confirmed trade", "setup": SETUP}), ""
    )
    cursor = run_worker(tmp_path, CAPTION, result)
    assert len(writes(cursor, "dispatches")) == 1
    note = payload(cursor)
    assert note["message"] == "Confirmed trade"
    assert note["status"] == "CODEX_SUCCEEDED"
    assert note["image_analysis_status"] == "SUCCEEDED"
    assert note["dispatches"] == 1
