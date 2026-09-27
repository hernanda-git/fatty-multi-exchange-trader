from datetime import UTC, datetime
from pathlib import Path

import pytest

from fatty_trader.exchanges.bitget.protection_capability import (
    BitgetProtectionCapability,
    NativeProtectionState,
)
from fatty_trader.service import (
    build_bitget_protection_admission,
    build_bitget_protection_stream,
    service_config,
)
from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository

REPO_ROOT = Path(__file__).parents[2]
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def test_compose_keeps_bitget_execution_closed_by_default() -> None:
    dispatcher = COMPOSE.split("  dispatcher-bitget:", 1)[1].split("  monitor-binance:", 1)[0]
    monitor = COMPOSE.split("  monitor-bitget:", 1)[1].split("  operator-bot:", 1)[0]
    assert "TRADER_MODE: ${TRADER_MODE:-DEMO}" in dispatcher
    assert "BITGET_MODE: ${BITGET_MODE:-DEMO}" in dispatcher
    assert "BITGET_EXECUTION_ENABLED: ${BITGET_EXECUTION_ENABLED:-0}" in dispatcher
    assert (
        "BITGET_PROTECTION_CAPABILITY_GATE_ENABLED: ${BITGET_PROTECTION_CAPABILITY_GATE_ENABLED:-0}"
        in dispatcher
    )
    assert "BITGET_PROTECTION_STALE_SECONDS: ${BITGET_PROTECTION_STALE_SECONDS:-5}" in dispatcher
    assert "BITGET_PROTECTION_STREAM_ENABLED: ${BITGET_PROTECTION_STREAM_ENABLED:-0}" in monitor
    assert "BITGET_PROTECTION_STREAM_MODE: ${BITGET_PROTECTION_STREAM_MODE:-observe}" in monitor
    assert (
        "BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED: "
        "${BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED:-0}" in monitor
    )
    assert (
        "BITGET_PROTECTION_REST_WATCHDOG_SECONDS: ${BITGET_PROTECTION_REST_WATCHDOG_SECONDS:-5}"
        in monitor
    )
    assert "BITGET_CANARY_MAX_ORDERS: ${BITGET_CANARY_MAX_ORDERS:-0}" in COMPOSE
    assert "BITGET_APPROVAL_REFERENCE: ${BITGET_APPROVAL_REFERENCE:-}" in COMPOSE


def test_execution_uses_global_cap_without_single_symbol_restriction() -> None:
    environment = {
        "TRADER_MODE": "LIVE",
        "BITGET_MODE": "LIVE",
        "BITGET_EXECUTION_ENABLED": "1",
        "BITGET_CANARY_MAX_ORDERS": "5",
        "BITGET_APPROVAL_REFERENCE": "user-approved-20260908",
        "BITGET_MAX_CLOCK_SKEW_MS": "5000",
    }

    assert service_config("dispatcher-bitget", environment).execution_enabled is True


def test_protection_capability_gate_is_disabled_by_default() -> None:
    assert build_bitget_protection_admission({}) is None


def test_protection_capability_gate_rejects_invalid_flag() -> None:
    with pytest.raises(ValueError, match="BITGET_PROTECTION_CAPABILITY_GATE_ENABLED"):
        build_bitget_protection_admission({"BITGET_PROTECTION_CAPABILITY_GATE_ENABLED": "yes"})


def test_enabled_protection_capability_gate_uses_live_environment_record() -> None:
    now = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)
    repository = InMemoryProtectionCapabilityRepository()
    repository.upsert(
        BitgetProtectionCapability(
            exchange="bitget",
            environment="LIVE",
            symbol="BTCUSDT",
            native_state=NativeProtectionState.VERIFIED,
        )
    )

    admission = build_bitget_protection_admission(
        {
            "BITGET_PROTECTION_CAPABILITY_GATE_ENABLED": "1",
            "BITGET_MODE": "LIVE",
            "BITGET_PROTECTION_STALE_SECONDS": "5",
        },
        repository=repository,
        now=lambda: now,
    )

    assert admission is not None
    assert admission("BTCUSDT") == (True, "native-protected")
    assert admission("WLDUSDT") == (False, "protection-capability-unknown")


