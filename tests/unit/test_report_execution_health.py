"""Admission permission and provider flatness are not execution health evidence."""

import pytest

from fatty_trader.operator.health_report_format import format_report


def render(*, metrics=None, services=None, execution="1"):
    return format_report(
        [],
        [],
        {},
        {},
        [],
        metrics or {"kill_switch": "INACTIVE"},
        {"equity": "9.17026367"},
        {"mode": "LIVE", "venue_mode": "LIVE", "execution_enabled": execution},
        {"status": "LIVE"},
        services or {"status": "OK", "total": 1, "running": 1, "healthy": 1},
    )


@pytest.mark.parametrize("scope", ["bitget", "bitget-protection-stream"])
def test_active_latch_overrides_flat_provider_and_enabled_gate(scope):
    text = render(
        metrics={
            "kill_switch": "ACTIVE" if scope == "bitget" else "INACTIVE",
            "active_kill_switches": [{"scope": scope, "reason": "socket-not-connected"}],
        }
    )
    assert "DEGRADED" in text
    assert "🟢 ONLINE" not in text
    assert scope in text
    assert "socket-not-connected" in text


@pytest.mark.parametrize("execution", ["0", "UNKNOWN"])
def test_live_without_execution_permission_is_not_online(execution):
    assert "DEGRADED" in render(execution=execution)


def test_enabled_gate_explicitly_disclaims_execution_success():
    text = render()
    assert "permission only" in text
    assert "not fill evidence" in text
    assert "Signals/analyzer output are not provider fills" in text


def test_restart_cannot_be_green_even_with_cached_healthy_counts():
    text = render(
        services={
            "status": "OK",
            "total": 1,
            "running": 1,
            "healthy": 1,
            "dispatcher_state": "RESTARTING",
        }
    )
    assert "DEGRADED" in text


def test_lifecycle_blocked_overrides_running_container():
    text = render(
        services={
            "status": "OK",
            "total": 1,
            "running": 1,
            "healthy": 1,
            "lifecycle_recovery": "BLOCKED",
        }
    )
    assert "DEGRADED" in text
    assert "Lifecycle  BLOCKED" in text


def test_cron_reports_dispatcher_restart_and_exact_recovery_failure(monkeypatch):
    from types import SimpleNamespace

    from test_live_health_scope import script

    def run(cmd, **kwargs):
        if "config" in cmd:
            output = '{"services": {"dispatcher-bitget": {}}}'
        elif "logs" in cmd:
            output = "RuntimeError: Bitget lifecycle recovery is not ready"
        else:
            output = "dispatcher-bitget|restarting|healthy\n"
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(script.subprocess, "run", run)
    services = script.get_service_status()
    assert services["dispatcher_state"] == "RESTARTING"
    assert services["lifecycle_recovery"] == "BLOCKED"
    assert services["unhealthy"] == 1
    assert services["healthy"] == 0
    assert "DEGRADED" in render(services=services)


@pytest.mark.parametrize("state,log_error", [("running", False), ("restarting", True)])
def test_recovery_is_not_inferred_from_old_or_unavailable_logs(monkeypatch, state, log_error):
    import subprocess
    from types import SimpleNamespace

    from test_live_health_scope import script

    def run(cmd, **kwargs):
        if "config" in cmd:
            output = '{"services": {"dispatcher-bitget": {}}}'
        elif "logs" in cmd:
            assert log_error, "Running workers must not be diagnosed from historical logs"
            raise subprocess.TimeoutExpired(cmd, 10)
        else:
            output = f"dispatcher-bitget|{state}|healthy\n"
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(script.subprocess, "run", run)
    services = script.get_service_status()
    assert services["lifecycle_recovery"] == "UNKNOWN"
    if state == "restarting":
        assert "DEGRADED" in render(services=services)


def test_cron_loads_all_active_bitget_latch_scopes(monkeypatch):
    from test_live_health_scope import script

    queries = []

    def query(sql):
        queries.append(sql)
        return {
            "col7": "INACTIVE",
            "col11": '[{"scope":"bitget-protection-stream","reason":"socket-not-connected"}]',
        }

    monkeypatch.setattr(script, "query_one", query)
    metrics = script.load_db_metrics()
    assert metrics["active_kill_switches"][0]["scope"] == "bitget-protection-stream"
    assert "active" in queries[0]
    assert "bitget-protection-stream" in queries[0]
    assert "'global'" in queries[0]
    assert "DEGRADED" in render(metrics=metrics)


def test_operator_command_loads_stream_latch_through_shared_reader():
    from test_health_truthfulness import Connection, Cursor, Gateway

    from fatty_trader.operator.health import (
        create_shared_health_reader,
        load_operator_health_snapshot,
    )
    from fatty_trader.operator.live_commands import OperatorCommandService

    class LatchCursor(Cursor):
        def execute(self, sql):
            assert "bitget-protection-stream" in sql
            assert "'global'" in sql

        def fetchone(self):
            return (
                0,
                0,
                0,
                0,
                0,
                0,
                False,
                None,
                [{"scope": "bitget-protection-stream", "reason": "socket-not-connected"}],
            )

    class LatchConnection(Connection):
        def cursor(self):
            return LatchCursor()

    snapshot = load_operator_health_snapshot(
        Gateway(),
        LatchConnection,
        mode="LIVE",
        venue_mode="LIVE",
        execution_enabled=True,
    )
    assert snapshot["metrics"]["active_kill_switches"][0]["scope"] == "bitget-protection-stream"
    service = OperatorCommandService(
        Gateway(),
        operator_id=42,
        health_reader=create_shared_health_reader(lambda: snapshot),
    )
    text = service._on_health()
    assert "DEGRADED" in text
    assert "bitget-protection-stream: socket-not-connected" in text
    assert "not fill evidence" in text
