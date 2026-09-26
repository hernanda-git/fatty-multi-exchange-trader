from fatty_trader.storage.migrations import MIGRATIONS


def test_live_dispatch_migration_persists_take_profits() -> None:
    matching = [sql for version, sql in MIGRATIONS if version >= 2 and "take_profits" in sql]

    assert matching
    assert "canonical_signals" in matching[-1]


def test_kill_switch_migration_keeps_schema_and_enforces_alert_only_bitget() -> None:
    table_match = next(
        (version, sql)
        for version, sql in MIGRATIONS
        if "CREATE TABLE IF NOT EXISTS venue_kill_switches" in sql
    )
    constraint_match = next(
        (version, sql) for version, sql in MIGRATIONS if "bitget_kill_switch_alert_only" in sql
    )

    table_version, table_sql = table_match
    constraint_version, constraint_sql = constraint_match
    assert table_version >= 4
    assert "active" in table_sql
    assert constraint_version > table_version
    assert "scope <> 'bitget' OR active = FALSE" in constraint_sql


def test_canary_reservations_have_an_additive_durable_schema() -> None:
    matching = [(version, sql) for version, sql in MIGRATIONS if "canary_entry_reservations" in sql]

    assert matching
    version, sql = matching[-1]
    assert version > 6
    assert "CREATE TABLE IF NOT EXISTS" in sql
    assert "dispatch_id UUID PRIMARY KEY REFERENCES dispatches(id)" in sql


def test_bitget_protection_capability_migration_is_additive() -> None:
    matching = [
        (version, sql) for version, sql in MIGRATIONS if "bitget_protection_capabilities" in sql
    ]

    assert matching
    version, sql = matching[-1]
    assert version > 10
    assert "CREATE TABLE IF NOT EXISTS bitget_protection_capabilities" in sql
    for field in (
        "environment",
        "native_state",
        "fallback_allowed",
        "payload_profile",
        "stream_state",
        "last_stream_at",
    ):
        assert field in sql


def test_provider_reconciliation_migration_is_additive_and_idempotent() -> None:
    matching = [
        (version, sql) for version, sql in MIGRATIONS if "provider_reconciliation_events" in sql
    ]

    assert matching
    version, sql = matching[-1]
    assert version > 11
    assert "CREATE TABLE IF NOT EXISTS provider_reconciliation_events" in sql
    assert "provider_fill_id" in sql
    assert "SYSTEM_LIQUIDATION" in sql
    assert "UNIQUE (exchange, provider_fill_id)" in sql
