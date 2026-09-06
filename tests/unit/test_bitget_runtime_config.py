from pathlib import Path

import pytest

from fatty_trader.service import service_config

REPO_ROOT = Path(__file__).parents[2]
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def test_compose_keeps_bitget_execution_closed_by_default() -> None:
    assert "TRADER_MODE: DEMO" in COMPOSE
    assert "BITGET_EXECUTION_ENABLED: ${BITGET_EXECUTION_ENABLED:-0}" in COMPOSE
    assert "BITGET_CANARY_MAX_ORDERS: ${BITGET_CANARY_MAX_ORDERS:-0}" in COMPOSE
    assert "BITGET_APPROVAL_REFERENCE: ${BITGET_APPROVAL_REFERENCE:-}" in COMPOSE


def test_dispatcher_check_rejects_live_execution_without_all_explicit_gates() -> None:
    enabled = {
        "TRADER_MODE": "DEMO",
        "BITGET_MODE": "LIVE",
        "BITGET_EXECUTION_ENABLED": "1",
        "BITGET_API_KEY": "key",
        "BITGET_API_SECRET": "secret",
        "BITGET_API_PASSPHRASE": "passphrase",
    }
    with pytest.raises(ValueError, match="canary"):
        service_config("dispatcher-bitget", enabled)

    enabled.update(
        {
            "BITGET_CANARY_MAX_ORDERS": "1",
            "BITGET_CANARY_SYMBOL": "BTCUSDT",
            "BITGET_APPROVAL_REFERENCE": "operator-ticket-123",
            "BITGET_MAX_CLOCK_SKEW_MS": "5000",
        }
    )
    assert service_config("dispatcher-bitget", enabled).execution_enabled is True


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("BITGET_CANARY_MAX_ORDERS", "0", "canary"),
        ("BITGET_CANARY_SYMBOL", "btc-usdt", "canary"),
        ("BITGET_APPROVAL_REFERENCE", "", "approval"),
        ("BITGET_MAX_CLOCK_SKEW_MS", "0", "clock skew"),
    ],
)
def test_dispatcher_check_rejects_invalid_cutover_values(
    name: str, value: str, message: str
) -> None:
    environment = {
        "TRADER_MODE": "DEMO",
        "BITGET_MODE": "LIVE",
        "BITGET_EXECUTION_ENABLED": "1",
        "BITGET_API_KEY": "key",
        "BITGET_API_SECRET": "secret",
        "BITGET_API_PASSPHRASE": "passphrase",
        "BITGET_CANARY_MAX_ORDERS": "1",
        "BITGET_CANARY_SYMBOL": "BTCUSDT",
        "BITGET_APPROVAL_REFERENCE": "operator-ticket-123",
        "BITGET_MAX_CLOCK_SKEW_MS": "5000",
    }
    environment[name] = value
    with pytest.raises(ValueError, match=message):
        service_config("dispatcher-bitget", environment)


def test_backup_and_runtime_scripts_are_safe_compose_operational_tools() -> None:
    backup = (REPO_ROOT / "scripts" / "backup_postgres.sh").read_text(encoding="utf-8")
    verify = (REPO_ROOT / "scripts" / "verify_bitget_runtime.sh").read_text(encoding="utf-8")

    assert "docker compose exec -T postgres pg_dump" in backup
    assert '--username="${POSTGRES_USER:-fatty_app}"' in backup
    assert "test -s" in backup
    assert "restore_postgres.sh" in backup
    assert "BITGET_API_SECRET" not in backup
    assert "docker compose ps" in verify
    assert "completed_services=(migrate init)" in verify
    assert "exited:0" in verify
    assert "schema_migrations" in verify
    assert "bitget_api_probe.py" in verify
    assert "BITGET_API_SECRET" not in verify
