from fatty_trader.storage.migrations import MIGRATIONS


def test_live_dispatch_migration_persists_take_profits() -> None:
    version, sql = MIGRATIONS[-1]

    assert version >= 2
    assert "canonical_signals" in sql
    assert "take_profits" in sql
