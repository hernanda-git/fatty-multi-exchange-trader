from pathlib import Path

import pytest

from fatty_trader.service import SUPPORTED_SERVICES, service_config
from fatty_trader.web.health import build_health_report

REPO_ROOT = Path(__file__).parents[2]
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def test_service_config_defaults_to_paper_and_exposes_only_role_credentials() -> None:
    binance = service_config("dispatcher-binance", {})
    assert binance.mode == "PAPER"
    assert binance.required_credentials == ("BINANCE_API_KEY", "BINANCE_API_SECRET")
    assert "BITGET_API_KEY" not in binance.allowed_environment

    analyzer = service_config("analyzer", {})
    assert analyzer.mode == "PAPER"
    assert "BINANCE_API_SECRET" not in analyzer.allowed_environment
    assert "BITGET_API_SECRET" not in analyzer.allowed_environment


def test_bitget_mode_is_isolated_from_global_paper_mode() -> None:
    config = service_config(
        "dispatcher-bitget",
        {"TRADER_MODE": "PAPER", "BITGET_MODE": "LIVE"},
    )
    assert config.mode == "PAPER"
    assert config.venue_mode == "LIVE"


def test_bitget_dispatcher_starts_cutover_gated_without_constructing_execution_client() -> None:
    from fatty_trader.service import bitget_dispatcher_state

    constructed = False

    def execution_client_factory() -> object:
        nonlocal constructed
        constructed = True
        return object()

    state = bitget_dispatcher_state(
        {"TRADER_MODE": "PAPER", "BITGET_MODE": "LIVE"},
        execution_client_factory=execution_client_factory,
    )

    assert state == "cutover-gated"
    assert not constructed
    assert service_config("dispatcher-bitget", {}).execution_enabled is False


def test_non_bitget_live_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="only PAPER mode"):
        service_config("dispatcher-binance", {"TRADER_MODE": "LIVE"})


def test_unknown_service_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported service"):
        service_config("not-a-service", {})


def test_intake_config_allows_missing_telegram_values_without_starting() -> None:
    from fatty_trader.service import intake_settings

    assert intake_settings({}) is None


def test_compose_contains_migration_init_and_isolated_workers() -> None:
    for service in ("migrate", "init", *SUPPORTED_SERVICES):
        assert f"  {service}:" in COMPOSE
    for service in ("dispatcher-binance", "dispatcher-bitget"):
        command = (
            'command: ["/app/.venv/bin/python", "-m", "fatty_trader.service", '
            f'"--service", "{service}"]'
        )
        assert command in COMPOSE
    assert "service_completed_successfully" in COMPOSE
    assert "TRADER_MODE: PAPER" in COMPOSE
    assert "CODEX_ACCOUNT_LABEL: ${CODEX_ACCOUNT_LABEL:-UNCONFIGURED}" in COMPOSE


def test_health_report_is_sanitized_and_exposes_component_states() -> None:
    report = build_health_report(
        {
            "SERVICE_NAME": "web",
            "TRADER_MODE": "PAPER",
            "SERVICE_COMPONENTS": "postgres=ready,dispatcher-binance=starting",
        }
    )
    assert report["status"] == "degraded"
    assert report["mode"] == "PAPER"
    assert report["live_execution_enabled"] is False
    assert report["components"] == {
        "postgres": "ready",
        "dispatcher-binance": "starting",
    }
    assert "API_SECRET" not in str(report)


def test_bitget_dispatch_migration_records_auditable_transitions() -> None:
    from fatty_trader.storage.migrations import MIGRATIONS

    version, sql = MIGRATIONS[-1]

    assert version >= 3
    assert "dispatch_transitions" in sql
    assert "dispatch_id" in sql


def test_apply_schema_runs_additive_live_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg

    from fatty_trader import service

    class Cursor:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement: str) -> None:
            self.statements.append(statement)

        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class Connection:
        def __init__(self, cursor: Cursor) -> None:
            self.cursor_value = cursor
            self.committed = False

        def cursor(self) -> Cursor:
            return self.cursor_value

        def commit(self) -> None:
            self.committed = True

        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    cursor = Cursor()
    connection = Connection(cursor)
    called = False

    def fake_apply_migrations(value: Cursor) -> list[int]:
        nonlocal called
        called = value is cursor
        return [1]

    monkeypatch.setattr(psycopg, "connect", lambda: connection)
    monkeypatch.setattr(service, "apply_migrations", fake_apply_migrations)

    service.apply_schema()

    assert called
    assert connection.committed
