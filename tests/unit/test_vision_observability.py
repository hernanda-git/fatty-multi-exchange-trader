"""Vision must be configurable and visible when it fails."""

from types import SimpleNamespace

import pytest

from fatty_trader.analyzer import codex_runner
from fatty_trader.analyzer.codex_runner import CodexRunner
from fatty_trader.analyzer.postgres_worker import _alert_vision_failure
from fatty_trader.service import build_codex_runner


def test_runner_reads_model_reasoning_and_timeout_from_env():
    runner = build_codex_runner(
        {
            "CODEX_HOME": "/tmp/codex",
            "CODEX_MODEL": "model-from-env",
            "CODEX_REASONING_EFFORT": "high",
            "CODEX_TIMEOUT_SECONDS": "45",
        }
    )

    assert runner._config.model == "model-from-env"
    assert runner._config.reasoning_effort == "high"
    assert runner._config.timeout_seconds == 45.0


def test_runner_defaults_leave_headroom_over_measured_latency():
    runner = build_codex_runner({"CODEX_HOME": "/tmp/codex"})

    # A real chart took 9-11 s; 30 s was too tight to survive a slow day.
    assert runner._config.timeout_seconds == 60.0


def test_probe_auth_reports_ok_and_expired_without_running_codex(monkeypatch):
    runner = CodexRunner(auth_env={"CODEX_HOME": "/tmp/codex"})

    monkeypatch.setattr(
        codex_runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="Logged in using ChatGPT", stderr=""
        ),
    )
    assert runner.probe_auth() == "ok"

    monkeypatch.setattr(
        codex_runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="Your refresh token was already used"
        ),
    )
    assert runner.probe_auth() == "expired"


class _Cursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))


def test_vision_failure_alerts_once_per_hour_not_once_per_message():
    cursor = _Cursor()

    _alert_vision_failure(cursor, 16553, "Image analysis unavailable")

    assert cursor.calls[0][0] == "SAVEPOINT vision_alert"
    insert_sql, insert_params = cursor.calls[1]
    assert "ON CONFLICT (dedup_key) DO NOTHING" in insert_sql
    assert insert_params[1].startswith("vision-failed:")
    assert cursor.calls[-1][0] == "RELEASE SAVEPOINT vision_alert"


def test_vision_alert_failure_never_poisons_the_transaction():
    class Broken(_Cursor):
        def execute(self, sql, params=None):
            if sql.startswith("INSERT"):
                raise RuntimeError("outbox unavailable")
            self.calls.append((sql, params))

    cursor = Broken()

    _alert_vision_failure(cursor, 1, "Image analysis returned invalid JSON")

    assert (cursor.calls[-1][0]) == "ROLLBACK TO SAVEPOINT vision_alert"