def test_protection_stream_is_disabled_by_default() -> None:
    assert build_bitget_protection_stream({}) is None


def test_enabled_protection_stream_can_start_empty_and_sync_active_symbols_dynamically() -> None:
    stream = build_bitget_protection_stream(
        {
            "BITGET_PROTECTION_STREAM_ENABLED": "1",
            "BITGET_API_KEY": "key",
            "BITGET_API_SECRET": "secret",
            "BITGET_API_PASSPHRASE": "passphrase",
            "BITGET_MODE": "LIVE",
        },
        active_symbol_source=lambda: ["BTCUSDT"],
    )

    assert stream is not None
    assert stream.symbols == ()


def test_stream_mutation_flag_cannot_enable_unimplemented_close_path() -> None:
    with pytest.raises(ValueError, match="observe-only"):
        build_bitget_protection_stream(
            {
                "BITGET_PROTECTION_STREAM_ENABLED": "1",
                "BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED": "1",
                "BITGET_PROTECTION_STREAM_SYMBOLS": "BTCUSDT",
                "BITGET_API_KEY": "key",
                "BITGET_API_SECRET": "secret",
                "BITGET_API_PASSPHRASE": "passphrase",
                "BITGET_MODE": "LIVE",
            }
        )


def test_dispatcher_check_rejects_live_execution_without_all_explicit_gates() -> None:
    enabled = {
        "TRADER_MODE": "DEMO",
        "BITGET_MODE": "DEMO",
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
        ("BITGET_APPROVAL_REFERENCE", "", "approval"),
        ("BITGET_MAX_CLOCK_SKEW_MS", "0", "clock skew"),
    ],
)
def test_dispatcher_check_rejects_invalid_cutover_values(
    name: str, value: str, message: str
) -> None:
    environment = {
        "TRADER_MODE": "DEMO",
        "BITGET_MODE": "DEMO",
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
    restore = (REPO_ROOT / "scripts" / "restore_postgres.sh").read_text(encoding="utf-8")
    verify = (REPO_ROOT / "scripts" / "verify_bitget_runtime.sh").read_text(encoding="utf-8")

    assert "docker compose exec -T postgres pg_dump" in backup
    assert '--username="${POSTGRES_USER:-fatty_app}"' in backup
    assert "test -s" in backup
    assert "restore_postgres.sh" in backup
    assert "BITGET_API_SECRET" not in backup
    # An unverifiable archive is useless in an incident, and retention stops the
    # backup directory from growing without bound on a small host.
    assert "pg_restore --list" in backup
    assert "BACKUP_RETAIN_DAYS" in backup
    # The postgres service publishes no host port, so the restore must run inside
    # the container; a host-side pg_restore can never connect on this deployment.
    assert 'compose_bin="${COMPOSE_BIN:-docker compose}"' in restore
    assert "exec -T postgres pg_restore" in restore
    assert "127.0.0.1" not in restore
    assert "POSTGRES_PASSWORD" not in restore
    assert "CONFIRM_RESTORE" in restore
    assert "pg_restore --list" in restore
    assert "docker compose ps" in verify
    assert "completed_services=(migrate init)" in verify
    assert "exited:0" in verify
    assert "schema_migrations" in verify
    assert "bitget_api_probe.py" in verify
    assert "BITGET_API_SECRET" not in verify


def test_daily_backup_timer_is_installed_as_repo_tooling() -> None:
    """The live ledger must have a scheduled backup, not just a manual script."""
    timer = (REPO_ROOT / "deploy" / "systemd" / "fatty-backup.timer").read_text(encoding="utf-8")
    service = (REPO_ROOT / "deploy" / "systemd" / "fatty-backup.service").read_text(
        encoding="utf-8"
    )

    assert "OnUnitActiveSec=24h" in timer
    assert "Persistent=true" in timer
    assert "Unit=fatty-backup.service" in timer
    assert "scripts/backup_postgres.sh" in service
    # Dumps live outside the repository tree on purpose.
    assert "BACKUP_DIR=/home/valarion/backups/fatty-trader" in service
    assert "BACKUP_RETAIN_DAYS" in service
    assert "EnvironmentFile" not in service
