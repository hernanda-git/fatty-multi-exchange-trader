"""Offline consumer regressions for failed and independently observed evidence."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from fatty_trader.operator.health import (
    _latch_recovery_readiness,
    build_operator_diagnostic,
    load_lifecycle_recovery,
    load_operator_health_snapshot,
    stream_condition,
)
from fatty_trader.operator.health_report_format import format_report


class Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql):
        self.sql = sql

    def fetchone(self):
        if "canonical_signals" in self.sql:
            return (0, 0, 0, 0, 0, 0, 0)
        if "venue_kill_switches" in self.sql:
            return ("INACTIVE", None, [])
        if "bitget_margin_reservations" in self.sql:
            return ({},)
        if "balance_snapshots" in self.sql:
            return (None, None)
        if "bitget_post_fill_reconciliations" in self.sql:
            return (None,)
        if "FROM fills" in self.sql:
            return (0, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))
        raise AssertionError("unexpected fixture query")

    def fetchall(self):
        if "SELECT (SELECT count(*) FROM positions" in self.sql:
            return [(0, 0, 0)]
        return []


class Connection:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def cursor(self):
        return Cursor()


class Gateway:
    def get_account_snapshot(self):
        return {"accountEquity": "100", "available": "99", "unrealizedPL": "0"}

    def get_positions(self):
        return []

    def get_orders(self):
        return []


class FailedGateway(Gateway):
    def get_positions(self):
        raise TimeoutError("offline provider")


def snapshot(gateway, connection=Connection, **kwargs):
    return load_operator_health_snapshot(
        gateway, connection, mode="LIVE", venue_mode="LIVE", execution_enabled=True, **kwargs
    )


def report(gateway, connection=Connection, **kwargs):
    return format_report(**snapshot(gateway, connection, **kwargs))


def test_failed_provider_does_not_match_healthy_empty_database():
    assert "OK · provider 0 = DB 0" in report(Gateway())
    failed = report(FailedGateway())
    assert "UNKNOWN" in failed
    assert "OK · provider" not in failed
    assert "OK · flat" not in failed
    assert "Equity     100" in failed


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

    text = report(TpGateway())
    assert "MISSING BTCUSDT" in text
    assert "NATIVE REGISTERED" not in text
    assert "No operator action needed" not in text


def test_tp_only_fallback_row_is_not_a_loss_stop():
    class TpGateway(Gateway):
        def get_positions(self):
            return [{"symbol": "BTCUSDT", "side": "LONG", "stop_loss": None, "take_profit": "110"}]

    class TpCursor(Cursor):
        def fetchall(self):
            if "fallback_protection" in self.sql:
                return [("BTCUSDT", "LONG", "100", None, "[110]", "1", "active", "epoch", "now")]
            return super().fetchall()

    class TpConnection(Connection):
        def cursor(self):
            return TpCursor()

    text = report(TpGateway(), TpConnection, fallback_mutations_enabled="1")
    assert "MISSING BTCUSDT" in text
    assert "NATIVE REGISTERED" not in text
    assert "can close" not in text


@pytest.mark.parametrize("failed_table", ["fills", "balance_snapshots", "telegram_messages"])
def test_section_failure_preserves_successful_account_and_other_ledger_sections(failed_table):
    class PartialCursor(Cursor):
        def execute(self, sql):
            if f"FROM {failed_table}" in sql and "canonical_signals" not in sql:
                raise TimeoutError("private diagnostic must not leak")
            super().execute(sql)

    class PartialConnection(Connection):
        def cursor(self):
            return PartialCursor()

    data = snapshot(Gateway(), PartialConnection)
    assert data["account"]["equity"] == "100"
    assert data["metrics"]["open_positions"] == 0
    text = format_report(**data)
    assert "private diagnostic" not in text
    if failed_table == "fills":
        assert data["pnl"]["fill_n"] == "UNKNOWN"
        assert "Fees paid    UNKNOWN" in text
        assert "Net realized ➖ UNKNOWN" in text
        assert "no realized PNL yet" not in text
    elif failed_table == "telegram_messages":
        assert data["messages"] is None
        assert "message read unavailable" in text
        assert "No source message stored" not in text
    else:
        assert data["metrics"]["last_admission_captured_at"] == "UNKNOWN"
        assert data["pnl"]["fill_n"] == 0


def test_unknown_provider_and_ledger_counts_never_reconcile_successfully():
    def missing_db():
        raise TimeoutError("offline")

    text = report(FailedGateway(), missing_db)
    assert "provider UNKNOWN ⚠️" in text
    assert "provider UNKNOWN ✅" not in text
    assert "OK · provider" not in text
    assert "Reservations   UNKNOWN" in text


def test_partially_missing_open_pnl_is_not_a_partial_total():
    class Positions(Gateway):
        def get_positions(self):
            return [
                {"symbol": "BTCUSDT", "unrealized_pl": "3"},
                {"symbol": "ETHUSDT", "unrealized_pl": None},
            ]

    text = report(Positions())
    assert "Open uPnL    UNKNOWN" in text
    assert "Open uPnL    🟢 +3" not in text


def test_lifecycle_recovery_reports_durable_issues_only():
    assert (
        load_lifecycle_recovery(lambda _sql: ("[]",), venue_mode="LIVE", account_id="123")
        == "READY"
    )
    blocked = load_lifecycle_recovery(
        lambda _sql: ('["reservation-without-entry-intent"]',),
        venue_mode="LIVE",
        account_id="123",
    )
    assert blocked == "BLOCKED · reservation-without-entry-intent"


@pytest.mark.parametrize(
    ("venue_mode", "account_id", "row"),
    [
        ("UNKNOWN", "123", ("[]",)),
        ("LIVE", None, ("[]",)),
        ("LIVE", "123", None),
    ],
    ids=["unbound-venue-mode", "unbound-account", "unreadable-issue-set"],
)
def test_lifecycle_recovery_never_reports_ready_without_evidence(venue_mode, account_id, row):
    value = load_lifecycle_recovery(lambda _sql: row, venue_mode=venue_mode, account_id=account_id)

    assert value.startswith("UNKNOWN")

    def failing(_sql):
        raise TimeoutError("offline")

    assert load_lifecycle_recovery(failing, venue_mode="LIVE", account_id="123").startswith(
        "UNKNOWN"
    )


def test_stream_condition_reports_stored_evidence_with_age():
    fresh = datetime.now(UTC).isoformat()
    live_row = stream_condition(
        [{"symbol": "PUMPUSDT", "state": "HEALTHY", "last_stream_at": fresh}]
    )
    assert live_row.startswith("STORED · PUMPUSDT HEALTHY")
    assert stream_condition(
        [{"symbol": "PUMPUSDT", "state": "HEALTHY", "last_stream_at": "2026-01-01T00:00:00+00:00"}]
    ).startswith("STORED-STALE · PUMPUSDT HEALTHY")
    assert stream_condition(
        [{"symbol": "BTCUSDT", "state": "FAILED", "last_stream_at": fresh}]
    ).startswith("STORED · BTCUSDT FAILED")
    assert stream_condition([]).startswith("UNKNOWN")
    assert stream_condition(None).startswith("UNKNOWN")


def test_latch_readiness_distinguishes_clear_and_refused():
    assert _latch_recovery_readiness([]) == "CLEAR · no active latch"
    refused = _latch_recovery_readiness(
        [
            {
                "scope": "global",
                "reason": "operator-contain",
                "latched_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    )
    assert refused.startswith("REFUSED")
    assert _latch_recovery_readiness("UNKNOWN").startswith("UNKNOWN")
