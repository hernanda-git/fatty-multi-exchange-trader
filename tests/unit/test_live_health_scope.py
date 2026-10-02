"""LIVE operational reporting must not disguise a non-LIVE runtime."""

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from fatty_trader.operator.health import build_operator_health_report
from fatty_trader.operator.health_report_format import format_report

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "live_health_report", ROOT / "scripts/health_report.py"
)
assert spec is not None and spec.loader is not None
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def render(mode, venue_mode):
    return format_report(
        [],
        [],
        {},
        {},
        [],
        {},
        {},
        {"mode": mode, "venue_mode": venue_mode, "execution_enabled": "0"},
        {"status": "LIVE"},
        {"status": "OK"},
    )


def test_live_health_title_requires_both_runtime_modes_live():
    assert "LIVE HEALTH" in render("LIVE", "LIVE")
    for mode, venue in [
        ("LIVE", "DEMO"),
        ("DEMO", "LIVE"),
        ("PAPER", "PAPER"),
        ("UNKNOWN", "UNKNOWN"),
    ]:
        report = render(mode, venue)
        assert "LIVE HEALTH" not in report
        assert "DEGRADED" in report
        assert f"{mode} · Bitget {venue}" in report
        assert "NON-LIVE / UNKNOWN HEALTH" in report
        assert f"Bitget {venue}</code>" in report


@pytest.mark.parametrize(
    "extra,expected_unexpected",
    [
        ("paper-kaka|exited|\nmonitor-binance|exited|\n", 0),
        ("paper-kaka|running|healthy\n", 1),
        ("dispatcher-demo|running|healthy\n", 1),
    ],
)
def test_service_scope_excludes_only_inactive_disabled_lanes(
    monkeypatch, extra, expected_unexpected
):
    config = {
        "services": {
            "dispatcher-bitget": {},
            "intake": {},
            "paper-kaka": {},
            "monitor-binance": {"profiles": ["binance-disabled"]},
            "init": {},
            "migrate": {},
        }
    }
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(config)
            if "config" in cmd
            else "dispatcher-bitget|running|healthy\nintake|exited|\n"
            "init|exited|\nmigrate|exited|\n" + extra,
        )

    monkeypatch.setattr(script.subprocess, "run", run)
    status = script.get_service_status()
    assert status["total"] == 2 + expected_unexpected
    assert status["running"] == 1 + expected_unexpected
    assert status["unexpected_runtime"] == expected_unexpected
    assert "--all" in calls[0]
    report = format_report(
        [],
        [],
        {},
        {},
        [],
        {},
        {},
        {"mode": "LIVE", "venue_mode": "LIVE"},
        {"status": "LIVE"},
        status,
    )
    assert "DEGRADED" in report


def test_missing_live_service_is_counted_not_silently_healthy(monkeypatch):
    def run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"services": {"intake": {}}})
            if "config" in cmd
            else "paper-kaka|exited|\n",
        )

    monkeypatch.setattr(script.subprocess, "run", run)
    status = script.get_service_status()
    assert status["total"] == 1
    assert status["running"] == 0


def test_unavailable_compose_evidence_is_unknown(monkeypatch):
    monkeypatch.setattr(
        script.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout="")
    )
    assert script.get_service_status()["status"] == "UNKNOWN"


@pytest.mark.parametrize("mode,venue", [("LIVE", "LIVE"), ("LIVE", "DEMO"), ("PAPER", "PAPER")])
def test_on_demand_policy_preserves_reported_venue(mode, venue):
    class Gateway:
        def get_account_snapshot(self):
            return {}

        def get_positions(self):
            return []

        def get_orders(self):
            return []

    def unavailable_db():
        raise OSError("offline")

    text = build_operator_health_report(
        Gateway(), unavailable_db, mode=mode, venue_mode=venue, execution_enabled=False
    )
    assert f"venue <code>{venue}</code>" in text
    assert ("✅ LIVE-only runtime" in text) == (mode == venue == "LIVE")
    assert ("DEGRADED" in text) == (mode != "LIVE" or venue != "LIVE")


@pytest.mark.parametrize(
    "modes,title", [("LIVE|LIVE|0", "LIVE HEALTH"), ("LIVE|DEMO|0", "NON-LIVE / UNKNOWN HEALTH")]
)
def test_legacy_shell_scope_and_mode_are_executed_offline(modes, title):
    text = (ROOT / "scripts/telegram_health_report.sh").read_text()
    assert ".env.bitget-demo" not in text
    assert "bitget_demo_telemetry.py" not in text
    assert "DEMO Provider Read-back" not in text
    segment = text[text.index("# Include stopped LIVE workers") : text.index("intake_id=")]
    prelude = """set -euo pipefail
    overall='ONLINE'
    docker() {
      if [[ "$*" == *'ps --all'* ]]; then
        printf '%s\n' 'dispatcher-bitget|running|healthy' 'paper-kaka|exited|'
        printf '%s\n' 'monitor-binance|exited|' 'init|exited|'
      else
        printf '%s' "$TEST_MODES"
      fi
    }
    """
    result = subprocess.run(
        [
            "bash",
            "-c",
            prelude
            + segment
            + 'printf "TITLE=%s\nSERVICES=%s\nOVERALL=%s" "$health_title" "$services" "$overall"',
        ],
        env={"PATH": "/usr/bin:/bin", "TEST_MODES": modes},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "TITLE=" + title in result.stdout
    assert "paper-kaka" not in result.stdout
    assert "monitor-binance" not in result.stdout
    if "DEMO" in modes:
        assert "DEGRADED" in result.stdout
