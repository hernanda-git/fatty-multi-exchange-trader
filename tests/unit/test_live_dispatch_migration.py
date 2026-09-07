from fatty_trader.storage.migrations import MIGRATIONS


def test_live_dispatch_migration_persists_take_profits() -> None:
    matching = [sql for version, sql in MIGRATIONS if version >= 2 and "take_profits" in sql]

    assert matching
    assert "canonical_signals" in matching[-1]


def test_kill_switch_migration_is_additive_and_persistent() -> None:
    matching = [(version, sql) for version, sql in MIGRATIONS if "venue_kill_switches" in sql]

    assert matching
    version, sql = matching[-1]
    assert version >= 4
    assert "venue_kill_switches" in sql
    assert "CREATE TABLE IF NOT EXISTS" in sql
    assert "active" in sql


def test_canary_reservations_have_an_additive_durable_schema() -> None:
    matching = [(version, sql) for version, sql in MIGRATIONS if "canary_entry_reservations" in sql]

    assert matching
    version, sql = matching[-1]
    assert version > 6
    assert "CREATE TABLE IF NOT EXISTS" in sql
    assert "dispatch_id UUID PRIMARY KEY REFERENCES dispatches(id)" in sql
