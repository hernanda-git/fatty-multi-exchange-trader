from fatty_trader.storage.migrations import MIGRATIONS


def test_margin_admission_migration_is_append_only_and_has_active_reservation_guards() -> None:
    versions = [version for version, _ in MIGRATIONS]
    assert versions == sorted(versions)
    assert versions[-1] == 17
    sql = dict(MIGRATIONS)[14]
    assert "CREATE TABLE IF NOT EXISTS bitget_margin_reservations" in sql
    assert "planned_margin_usdt NUMERIC NOT NULL CHECK (planned_margin_usdt > 0)" in sql
    assert "state IN ('reserved', 'consumed', 'released', 'unknown')" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS bitget_margin_reservations_active_dispatch" in sql
    assert "planned_margin_usdt" in sql and "balance_snapshot_id" in sql


def test_bitget_kill_switch_alert_only_constraint_is_dropped_so_a_latch_can_land() -> None:
    """Migration 16 pinned Bitget to alert-only; migration 17 must undo that.

    With the CHECK in place, ``latch_kill_switch()`` (``active = TRUE``) raises
    CheckViolation, so an anomaly kills the LIVE monitor instead of blocking
    entries. The last migration has to drop the constraint, not add it.
    """
    sql = dict(MIGRATIONS)[17]
    assert "DROP CONSTRAINT IF EXISTS bitget_kill_switch_alert_only" in sql
    assert "ADD CONSTRAINT" not in sql
    assert "CHECK" not in sql
