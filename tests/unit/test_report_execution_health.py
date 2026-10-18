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
        metrics or {"kill_switch": "INACTIVE", "active_kill_switches": []},
        {"equity": "9.17026367", "read_status": "OK"},
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


def test_missing_container_health_never_permits_online_card():
    services = {
        "status": "OK",
        "total": 2,
        "running": 2,
        "healthy": 2,
        "dispatcher_state": "RUNNING",
        "lifecycle_recovery": "READY",
    }
    assert "🟢 ONLINE" in render(services=services)
    services["healthy"] = 1
    text = render(services=services)
    assert "DEGRADED" in text
    assert "🟢 ONLINE" not in text


def test_cron_classifies_running_service_without_health_as_unhealthy(monkeypatch):
    from types import SimpleNamespace

    from test_health_report_provider_source import health_report as script

    def run(cmd, **kwargs):
        output = (
            '{"services": {"source-management": {}}}'
            if "config" in cmd
            else "source-management|running|\n"
        )
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(script.subprocess, "run", run)
    services = script.get_service_status()
    assert services["running"] == 1
    assert services["healthy"] == 0
    assert services["unhealthy"] == 1


def test_cron_reports_dispatcher_block_with_exact_issue(monkeypatch):
    from types import SimpleNamespace

    from test_health_report_provider_source import health_report as script

    def run(cmd, **kwargs):
        if "config" in cmd:
            output = '{"services": {"dispatcher-bitget": {}}}'
        elif "logs" in cmd:
            output = (
                "dispatcher-bitget-1  | service=dispatcher-bitget state=recovery-blocked "
                "issues=reservation-without-entry-intent"
            )
        else:
            output = "dispatcher-bitget|running|healthy\n"
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(script.subprocess, "run", run)
    monkeypatch.setattr(script, "query_one", lambda _sql: ("[]",))
    monkeypatch.setenv("CONSOLE_ACCOUNT_ID", "5536885345")
    services = script.get_service_status("LIVE")
    assert services["dispatcher_state"] == "RUNNING"
    assert services["lifecycle_recovery"] == "BLOCKED · reservation-without-entry-intent"
    assert "DEGRADED" in render(services=services)


def test_latest_cycle_line_clears_a_historical_block(monkeypatch):
    from types import SimpleNamespace

    from test_health_report_provider_source import health_report as script

    def run(cmd, **kwargs):
        if "config" in cmd:
            output = '{"services": {"dispatcher-bitget": {}}}'
        elif "logs" in cmd:
            output = (
                "dispatcher-bitget-1  | service=dispatcher-bitget state=recovery-blocked "
                "issues=reservation-without-entry-intent\n"
                "dispatcher-bitget-1  | service=dispatcher-bitget mode=LIVE venue_mode=LIVE "
                "state=idle"
            )
        else:
            output = "dispatcher-bitget|running|healthy\n"
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(script.subprocess, "run", run)
    monkeypatch.setattr(script, "query_one", lambda _sql: ("[]",))
    monkeypatch.setenv("CONSOLE_ACCOUNT_ID", "5536885345")
    services = script.get_service_status("LIVE")
    assert services["lifecycle_recovery"] == "READY"
    assert "Lifecycle  READY" in render(services=services)


@pytest.mark.parametrize("state", ["running", "restarting"])
def test_recovery_never_comes_from_an_unreadable_or_unbounded_log(monkeypatch, state):
    import subprocess
    from types import SimpleNamespace

    from test_health_report_provider_source import health_report as script

    def run(cmd, **kwargs):
        if "config" in cmd:
            output = '{"services": {"dispatcher-bitget": {}}}'
        elif "logs" in cmd:
            assert "--since" in cmd and "5m" in cmd, "only a bounded window may be read"
            raise subprocess.TimeoutExpired(cmd, 10)
        else:
            output = f"dispatcher-bitget|{state}|healthy\n"
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(script.subprocess, "run", run)
    monkeypatch.setattr(script, "query_one", lambda _sql: ("[]",))
    monkeypatch.setenv("CONSOLE_ACCOUNT_ID", "5536885345")
    services = script.get_service_status("LIVE")
    # An unreadable cycle is unknown evidence, never an inferred READY or BLOCKED.
    assert services["lifecycle_recovery"] == "UNKNOWN · dispatcher log read failed"
    assert "DEGRADED" in render(services=services)


