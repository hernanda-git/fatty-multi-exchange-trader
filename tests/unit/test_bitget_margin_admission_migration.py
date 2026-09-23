from fatty_trader.storage.migrations import MIGRATIONS


def test_margin_admission_migration_is_append_only_and_has_active_reservation_guards() -> None:
    versions = [version for version, _ in MIGRATIONS]
    assert versions[-1] == 14
    sql = dict(MIGRATIONS)[14]
    assert "CREATE TABLE IF NOT EXISTS bitget_margin_reservations" in sql
    assert "planned_margin_usdt NUMERIC NOT NULL CHECK (planned_margin_usdt > 0)" in sql
    assert "state IN ('reserved', 'consumed', 'released', 'unknown')" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS bitget_margin_reservations_active_dispatch" in sql
    assert "planned_margin_usdt" in sql and "balance_snapshot_id" in sql
