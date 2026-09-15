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
