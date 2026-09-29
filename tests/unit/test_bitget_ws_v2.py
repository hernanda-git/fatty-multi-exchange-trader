"""Bitget V2 dual-transport WebSocket contract.

Wire facts verified empirically against the LIVE public endpoint on
2026-09-29 (see commit dfa5dfd follow-up work):

- Subscribe uses ``op``, NOT ``action``. ``action`` returns
  ``{"code":30003,"msg":"INVALID op:null"}``.
- ``instType`` is ``USDT-FUTURES``, not the v1 ``mc``/``UMCBL`` pair.
- App-level heartbeat is the bare text ``ping`` -> ``pong``; the JSON form
  ``{"op":"ping"}`` returns ``{"code":30002}``.
- The public socket rejects private channels with
  ``{"code":30016,"msg":"Param error"}``. Ticker on the private socket fails
  the same way. The split is mandatory, not stylistic.
- Private login signs ``/user/verify`` over a SECOND-precision timestamp.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.websocket_v2 import BitgetV2WebSocket
from fatty_trader.exchanges.bitget.ws_models import WebSocketProtocolError
from fatty_trader.exchanges.bitget.ws_v2_models import (
    build_v2_login_message,
    build_v2_private_subscription,
    build_v2_public_subscription,
    normalize_v2_frame,
)

V2_PUBLIC_URL = "wss://ws.bitget.com/v2/ws/public"
V2_PRIVATE_URL = "wss://ws.bitget.com/v2/ws/private"


class FakeConnection:
    """Minimal scripted WebSocketConnection."""

    def __init__(self, script: list[str]) -> None:
        self.sent: list[str] = []
        self._script = list(script)
        self.closed = False

    async def send(self, value: str) -> None:
        self.sent.append(value)

    async def recv(self) -> str:
        if not self._script:
            await asyncio.sleep(3600)
        return self._script.pop(0)

    async def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self, public_script, private_script) -> None:
        self._public = public_script
        self._private = private_script
        self.urls: list[str] = []
        self.public = FakeConnection(public_script)
        self.private = FakeConnection(private_script)

    async def connect(self, url: str):
        self.urls.append(url)
        if url == V2_PUBLIC_URL:
            return self.public
        if url == V2_PRIVATE_URL:
            return self.private
        raise AssertionError(f"unexpected url {url}")


def _ticker_frame(symbol: str = "BTCUSDT", mark: str = "100.5") -> str:
    return json.dumps(
        {
            "action": "snapshot",
            "arg": {
                "instType": "USDT-FUTURES",
                "channel": "ticker",
                "instId": symbol,
            },
            "data": [{"instId": symbol, "markPrice": mark, "lastPr": mark}],
            "ts": "1700000000000",
        }
    )


# --- message construction -------------------------------------------------


def test_public_subscription_uses_op_and_usdt_futures() -> None:
    message = build_v2_public_subscription(["BTCUSDT", "ETHUSDT"])
    assert message["op"] == "subscribe"
    assert message["args"] == [
        {"instType": "USDT-FUTURES", "channel": "ticker", "instId": "BTCUSDT"},
        {"instType": "USDT-FUTURES", "channel": "ticker", "instId": "ETHUSDT"},
    ]


def test_public_subscription_deduplicates_and_rejects_empty_symbol() -> None:
    assert len(build_v2_public_subscription(["btcusdt", "BTCUSDT"])["args"]) == 1
    with pytest.raises(ValueError):
        build_v2_public_subscription([""])


def test_private_subscription_uses_orders_algo_channel() -> None:
    args = build_v2_private_subscription()["args"]
    channels = {arg["channel"] for arg in args}
    # `ordersAlgo` is the retired v1 spelling; v2 uses `orders-algo`.
    assert "orders-algo" in channels
    assert "ordersAlgo" not in channels
    assert all(arg["instType"] == "USDT-FUTURES" for arg in args)


def test_login_uses_second_precision_timestamp_and_user_verify_signature() -> None:
    message = build_v2_login_message(
        api_key="k", passphrase="p", secret="s", timestamp_seconds=1_700_000_000
    )
    assert message["op"] == "login"
    arg = message["args"][0]
    assert arg["timestamp"] == "1700000000"
    assert arg["apiKey"] == "k"
    assert arg["sign"]


# --- frame normalization --------------------------------------------------


def test_ticker_frame_normalizes_to_mark_price_event() -> None:
    events = normalize_v2_frame(_ticker_frame())
    assert len(events) == 1
    event = events[0]
    assert event.kind == "mark_price"
    assert event.symbol == "BTCUSDT"
    assert event.mark_price == Decimal("100.5")
    assert event.event_time_ms == 1_700_000_000_000


def test_subscribe_ack_is_not_an_event() -> None:
    frame = json.dumps({"event": "subscribe", "arg": {"channel": "ticker"}})
    assert normalize_v2_frame(frame) == []


def test_pong_text_frame_is_not_an_event() -> None:
    assert normalize_v2_frame("pong") == []


def test_error_frame_raises_with_provider_code() -> None:
    frame = json.dumps({"event": "error", "code": 30016, "msg": "Param error"})
    with pytest.raises(WebSocketProtocolError) as excinfo:
        normalize_v2_frame(frame)
    assert "30016" in str(excinfo.value)


def test_frame_without_mark_price_is_rejected() -> None:
    frame = json.dumps(
        {
            "action": "snapshot",
            "arg": {"instType": "USDT-FUTURES", "channel": "ticker", "instId": "BTCUSDT"},
            "data": [{"instId": "BTCUSDT"}],
            "ts": "1700000000000",
        }
    )
    with pytest.raises(WebSocketProtocolError):
        normalize_v2_frame(frame)


# --- transport ------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_opens_both_sockets_and_does_not_mix_channels() -> None:
    transport = FakeTransport(
        public_script=[_ticker_frame()],
        private_script=[json.dumps({"event": "login", "code": 0})],
    )
    socket = BitgetV2WebSocket(
        api_key="k",
        api_secret="s",
        passphrase="p",
        symbols=["BTCUSDT"],
        transport=transport,
    )
    await socket.connect()
    assert transport.urls == [V2_PUBLIC_URL, V2_PRIVATE_URL]
    public_sent = "".join(transport.public.sent)
    private_sent = "".join(transport.private.sent)
    # Ticker belongs on public only; positions/orders only on private.
    assert "ticker" in public_sent
    assert "positions" not in public_sent
    assert "orders-algo" in private_sent
    assert "ticker" not in private_sent
    await socket.close()


@pytest.mark.asyncio
async def test_private_login_failure_leaves_transport_not_connected() -> None:
    transport = FakeTransport(
        public_script=[_ticker_frame()],
        private_script=[json.dumps({"event": "error", "code": 30005, "msg": "bad"})],
    )
    socket = BitgetV2WebSocket(
        api_key="k", api_secret="s", passphrase="p", symbols=["BTCUSDT"], transport=transport
    )
    with pytest.raises(WebSocketProtocolError):
        await socket.connect()
    # A half-open private socket must never look healthy.
    assert socket.state.value != "CONNECTED"
    assert socket.check_freshness() is False


@pytest.mark.asyncio
async def test_public_only_deadline_marks_stream_stale() -> None:
    transport = FakeTransport(
        public_script=[_ticker_frame()],
        private_script=[json.dumps({"event": "login", "code": 0})],
    )
    clock = {"now": 0.0}
    socket = BitgetV2WebSocket(
        api_key="k",
        api_secret="s",
        passphrase="p",
        symbols=["BTCUSDT"],
        transport=transport,
        clock=lambda: clock["now"],
        stale_after=5.0,
    )
    await socket.connect()
    await socket.receive_once()
    assert socket.check_freshness() is True
    clock["now"] = 30.0
    assert socket.check_freshness() is False
    await socket.close()


@pytest.mark.asyncio
async def test_missing_private_socket_marks_transport_dead() -> None:
    """The public ticker alone is not a working protection stream."""
    transport = FakeTransport(
        public_script=[_ticker_frame()],
        private_script=[json.dumps({"event": "login", "code": 0})],
    )
    socket = BitgetV2WebSocket(
        api_key="k", api_secret="s", passphrase="p", symbols=["BTCUSDT"], transport=transport
    )
    await socket.connect()
    # Feed a real, FRESH public mark event first. Without this the test would
    # pass for the wrong reason (no marks at all) and never exercise the
    # private-leg interlock.
    await socket.receive_once()
    assert socket.check_freshness() is True
    # Now simulate the private leg dying while public still ticks.
    await socket._close_private()
    assert socket.check_freshness() is False
    assert socket.state.value in {"FAILED", "DISCONNECTED", "RECONNECTING", "STALE"}
    await socket.close()


@pytest.mark.asyncio
async def test_subscribe_symbols_only_touches_public_socket() -> None:
    transport = FakeTransport(
        public_script=[_ticker_frame()],
        private_script=[json.dumps({"event": "login", "code": 0})],
    )
    socket = BitgetV2WebSocket(
        api_key="k", api_secret="s", passphrase="p", symbols=["BTCUSDT"], transport=transport
    )
    await socket.connect()
    added = await socket.subscribe_symbols(["ETHUSDT", "BTCUSDT"])
    assert added == ("ETHUSDT",)
    assert "ETHUSDT" in "".join(transport.public.sent)
    assert "ETHUSDT" not in "".join(transport.private.sent)
    removed = await socket.unsubscribe_symbols(["BTCUSDT"])
    assert removed == ("BTCUSDT",)
    assert socket.symbols == ("ETHUSDT",)
    await socket.close()


@pytest.mark.asyncio
async def test_subscribe_without_credentials_is_rejected() -> None:
    socket = BitgetV2WebSocket(api_key="", api_secret="", passphrase="", symbols=["BTCUSDT"])
    with pytest.raises(ValueError):
        socket._private_subscription_args()  # type: ignore[attr-defined]
