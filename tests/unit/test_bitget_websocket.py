"""Offline tests for the Bitget Classic V1 websocket contract used by the V2 REST lane."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.websocket import (
    CLASSIC_WS_URL,
    BitgetClassicWebSocket,
    WebSocketConnectionState,
)
from fatty_trader.exchanges.bitget.ws_models import (
    WebSocketProtocolError,
    build_login_message,
    build_subscription_message,
    normalize_ws_message,
)


def test_login_uses_seconds_and_classic_user_verify_signature() -> None:
    message = build_login_message(
        api_key="key",
        passphrase="pass",
        secret="secret",
        timestamp_seconds=1_693_830_000,
    )

    assert message["op"] == "login"
    assert message["args"][0]["apiKey"] == "key"
    assert message["args"][0]["passphrase"] == "pass"
    assert message["args"][0]["timestamp"] == "1693830000"
    assert message["args"][0]["sign"]
    assert "secret" not in json.dumps(message)


def test_subscription_uses_classic_mc_and_umcbl_channels() -> None:
    message = build_subscription_message(["wldusdt", "BTCUSDT"])

    assert message == {
        "op": "subscribe",
        "args": [
            {"instType": "mc", "channel": "ticker", "instId": "WLDUSDT"},
            {"instType": "mc", "channel": "ticker", "instId": "BTCUSDT"},
            {"instType": "UMCBL", "channel": "positions", "instId": "default"},
            {"instType": "UMCBL", "channel": "orders", "instId": "default"},
            {"instType": "UMCBL", "channel": "ordersAlgo", "instId": "default"},
        ],
    }


def test_ticker_normalizer_uses_mark_price() -> None:
    events = normalize_ws_message(
        json.dumps(
            {
                "action": "snapshot",
                "arg": {"instType": "mc", "channel": "ticker", "instId": "WLDUSDT"},
                "data": [
                    {
                        "instId": "WLDUSDT",
                        "last": "0.3927",
                        "markPrice": "0.3919",
                        "systemTime": 1693830000123,
                    }
                ],
            }
        )
    )

    assert len(events) == 1
    assert events[0].kind == "mark_price"
    assert events[0].symbol == "WLDUSDT"
    assert events[0].mark_price == Decimal("0.3919")
    assert events[0].event_time_ms == 1693830000123


def test_private_order_normalizer_emits_order_and_fill_from_classic_order_push() -> None:
    events = normalize_ws_message(
        json.dumps(
            {
                "action": "snapshot",
                "arg": {"instType": "UMCBL", "channel": "orders", "instId": "default"},
                "data": [
                    {
                        "instId": "WLDUSDT",
                        "ordId": "entry-1",
                        "clOrdId": "client-1",
                        "side": "buy",
                        "posSide": "net",
                        "fillPx": "0.3971",
                        "fillSz": "91",
                        "fillTime": "1693830000123",
                        "fillFee": "-0.01",
                        "fillFeeCcy": "USDT",
                        "accFillSz": "91",
                        "avgPx": "0.3971",
                        "status": "full-fill",
                        "low": False,
                    }
                ],
            }
        )
    )

    assert [event.kind for event in events] == ["order", "fill"]
    assert events[0].provider_order_id == "entry-1"
    assert events[0].client_oid == "client-1"
    assert events[1].provider_fill_id == "entry-1:1693830000123"
    assert events[1].quantity == Decimal("91")
    assert events[1].price == Decimal("0.3971")
    assert events[1].fee == Decimal("-0.01")


def test_private_position_and_plan_normalizers_preserve_provider_payload() -> None:
    position_events = normalize_ws_message(
        json.dumps(
            {
                "action": "snapshot",
                "arg": {"instType": "UMCBL", "channel": "positions", "instId": "default"},
                "data": [
                    {
                        "instId": "WLDUSDT",
                        "holdSide": "long",
                        "total": "91",
                        "averageOpenPrice": "0.3971",
                        "liqPx": "0.3866",
                        "uTime": "1693830000123",
                    }
                ],
            }
        )
    )
    plan_events = normalize_ws_message(
        json.dumps(
            {
                "action": "snapshot",
                "arg": {"instType": "UMCBL", "channel": "ordersAlgo", "instId": "default"},
                "data": [
                    {
                        "instId": "WLDUSDT",
                        "id": "sl-1",
                        "cOid": "sl-client",
                        "triggerPx": "0.388",
                        "ordType": "market",
                        "state": "not_trigger",
                        "triggerPxType": "mark",
                        "uTime": "1693830000123",
                    }
                ],
            }
        )
    )

    assert position_events[0].kind == "position"
    assert position_events[0].quantity == Decimal("91")
    assert position_events[0].liquidation_price == Decimal("0.3866")
    assert plan_events[0].kind == "plan"
    assert plan_events[0].provider_order_id == "sl-1"
    assert plan_events[0].trigger_price == Decimal("0.388")


def test_protocol_errors_are_not_silently_treated_as_market_events() -> None:
    with pytest.raises(WebSocketProtocolError, match="30004"):
        normalize_ws_message(json.dumps({"event": "error", "code": "30004", "msg": "login"}))


class FakeConnection:
    def __init__(self, incoming: list[str]) -> None:
        self.incoming = list(incoming)
        self.sent: list[str] = []
        self.closed = False

    async def send(self, value: str) -> None:
        self.sent.append(value)

    async def recv(self) -> str:
        if not self.incoming:
            raise AssertionError("fake connection has no incoming message")
        return self.incoming.pop(0)

    async def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self, connections: list[FakeConnection]) -> None:
        self.connections = connections
        self.urls: list[str] = []

    async def connect(self, url: str) -> FakeConnection:
        self.urls.append(url)
        return self.connections.pop(0)


@pytest.mark.asyncio
async def test_transport_logs_in_subscribes_and_normalizes_without_mutations() -> None:
    ticker = json.dumps(
        {
            "action": "update",
            "arg": {"instType": "mc", "channel": "ticker", "instId": "WLDUSDT"},
            "data": [{"instId": "WLDUSDT", "markPrice": "0.3919", "systemTime": 10}],
        }
    )
    connection = FakeConnection([json.dumps({"event": "login", "code": "0"}), ticker])
    transport = FakeTransport([connection])
    client = BitgetClassicWebSocket(
        api_key="key",
        api_secret="secret",
        passphrase="pass",
        symbols=["WLDUSDT"],
        transport=transport,
        clock=lambda: 100.0,
    )

    await client.connect()
    events = await client.receive_once()

    assert transport.urls == [CLASSIC_WS_URL]
    assert client.state is WebSocketConnectionState.CONNECTED
    assert events[0].mark_price == Decimal("0.3919")
    assert json.loads(connection.sent[0])["op"] == "login"
    assert json.loads(connection.sent[1])["op"] == "subscribe"
    assert "secret" not in "".join(connection.sent)


@pytest.mark.asyncio
async def test_transport_reconnects_and_resubscribes_after_disconnect() -> None:
    first = FakeConnection([json.dumps({"event": "login", "code": "0"})])
    second = FakeConnection([json.dumps({"event": "login", "code": "0"})])
    transport = FakeTransport([first, second])
    client = BitgetClassicWebSocket(
        api_key="key",
        api_secret="secret",
        passphrase="pass",
        symbols=["BTCUSDT"],
        transport=transport,
        clock=lambda: 100.0,
    )

    await client.connect()
    await client.reconnect()

    assert first.closed is True
    assert len(transport.urls) == 2
    assert len(second.sent) == 2
    assert client.state is WebSocketConnectionState.CONNECTED


@pytest.mark.asyncio
async def test_transport_marks_stream_stale_and_sends_text_ping() -> None:
    connection = FakeConnection([json.dumps({"event": "login", "code": "0"})])
    transport = FakeTransport([connection])
    clock_value = [100.0]
    client = BitgetClassicWebSocket(
        api_key="key",
        api_secret="secret",
        passphrase="pass",
        symbols=["BTCUSDT"],
        transport=transport,
        clock=lambda: clock_value[0],
        stale_after=5.0,
        heartbeat_interval=3.0,
    )

    await client.connect()
    clock_value[0] = 106.0
    assert client.check_freshness() is False
    assert client.state is WebSocketConnectionState.STALE
    await client.send_heartbeat_if_due()
    assert connection.sent[-1] == "ping"


@pytest.mark.asyncio
async def test_mark_freshness_is_symbol_local_and_requires_a_received_mark_event() -> None:
    ticker = json.dumps(
        {
            "action": "update",
            "arg": {"instType": "mc", "channel": "ticker", "instId": "BTCUSDT"},
            "data": [{"instId": "BTCUSDT", "markPrice": "100", "systemTime": 10}],
        }
    )
    connection = FakeConnection([json.dumps({"event": "login", "code": "0"}), ticker])
    client = BitgetClassicWebSocket(
        api_key="key",
        api_secret="secret",
        passphrase="pass",
        symbols=["BTCUSDT", "ETHUSDT"],
        transport=FakeTransport([connection]),
        clock=lambda: 100.0,
        stale_after=5.0,
    )

    await client.connect()

    assert client.check_freshness("BTCUSDT") is False
    assert client.check_freshness("ETHUSDT") is False
    await client.receive_once()
    assert client.check_freshness("BTCUSDT") is True
    assert client.check_freshness("ETHUSDT") is False


@pytest.mark.asyncio
async def test_silent_connection_receive_times_out_and_emits_text_ping() -> None:
    class SilentConnection(FakeConnection):
        async def recv(self) -> str:
            if self.incoming:
                return self.incoming.pop(0)
            await asyncio.sleep(1)
            return ""

    connection = SilentConnection([json.dumps({"event": "login", "code": "0"})])
    client = BitgetClassicWebSocket(
        api_key="key",
        api_secret="secret",
        passphrase="pass",
        symbols=["BTCUSDT"],
        transport=FakeTransport([connection]),
        stale_after=0.01,
        heartbeat_interval=0.01,
    )

    await client.connect()
    assert await client.receive_once() == []
    assert connection.sent[-1] == "ping"


@pytest.mark.asyncio
async def test_missing_pong_after_two_heartbeat_windows_forces_reconnect() -> None:
    class SilentConnection(FakeConnection):
        async def recv(self) -> str:
            if self.incoming:
                return self.incoming.pop(0)
            await asyncio.sleep(1)
            return ""

    connection = SilentConnection([json.dumps({"event": "login", "code": "0"})])
    client = BitgetClassicWebSocket(
        api_key="key",
        api_secret="secret",
        passphrase="pass",
        symbols=["BTCUSDT"],
        transport=FakeTransport([connection]),
        stale_after=0.01,
        heartbeat_interval=0.01,
    )

    await client.connect()
    assert await client.receive_once() == []
    with pytest.raises(ConnectionError, match="heartbeat"):
        await client.receive_once()
    assert client.state is WebSocketConnectionState.RECONNECTING
