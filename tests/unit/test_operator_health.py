"""Actual slash loader/renderer financial, freshness and safety boundaries."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_health_truthfulness import Connection, Cursor, Gateway, report, snapshot

from fatty_trader.operator.health import normalize_account_snapshot
from fatty_trader.operator.health_report_format import format_report

LATCH = {
    "scope": "bitget-protection-stream",
    "reason": "socket-not-connected",
    "latched_at": "2026-10-12T06:08:00+00:00",
    "updated_at": "2026-10-13T06:00:00+00:00",
    "age_seconds": "86400",
}


class LoadedCursor(Cursor):
    def fetchone(self):
        if "canonical_signals" in self.sql:
            return (440, 100, 0, 0, 30, 2, 1)
        if "venue_kill_switches" in self.sql:
            return ("INACTIVE", None, [LATCH])
        if "bitget_margin_reservations" in self.sql:
            return ({"reserved": 1, "unknown": 1},)
        if "balance_snapshots" in self.sql:
            return ("2026-10-01T07:11:20+00:00", Decimal("1025168.418400"))
        if "bitget_post_fill_reconciliations" in self.sql:
            return ("mismatch",)
        if "FROM fills" in self.sql:
            return (19, Decimal("-0.4"), Decimal("1.2"), Decimal("-1.6"), Decimal("0.03"))
        return super().fetchone()

    def fetchall(self):
        if "FROM telegram_messages" in self.sql:
            return [(16134, "2026-10-13T08:00:00+00:00", "$WLD latest <source> & update")]
        if "fallback_protection" in self.sql:
            return [
                (
                    "WLDUSDT",
                    "SHORT",
                    "0.3874",
                    "0.3938",
                    "[]",
                    "80",
                    "active",
                    "epoch",
                    "2026-10-12",
                )
            ]
        if "bitget_protection_capabilities" in self.sql:
            return [
                ("WLDUSDT", "HEALTHY", "2026-10-13T08:00:00+00:00", "2026-10-13T08:01:00+00:00")
            ]
        return super().fetchall()


class LoadedConnection(Connection):
    def cursor(self):
        return LoadedCursor()


class PositionGateway(Gateway):
    def get_positions(self):
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


def test_slash_loads_historical_financials_latest_message_and_admission_age():
    data = snapshot(Gateway(), LoadedConnection)
    text = format_report(**data)
    assert data["pnl"]["fill_n"] == 19
    assert data["pnl"]["total_fees"] == Decimal("0.03")
    assert data["metrics"]["total_orders"] == 30
    assert data["metrics"]["reservation_totals"] == {"reserved": 1, "unknown": 1}
    assert data["metrics"]["latest_post_fill_reconciliation"] == "mismatch"
    assert data["messages"][0]["msg_id"] == 16134
    assert "Net realized 🔴 -0.43" in text
    assert "Fees paid    0.03" in text
    assert "Last admission balance evidence" in text
    assert "2026-10-01T07:11:20+00:00" in text
    assert data["account"]["observed_at"] != data["metrics"]["last_admission_captured_at"]
    assert "Bitget authenticated GET" in text
    assert "USDT-FUTURES · USDT" in text
    assert "#16134" in text
    assert "&lt;source&gt; &amp; update" in text
    assert "Not probed in operator container" in text


def test_stored_healthy_stream_is_not_current_monitor_or_latch_release_evidence():
    text = report(Gateway(), LoadedConnection)
    assert "HEALTHY" in text
    assert "Monitor    UNKNOWN" in text
    # The latch itself is allowlisted, so recovery policy permits it; a stored
    # HEALTHY stream row is still not the proof that releases it.
    assert "Recovery   RELEASABLE · approved recovery permitted" in text
    assert "DEGRADED" in text
    assert "socket-not-connected" in text
    assert LATCH["latched_at"] in text
    assert LATCH["updated_at"] in text
    assert "86400 sec" in text
    assert "🟢 ONLINE" not in text


@pytest.mark.parametrize(
    "scope,reason",
    [("global", "clock-skew-exceeded"), ("bitget", "new-fault"), ("new-safety-scope", "new-fault")],
)
def test_outside_recovery_whitelist_is_reported_refused(scope, reason):
    class UnsafeCursor(LoadedCursor):
        def fetchone(self):
            if "venue_kill_switches" in self.sql:
                return ("INACTIVE", None, [{**LATCH, "scope": scope, "reason": reason}])
            return super().fetchone()

    class UnsafeConnection(LoadedConnection):
        def cursor(self):
            return UnsafeCursor()

    assert "Recovery   REFUSED" in report(Gateway(), UnsafeConnection)


@pytest.mark.parametrize("gate", ["1", "UNKNOWN"])
def test_fallback_registration_does_not_prove_working_protection(gate):
    text = report(PositionGateway(), LoadedConnection, fallback_mutations_enabled=gate)
    assert "DRIFT" in text
    assert "FALLBACK REGISTERED WLDUSDT" in text
    assert "enforcement UNKNOWN" in text
    assert "registered" in text
    assert "can close" not in text
    assert "No operator action needed" not in text


def test_fallback_disabled_gate_is_distinct_from_missing_protection():
    text = report(PositionGateway(), LoadedConnection, fallback_mutations_enabled="0")
    assert "CANNOT CLOSE (gated)" in text
    assert "MISSING WLDUSDT" not in text
    missing = report(PositionGateway(), Connection, fallback_mutations_enabled="0")
    assert "MISSING WLDUSDT" in missing
    assert "CANNOT CLOSE" not in missing


def test_entry_latch_does_not_fabricate_shutdown_of_reduce_only_fallback():
    class ActiveCursor(LoadedCursor):
        def fetchone(self):
            if "venue_kill_switches" in self.sql:
                return (
                    "ACTIVE",
                    "clock-skew-exceeded",
                    [{**LATCH, "scope": "bitget", "reason": "clock-skew-exceeded"}],
                )
            return super().fetchone()

    class ActiveConnection(LoadedConnection):
        def cursor(self):
            return ActiveCursor()

    text = report(PositionGateway(), ActiveConnection, fallback_mutations_enabled="1")
    assert "clock-skew-exceeded" in text
    assert "FALLBACK REGISTERED WLDUSDT" in text
    assert "enforcement UNKNOWN" in text
    assert "CANNOT CLOSE (gated)" not in text


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "bad", None])
def test_account_normalization_preserves_invalid_as_unknown_and_signed_pnl(value):
    observed = datetime(2026, 10, 13, tzinfo=UTC)
    account = normalize_account_snapshot(
        {
            "accountEquity": value,
            "available": value,
            "unrealizedPL": "-0.123",
            "api_key": "secret",
            "uid": "private-account",
            "raw": {"credentials": "hidden"},
        },
        environment="LIVE",
        observed_at=observed,
    )
    assert account["equity"] is None
    assert account["available"] is None
    assert account["unrealized_pl"] == "-0.123"
    assert account["observed_at"] == observed.isoformat()
    assert "uid" not in account
    assert "secret" not in str(account)


def test_successful_zero_balance_is_not_unknown():
    account = normalize_account_snapshot(
        {"usdtEquity": "0", "availableBalance": "0", "unrealizedPL": "0"}, environment="LIVE"
    )
    assert account["equity"] == "0"
    assert account["available"] == "0"
    assert account["read_status"] == "OK"
