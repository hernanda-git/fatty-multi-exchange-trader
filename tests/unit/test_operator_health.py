from __future__ import annotations

from decimal import Decimal

from fatty_trader.operator.health import build_operator_health_report


class Gateway:
    def get_account_snapshot(self) -> dict[str, str]:
        return {"usdtEquity": "8.0", "available": "6.6"}

    def get_positions(self) -> list[dict[str, object]]:
        return [
            {
                "symbol": "WLDUSDT",
                "side": "SHORT",
                "size": Decimal("80"),
                "entry": Decimal("0.3874"),
                "mark": "0.3829",
                "unrealized_pl": "0.36",
                "leverage": "30",
                "margin_mode": "isolated",
                "liquidation_price": "0.3974",
                "stop_loss": None,
                "take_profit": None,
            }
        ]

    def get_orders(self) -> list[dict[str, object]]:
        return []


class Cursor:
    def execute(self, sql: str) -> None:
        self.sql = sql

    def fetchone(self):
        return (
            42,
            15,
            0,
            0,
            0,
            1,
            True,
            "historical-latch",
            16134,
            "2026-09-15 01:12:14+00",
            "$WLD invalid",
        )

    def fetchall(self):
        return [("WLDUSDT", "SHORT", "0.3874", "0.3938", "[]", "80", "active")]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class Connection:
    def cursor(self):
        return Cursor()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_health_report_explains_drift_protection_and_kill_switch() -> None:
    calls = iter([Connection(), Connection()])
    report = build_operator_health_report(
        Gateway(),
        lambda: next(calls),
        mode="LIVE",
        venue_mode="LIVE",
        execution_enabled=True,
    )

    assert "HEALTH" in report
    assert "WLDUSDT" in report
    assert "FALLBACK" in report
    assert "Active blocks new dispatches" in report
    assert "DRIFT" in report
    assert "No order dibuat" not in report


class GatedCursor(Cursor):
    """A fallback row exists while the fallback mutation gate is OFF."""

    def fetchall(self):
        return [("WLDUSDT", "SHORT", "0.3874", "0.3938", "[]", "80", "active")]


def _report(fallback_mutations_enabled: str, *, kill_active: bool = True) -> str:
    calls = iter([Connection(), Connection()])
    if not kill_active:

        class InactiveCursor(Cursor):
            def fetchone(self):
                return (
                    42,
                    15,
                    0,
                    0,
                    0,
                    1,
                    False,
                    None,
                    16134,
                    "2026-09-15 01:12:14+00",
                    "$WLD invalid",
                )

        class InactiveConnection(Connection):
            def cursor(self):
                return InactiveCursor()

        calls = iter([InactiveConnection(), Connection()])
    return build_operator_health_report(
        Gateway(),
        lambda: next(calls),
        mode="LIVE",
        venue_mode="LIVE",
        execution_enabled=True,
        fallback_mutations_enabled=fallback_mutations_enabled,
    )


def test_fallback_row_with_mutation_gate_off_must_not_read_as_protected() -> None:
    """A registered fallback that cannot mutate is NOT protection.

    The fallback monitor returns early when the mutation gate is off, so the
    SL/TP levels in the table are inert. Reporting "FALLBACK / active" next to
    a 30x position implies an auto-close that will never fire.
    """
    report = _report("0")

    assert "🟡 FALLBACK" not in report, (
        "a fallback that cannot mutate must not be presented as protection"
    )
    # "CANNOT CLOSE" is the operator-critical fact and must be in the report;
    # the gate's own name is already shown in the SYSTEM block, so do not
    # assert a redundant copy of it here.
    assert "CANNOT CLOSE" in report.upper()
    assert "🔴 GATED OFF" in report


def test_fallback_row_with_mutation_gate_on_reports_fallback() -> None:
    """With the gate on, the registered fallback genuinely owns the levels."""
    report = _report("1", kill_active=False)

    assert "🟡 FALLBACK" in report
    assert "Fallback   active" in report


def test_active_kill_switch_blocks_fallback_mutation_even_when_gate_on() -> None:
    report = _report("1")
    assert "🔴 GATED OFF" in report
    assert "CANNOT CLOSE" in report.upper()
    assert "🟡 FALLBACK" not in report


def test_unprotected_position_is_distinguishable_from_gated_fallback() -> None:
    """A truly unprotected position must NOT say 'CANNOT CLOSE' either.

    Otherwise the report collapses 'no protection at all' into 'protection
    exists but is disabled', which is a different problem needing a different
    response.
    """

    class NoFallbackCursor(Cursor):
        def fetchall(self):
            return []

    calls = iter([Connection(), NoFallbackCursor()])
    report = build_operator_health_report(
        Gateway(),
        lambda: next(calls),
        mode="LIVE",
        venue_mode="LIVE",
        execution_enabled=True,
        fallback_mutations_enabled="0",
    )

    assert "🔴 MISSING" in report
    assert "CANNOT CLOSE" not in report.upper()
