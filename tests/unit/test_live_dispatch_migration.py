from fatty_trader.storage.migrations import MIGRATIONS


def test_live_dispatch_migration_persists_take_profits() -> None:
    matching = [sql for version, sql in MIGRATIONS if version >= 2 and "take_profits" in sql]

    assert matching
    assert "canonical_signals" in matching[-1]
    assert "take_profits" in matching[-1]
