"""Execute the reader contract intended for service.py, with offline loaders."""

from datetime import UTC

from test_health_truthfulness import Connection, FailedGateway, Gateway

from fatty_trader.operator import health
from fatty_trader.operator import health_report_format as fmt
from fatty_trader.operator.live_commands import OperatorCommandService


def test_service_reader_passes_same_snapshot_to_cron_formatter(monkeypatch):
    from datetime import datetime

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(fmt, "datetime", FixedClock)
    snapshot = dict(
        positions=[],
        pending_orders=[],
        sltp={},
        pnl={},
        messages=[],
        metrics={"open_positions": 0},
        account={"equity": "100"},
        modes={"mode": "DEMO"},
        codex={},
        services={},
    )
    calls = []
    real_formatter = fmt.format_report

    def render(**data):
        calls.append(data)
        return real_formatter(**data)

    monkeypatch.setattr(fmt, "format_report", render)
    reader = health.create_shared_health_reader(lambda: snapshot)
    service = OperatorCommandService(Gateway(), operator_id=42, health_reader=reader)
    actual = service._on_health()
    assert calls == [snapshot]
    assert actual == real_formatter(**snapshot)


def test_shared_loader_preserves_unknown_provider_and_database(monkeypatch):
    snapshot = health.load_operator_health_snapshot(
        FailedGateway(), Connection, mode="DEMO", venue_mode="DEMO", execution_enabled=False
    )
    assert snapshot["positions"] is None
    assert snapshot["pending_orders"] == []
    assert snapshot["metrics"]["open_positions"] == 0
    text = health.create_shared_health_reader(lambda: snapshot)()
    assert "UNKNOWN" in text
    assert "OK · provider 0 = DB 0" not in text
    assert "OK · flat" not in text


def test_shared_loader_maps_tp_only_as_missing_loss_stop():
    class TpGateway(Gateway):
        def get_positions(self):
            return [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "size": "1",
                    "entry": "100",
                    "mark": "101",
                    "stop_loss": None,
                    "take_profit": "110",
                }
            ]

    snapshot = health.load_operator_health_snapshot(
        TpGateway(), Connection, mode="DEMO", venue_mode="DEMO", execution_enabled=False
    )
    assert snapshot["positions"][0]["direction"] == "LONG"
    assert snapshot["positions"][0]["qty"] == "1"
    assert snapshot["sltp"]["BTCUSDT"]["has_sl"] is False
    text = health.create_shared_health_reader(lambda: snapshot)()
    assert "SL MISSING" in text
    assert "🔴 MISSING BTCUSDT" in text
    assert "🟢 NATIVE OK" not in text


def test_shared_loader_keeps_order_read_failure_unknown():
    class OrderFailure(Gateway):
        def get_orders(self):
            raise TimeoutError("offline")

    snapshot = health.load_operator_health_snapshot(
        OrderFailure(), Connection, mode="DEMO", venue_mode="DEMO", execution_enabled=False
    )
    assert snapshot["positions"] == []
    assert snapshot["pending_orders"] is None
    text = health.create_shared_health_reader(lambda: snapshot)()
    assert "UNKNOWN — provider open-order read failed" in text
    assert "0 pending orders · provider read OK" not in text


def test_shared_loader_failed_db_never_claims_matching_ledger():
    def failed_connection():
        raise TimeoutError("offline DB")

    snapshot = health.load_operator_health_snapshot(
        Gateway(), failed_connection, mode="DEMO", venue_mode="DEMO", execution_enabled=False
    )
    assert snapshot["metrics"]["open_positions"] == "UNKNOWN"
    assert snapshot["metrics"]["kill_switch"] == "UNKNOWN"
    text = health.create_shared_health_reader(lambda: snapshot)()
    assert "OK · provider 0 = DB 0" not in text
    assert "DB UNKNOWN" in text


def test_shared_loader_maps_valid_native_stop():
    class StopGateway(Gateway):
        def get_positions(self):
            return [{"symbol": "BTCUSDT", "side": "LONG", "stop_loss": "90", "take_profit": "110"}]

    snapshot = health.load_operator_health_snapshot(
        StopGateway(), Connection, mode="DEMO", venue_mode="DEMO", execution_enabled=False
    )
    assert snapshot["sltp"]["BTCUSDT"]["has_sl"] is True
    text = health.create_shared_health_reader(lambda: snapshot)()
    assert "🟢 NATIVE OK" in text
