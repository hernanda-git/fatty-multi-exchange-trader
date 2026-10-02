"""Offline regression for the actual LIVE monitor stream assembly."""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from ws_v2_fakes import FakeClock, FakeTransport

from fatty_trader.exchanges.bitget.protection_capability import StreamState
from fatty_trader.exchanges.bitget.websocket_v2 import BitgetV2WebSocket
from fatty_trader.service import build_bitget_protection_stream
from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository

PUBLIC = "wss://ws.bitget.com/v2/ws/public"
PRIVATE = "wss://ws.bitget.com/v2/ws/private"
EPOCH = 1_700_000_000


def live_environment():
    return {
        "TRADER_MODE": "LIVE",
        "BITGET_MODE": "LIVE",
        "BITGET_PROTECTION_STREAM_ENABLED": "1",
        "BITGET_API_KEY": "offline-key",
        "BITGET_API_SECRET": "offline-secret",
        "BITGET_API_PASSPHRASE": "offline-passphrase",
        "BITGET_PROTECTION_STREAM_SYMBOLS": "BTCUSDT",
    }


def test_live_production_stream_selects_v2_dual_endpoints():
    runtime = build_bitget_protection_stream(
        live_environment(), repository=InMemoryProtectionCapabilityRepository()
    )
    assert runtime is not None
    assert isinstance(runtime.socket, BitgetV2WebSocket)
    assert runtime.socket.public_endpoint == PUBLIC
    assert runtime.socket.private_endpoint == PRIVATE
    assert runtime.symbols == ("BTCUSDT",)


@pytest.mark.asyncio
async def test_live_production_stream_delivers_both_legs_and_updates_capability():
    ticker = json.dumps(
        {
            "action": "snapshot",
            "arg": {"instType": "USDT-FUTURES", "channel": "ticker", "instId": "BTCUSDT"},
            "data": [{"instId": "BTCUSDT", "markPrice": "100.5"}],
            "ts": str(EPOCH * 1000),
        }
    )
    position = json.dumps(
        {
            "event": "update",
            "arg": {"instType": "USDT-FUTURES", "channel": "positions"},
            "data": [
                {"instId": "BTCUSDT", "total": "1", "holdSide": "long", "ts": str(EPOCH * 1000)}
            ],
        }
    )
    transport = FakeTransport(
        public_script=[json.dumps({"event": "subscribe", "arg": {"channel": "ticker"}}), ticker],
        private_script=[
            json.dumps({"event": "login", "code": "0"}),
            json.dumps({"event": "subscribe", "arg": {"channel": "positions"}}),
            position,
        ],
    )
    repository = InMemoryProtectionCapabilityRepository()
    runtime = build_bitget_protection_stream(
        live_environment(),
        repository=repository,
        transport=transport,
        clock=FakeClock(),
        wall_clock=lambda: EPOCH,
    )
    assert runtime is not None
    assert isinstance(runtime.socket, BitgetV2WebSocket)
    runtime._now = lambda: datetime.fromtimestamp(EPOCH, UTC)
    stop = asyncio.Event()
    seen = []
    original = runtime._on_event

    async def record(event):
        seen.append(event.kind)
        await original(event)
        if "mark_price" in seen and "position" in seen:
            assert runtime.socket.check_freshness("BTCUSDT")
            stop.set()

    runtime._on_event = record
    await asyncio.wait_for(runtime.run(stop), timeout=2)
    assert transport.urls == [PUBLIC, PRIVATE]
    public_sub = json.loads(transport.public.sent[0])
    assert public_sub == {
        "op": "subscribe",
        "args": [{"instType": "USDT-FUTURES", "channel": "ticker", "instId": "BTCUSDT"}],
    }
    login, private_sub = map(json.loads, transport.private.sent[:2])
    assert login["op"] == "login"
    assert login["args"][0]["timestamp"] == str(EPOCH)
    assert private_sub["op"] == "subscribe"
    assert {arg["channel"] for arg in private_sub["args"]} == {"positions", "orders", "orders-algo"}
    assert all(arg["instId"] == "default" for arg in private_sub["args"])
    capability = repository.get("bitget", "LIVE", "BTCUSDT")
    assert capability is not None
    assert capability.stream_state == StreamState.HEALTHY
    assert capability.last_stream_at == datetime.fromtimestamp(EPOCH, UTC)
    assert transport.public.closed and transport.private.closed
