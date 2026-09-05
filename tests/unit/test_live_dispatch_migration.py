from fatty_trader.storage.migrations import MIGRATIONS


def test_live_dispatch_migration_persists_take_profits() -> None:
    matching = [sql for version, sql in MIGRATIONS if version >= 2 and "take_profits" in sql]

    assert matching
    assert "canonical_signals" in matching[-1]


def test_kill_switch_migration_is_additive_and_persistent() -> None:
    version, sql = MIGRATIONS[-1]

    assert version >= 4
    assert "venue_kill_switches" in sql
    assert "CREATE TABLE IF NOT EXISTS" in sql
    assert "active" in sql
