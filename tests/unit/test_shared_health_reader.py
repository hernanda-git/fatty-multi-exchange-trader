"""Execute the reader contract intended for service.py, with offline loaders."""

from test_health_truthfulness import Connection, FailedGateway, Gateway

from fatty_trader.operator import health


def test_shared_loader_preserves_unknown_provider_and_database():
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
    assert "NATIVE REGISTERED" not in text


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
    assert "NATIVE REGISTERED" in text


def test_reported_native_stop_id_does_not_fabricate_a_verified_stop_level():
    class IdGateway(Gateway):
        def get_positions(self):
            return [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "size": "1",
                    "stop_loss": None,
                    "stop_loss_id": "native-plan-id",
                    "take_profit": None,
                }
            ]

    data = health.load_operator_health_snapshot(
        IdGateway(), Connection, mode="LIVE", venue_mode="LIVE", execution_enabled=True
    )
    assert data["sltp"]["BTCUSDT"]["has_sl"] is False
    assert data["sltp"]["BTCUSDT"]["native_sl_id_reported"] is True
    text = health.create_shared_health_reader(lambda: data)()
    assert "UNKNOWN loss-stop level/registration BTCUSDT" in text
    assert "MISSING BTCUSDT" not in text
    assert "NATIVE REGISTERED" not in text