def test_cron_keeps_db_failure_unknown_without_erasing_successful_provider(monkeypatch):
    from test_health_report_provider_source import health_report as script

    monkeypatch.setattr(script, "get_runtime_modes", lambda: {"venue_mode": "LIVE"})
    monkeypatch.setattr(script, "load_provider_state", lambda: {"positions": [], "open_orders": []})
    monkeypatch.setattr(script, "get_account", lambda: {"read_status": "OK", "equity": "9.1"})
    monkeypatch.setattr(script, "get_codex_usage", lambda: {"status": "AUTH_FAILED"})
    monkeypatch.setattr(script, "get_service_status", lambda: {"status": "UNKNOWN"})

    def unavailable(_sql):
        raise TimeoutError("offline database")

    monkeypatch.setattr(script, "query_one", unavailable)
    monkeypatch.setattr(script, "query_all", unavailable)
    data = script.load_report_data()
    assert data["positions"] == []
    assert data["account"]["equity"] == "9.1"
    assert data["metrics"]["active_kill_switches"] == "UNKNOWN"
    assert data["pnl"]["fill_n"] == "UNKNOWN"
    assert data["messages"] is None
    text = format_report(**data)
    assert "Fees paid    UNKNOWN" in text
    assert "Durable latches UNKNOWN" in text
    assert "message read unavailable" in text


def test_operator_command_loads_stream_latch_through_shared_reader():
    from test_health_truthfulness import Gateway
    from test_operator_health import LoadedConnection

    from fatty_trader.operator.health import (
        create_shared_health_reader,
        load_operator_health_snapshot,
    )
    from fatty_trader.operator.live_commands import OperatorReadService

    snapshot = load_operator_health_snapshot(
        Gateway(),
        LoadedConnection,
        mode="LIVE",
        venue_mode="LIVE",
        execution_enabled=True,
    )
    assert snapshot["metrics"]["active_kill_switches"][0]["scope"] == "bitget-protection-stream"
    service = OperatorReadService(
        Gateway(),
        operator_id=42,
        health_reader=create_shared_health_reader(lambda: snapshot),
    )
    text = service.handle("/health", sender_id=42, is_private=True, is_forwarded=False)
    assert "DEGRADED" in text
    assert "bitget-protection-stream: socket-not-connected" in text
    assert "not fill evidence" in text


def test_health_card_budget_preserves_entities_and_closing_tags():
    from html.parser import HTMLParser

    class TagChecker(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.stack = []

        def handle_starttag(self, tag, attrs):
            self.stack.append(tag)

        def handle_endtag(self, tag):
            assert self.stack.pop() == tag

        def handle_data(self, data):
            assert "&" not in data

    codex = {
        key: "🟢&" * 4000 for key in ("status", "plan", "5h", "7d", "reset", "refreshed", "error")
    }
    text = format_report([], [], {}, {}, [], {}, {}, {}, codex, {})
    assert len(text.encode("utf-16-le")) // 2 <= 4000
    checker = TagChecker()
    checker.feed(text)
    checker.close()
    assert checker.stack == []


def test_budget_preserves_account_and_durable_safety_before_optional_details():
    from test_health_truthfulness import Gateway, snapshot
    from test_operator_health import LATCH, LoadedConnection

    data = snapshot(Gateway(), LoadedConnection)
    data["codex"] = {key: "🟢&" * 4000 for key in ("status", "plan", "5h", "7d", "error")}
    data["positions"] = [
        {
            "symbol": f"ASSET{index}USDT",
            "direction": "LONG",
            "qty": "1",
            "entry_price": "100",
            "mark_price": "99",
            "unrealized_pl": "-1",
            "leverage": "5",
            "margin_mode": "isolated",
            "liquidation_price": "80",
        }
        for index in range(10)
    ]
    text = format_report(**data)
    assert len(text.encode("utf-16-le")) // 2 <= 4000
    assert "Equity     100" in text
    assert "Available  99" in text
    assert "USDT-FUTURES · USDT" in text
    assert "Observed" in text
    assert LATCH["latched_at"] in text
    assert LATCH["updated_at"] in text
    assert "socket-not-connected" in text
    assert "Monitor    UNKNOWN" in text
    assert "no release is performed" in text


def test_codex_auth_failure_is_not_an_account_failure_or_admission_gate():
    text = render(
        services={
            "status": "OK",
            "total": 1,
            "running": 1,
            "healthy": 1,
            "dispatcher_state": "RUNNING",
            "lifecycle_recovery": "READY",
        }
    )
    assert "Equity     9.170264" in text
    # Quota failure is separately rendered; it never overwrites financial telemetry.
    data = {
        "positions": [],
        "pending_orders": [],
        "sltp": {},
        "pnl": {},
        "messages": [],
        "metrics": {"kill_switch": "INACTIVE", "active_kill_switches": []},
        "account": {"read_status": "OK", "equity": "9.17026367"},
        "modes": {"mode": "LIVE", "venue_mode": "LIVE", "execution_enabled": "1"},
        "codex": {"status": "AUTH_FAILED", "error": "HTTP 401"},
        "services": {
            "status": "OK",
            "total": 1,
            "running": 1,
            "healthy": 1,
            "dispatcher_state": "RUNNING",
            "lifecycle_recovery": "READY",
        },
    }
    text = format_report(**data)
    assert "Equity     9.170264" in text
    assert "AUTH_FAILED" in text
    assert "HTTP 401" in text
    assert "provider account read failed" not in text
