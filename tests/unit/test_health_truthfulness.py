"""Offline behavioral regressions for unknown provider evidence."""

import pytest

from fatty_trader.operator.health import build_operator_diagnostic, build_operator_health_report


class Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql):
        pass

    def fetchone(self):
        return (0, 0, 0, 0, 0, 0, False, None, None, None, None)

    def fetchall(self):
        return [(0, 0, 0)]


class Connection:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def cursor(self):
        return Cursor()


class Gateway:
    def get_account_snapshot(self):
        return {"equity": "100"}

    def get_positions(self):
        return []

    def get_orders(self):
        return []


class FailedGateway(Gateway):
    def get_positions(self):
        raise TimeoutError("offline provider")


def report(gateway):
    return build_operator_health_report(
        gateway, Connection, mode="DEMO", venue_mode="DEMO", execution_enabled=False
    )


def test_failed_provider_does_not_match_healthy_empty_database():
    assert "MATCH" in report(Gateway())
    failed = report(FailedGateway())
    assert "UNKNOWN" in failed
    assert "MATCH" not in failed
    assert "None confirmed by provider" not in failed


@pytest.mark.parametrize("kind", ["status", "reconcile"])
def test_failed_diagnostic_never_confirms_zero_positions(kind):
    healthy = build_operator_diagnostic(kind, Gateway(), Connection)
    assert "Provider positions: <code>0</code>" in healthy
    failed = build_operator_diagnostic(kind, FailedGateway(), Connection)
    assert "Provider positions: <code>UNKNOWN</code>" in failed
    assert "MATCH" not in failed
    assert "Pending orders: <code>0</code>" not in failed


@pytest.mark.parametrize("stop", [None, "", "0", "-1", "NaN", "Infinity"])
def test_tp_only_or_invalid_sl_is_missing_loss_stop(stop):
    class TpGateway(Gateway):
        def get_positions(self):
            return [{"symbol": "BTCUSDT", "side": "LONG", "stop_loss": stop, "take_profit": "110"}]

    failed = report(TpGateway())
    assert "MISSING LOSS STOP" in failed
    assert "🟢 NATIVE" not in failed
    assert "No operator action needed" not in failed


def test_tp_only_fallback_row_is_not_a_loss_stop():
    class TpGateway(Gateway):
        def get_positions(self):
            return [{"symbol": "BTCUSDT", "side": "LONG", "stop_loss": None, "take_profit": "110"}]

    class TpCursor(Cursor):
        def fetchall(self):
            return [("BTCUSDT", "LONG", "100", None, "[110]", "1", "active")]

    class TpConnection(Connection):
        def cursor(self):
            return TpCursor()

    text = build_operator_health_report(
        TpGateway(),
        TpConnection,
        mode="DEMO",
        venue_mode="DEMO",
        execution_enabled=False,
        fallback_mutations_enabled="1",
    )
    assert "MISSING LOSS STOP" in text
    assert "🟡 FALLBACK" not in text
    assert "(can close)" not in text
