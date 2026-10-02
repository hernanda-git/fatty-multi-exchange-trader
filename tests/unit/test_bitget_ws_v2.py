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
import contextlib
import json
from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.websocket_v2 import (
    BitgetV2WebSocket,
    V2ConnectionState,
)
from fatty_trader.exchanges.bitget.ws_models import WebSocketProtocolError
from fatty_trader.exchanges.bitget.ws_v2_models import (
    build_v2_login_message,
    build_v2_private_subscription,
    build_v2_public_subscription,
    normalize_v2_frame,
)

V2_PUBLIC_URL = "wss://ws.bitget.com/v2/ws/public"
V2_PRIVATE_URL = "wss://ws.bitget.com/v2/ws/private"


from ws_v2_fakes import FakeClock, FakeTransport  # noqa: E402


@pytest.fixture(autouse=True)
def provider_epoch_matches_injected_clock(monkeypatch):
    original = BitgetV2WebSocket.__init__

    def init(self, **kwargs):
        clock = kwargs.get("clock", lambda: 0.0)
        origin = clock()
        kwargs.setdefault("wall_clock", lambda: 1700000000 + clock() - origin)
        original(self, **kwargs)

    monkeypatch.setattr(BitgetV2WebSocket, "__init__", init)


async def _settle(socket, transport):
    # Finite fresh frame, not infinite replay, when testing transport liveness.
    frame = json.loads(_ticker_frame())
    frame["ts"] = str(int(socket._wall_clock() * 1000))
    transport.add_public(json.dumps(frame))
    for _ in range(12):
        await asyncio.sleep(0)
    return socket._drain_ready()


_LOGIN_OK = json.dumps({"event": "login", "code": "0", "msg": "success"})
_PRIVATE_SUB_OK = json.dumps(
    {"event": "subscribe", "arg": {"instType": "USDT-FUTURES", "channel": "positions"}}
)


def _position_frame(symbol: str = "BTCUSDT") -> str:
    return json.dumps(
        {
            "event": "update",
            "arg": {"instType": "USDT-FUTURES", "channel": "positions"},
            "data": [
                {
                    "ts": "1700000000000",
                    "instId": symbol,
                    "total": "1.0",
                    "available": "1.0",
                    "holdSide": "long",
                    "avgPrice": "100.0",
                    "markPrice": "100.0",
                }
            ],
        }
    )


async def _noop(*_args, **_kwargs) -> None:
    return None


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


_PRIVATE_POS = _position_frame()
_TICKER = _ticker_frame()


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
# --- defects found by independent review deleg_d65748fb (REJECT) ----------


def _two_leg_transport(marks: int = 1):
    """Public leg that keeps ticking; private leg that NEVER speaks again.

    This is the exact fail-open the reviewer found: a non-None private socket
    that is silent and unauthenticated-in-practice while public delivers marks
    forever. Before the fix, `_both_legs_open()` returned True and
    `check_freshness()` returned True forever.
    """
    transport = FakeTransport()
    transport.add_public(_TICKER, marks=marks)
    transport.add_public("pong", repeat=True)
    # private leg answers login, then goes permanently silent
    transport.add_private(_LOGIN_OK)
    transport.add_private(_PRIVATE_SUB_OK)
    return transport


async def test_silent_private_leg_is_not_healthy_even_while_public_ticks():
    transport = _two_leg_transport()
    clock = FakeClock()
    socket = BitgetV2WebSocket(
        api_key="k",
        api_secret="s",
        passphrase="p",
        symbols=["BTCUSDT"],
        transport=transport,
        clock=clock,
        stale_after=5.0,
        heartbeat_interval=10.0,
        private_silent_after=15.0,
    )
    await socket.connect()
    await socket.receive_once()
    assert socket.check_freshness() is True, "fresh private activity is healthy"

    # Keep public marks flowing, but let the private leg go silent past its
    # bound. Fresh public marks must NOT rescue the verdict.
    for _ in range(6):
        transport.add_public(_TICKER, repeat=True)
    for _ in range(6):
        await _settle(socket, transport)
        clock.advance(4.0)
        transport.add_public(_TICKER, repeat=True)
    assert socket.check_freshness() is False, "silent private leg must fail closed"
    assert socket.stale_reason() == "private-silent"
    await socket.close()


async def test_private_leg_pong_proves_liveness():
    transport = _two_leg_transport()
    clock = FakeClock()
    socket = BitgetV2WebSocket(
        api_key="k",
        api_secret="s",
        passphrase="p",
        symbols=["BTCUSDT"],
        transport=transport,
        clock=clock,
        stale_after=5.0,
        heartbeat_interval=10.0,
        private_silent_after=15.0,
    )
    await socket.connect()
    await socket.receive_once()
    # Keep the public leg fresh throughout, so the ONLY variable is private
    # silence. Otherwise this asserts stale-public, not stale-private.
    transport.public._script = []
    transport.public._orig = [_TICKER, "pong"]
    transport.public._loop = True
    transport.add_public(_TICKER, repeat=True)
    clock.advance(20.0)  # beyond private_silent_after with no private frame
    await _settle(socket, transport)
    assert socket.stale_reason() == "private-silent", socket.stale_reason()
    assert socket.check_freshness() is False
    # A pong on the private leg resets the silence clock.
    transport.add_private("pong", repeat=True)
    for _ in range(6):
        await _settle(socket, transport)
    assert socket.check_freshness() is True, "private pong must refresh liveness"
    assert socket.stale_reason() == "fresh"
    await socket.close()


async def test_unauthenticated_private_leg_is_not_healthy():
    transport = FakeTransport()
    transport.add_public(_TICKER, marks=2, repeat=True)
    transport.add_private(_LOGIN_OK)
    transport.add_private(_PRIVATE_SUB_OK)
    socket = BitgetV2WebSocket(
        api_key="k",
        api_secret="s",
        passphrase="p",
        symbols=["BTCUSDT"],
        transport=transport,
        private_silent_after=999.0,
    )
    await socket.connect()
    socket._private_authenticated = False  # simulate expiry/never-acked
    assert socket.check_freshness() is False
    assert socket.stale_reason() == "private-unauthenticated"
    await socket.close()


async def test_state_alone_must_not_be_read_as_fresh_by_a_caller():
    """Obs#2: .state can be CONNECTED while check_freshness() is False.

    ``_reader_loop`` promotes the state to CONNECTED on any arriving frame.
    A caller that reads ``.state`` without calling check_freshness() can
    therefore over-read a stream that is not actually fresh. This test pins
    that distinction so the gap stays visible until a caller lands and we
    decide whether to make ``state`` authoritative on its own.
    """
    transport = _two_leg_transport()
    clock = FakeClock()
    socket = _make(transport, clock=clock)
    await socket.connect()
    await socket.receive_once()
    # Mute the private leg and age the clock, but keep feeding public frames
    # so the reader loop promotes the state back to CONNECTED.
    transport.public._script = []
    transport.public._orig = [_TICKER, "pong"]
    transport.public._loop = True
    transport.add_public(_TICKER, repeat=True)
    clock.advance(60.0)
    for _ in range(4):
        await _settle(socket, transport)
    assert socket.state is V2ConnectionState.CONNECTED, (
        "this test documents current behaviour: the state follows the last frame"
    )
    assert socket.check_freshness() is False, (
        "but the verdict must still be False: the private leg is silent"
    )
    await socket.close()


# The old duplicate of this test asserted AFTER stop.set()/teardown, whose
# finally block closes both legs -- so it passed no matter what run() decided.
# It is deleted rather than fixed: the test below carries the real evidence
# (it asserts before teardown) and the duplicate only added a false signal.


async def test_heartbeat_send_failure_propagates():
    """defect #3: ping errors were suppressed, then stamped as sent."""
    transport = FakeTransport()
    transport.add_public(_TICKER, marks=1, repeat=True)
    transport.add_private(_LOGIN_OK)
    transport.add_private(_PRIVATE_SUB_OK)
    clock = FakeClock()
    socket = BitgetV2WebSocket(
        api_key="k",
        api_secret="s",
        passphrase="p",
        symbols=["BTCUSDT"],
        transport=transport,
        clock=clock,
        heartbeat_interval=10.0,
    )
    await socket.connect()
    clock.advance(11.0)  # heartbeat is now due
    socket._last_ping_at = None  # ignore the initial connect() heartbeat
    socket._public._fail_send = True
    with pytest.raises(ConnectionResetError):
        await socket.send_heartbeat_if_due()
    assert socket._last_ping_at is None, "must not stamp a ping on a failed send"
    await socket.close()


async def test_private_event_arriving_concurrently_is_not_lost():
    """defect #4: cancelling the losing recv() discarded private frames."""
    transport = FakeTransport()
    transport.add_public(_TICKER, marks=1)
    transport.add_public(_PRIVATE_POS, repeat=True)
    transport.add_private(_LOGIN_OK)
    transport.add_private(_PRIVATE_SUB_OK)
    transport.add_private(_PRIVATE_POS, repeat=True)
    socket = BitgetV2WebSocket(
        api_key="k",
        api_secret="s",
        passphrase="p",
        symbols=["BTCUSDT"],
        transport=transport,
    )
    await socket.connect()
    # Drain several times; the private position event must keep showing up.
    kinds = []
    for _ in range(4):
        for event in await socket.receive_once():
            kinds.append(event.kind)
    assert "position" in kinds, f"private position event was lost: {kinds}"
    await socket.close()


async def test_leg_reader_failure_surfaces_as_exception():
    transport = FakeTransport()
    transport.add_public(_TICKER, marks=1, repeat=True)
    transport.add_private(_LOGIN_OK)
    transport.add_private(_PRIVATE_SUB_OK)
    socket = BitgetV2WebSocket(
        api_key="k",
        api_secret="s",
        passphrase="p",
        symbols=["BTCUSDT"],
        transport=transport,
    )
    await socket.connect()
    transport.private._fail_recv = True
    with pytest.raises(ConnectionResetError):
        for _ in range(5):
            await socket.receive_once()
    await socket.close()


def test_expired_session_login_frame_raises_not_swallowed():
    """defect #5a: the success-ack branch shadowed the failure branch."""
    frame = json.dumps({"event": "login", "code": "30005", "msg": "login expired"})
    with pytest.raises(WebSocketProtocolError):
        normalize_v2_frame(frame)


def test_order_row_missing_size_raises_not_zero():
    """defect #5b: absent size defaulted to 0, fabricating an order event."""
    frame = json.dumps(
        {
            "event": "order",
            "arg": {"instType": "USDT-FUTURES", "channel": "orders"},
            "data": [{"orderId": "1", "symbol": "BTCUSDT", "side": "buy"}],
        }
    )
    with pytest.raises(WebSocketProtocolError):
        normalize_v2_frame(frame)


async def test_connect_rejects_missing_credentials_before_opening_sockets():
    """defect #6: ordering guarantee was untested via the public API."""
    transport = FakeTransport()
    transport.add_public(_TICKER, marks=1)
    transport.add_private(_LOGIN_OK)
    socket = BitgetV2WebSocket(
        api_key="", api_secret="", passphrase="", symbols=["BTCUSDT"], transport=transport
    )
    with pytest.raises(ValueError):
        await socket.connect()
    assert not transport.urls, f"must not open a socket when creds are missing: {transport.urls}"
    assert socket.state is not V2ConnectionState.CONNECTED


async def test_private_login_failure_leaves_transport_not_connected():
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


# --- regression tests for the vacuous-mutation battery ---------------------
# Each of these was written because a specific mutation of the production
# code left the suite GREEN. A test that cannot tell the difference is not
# evidence of anything.


def _make(transport, **kw):
    opts = {
        "api_key": "k",
        "api_secret": "s",
        "passphrase": "p",
        "symbols": ["BTCUSDT"],
        "transport": transport,
        "clock": FakeClock(),
        "stale_after": 5.0,
        "heartbeat_interval": 10.0,
        "private_silent_after": 15.0,
    }
    opts.update(kw)
    return BitgetV2WebSocket(**opts)


async def test_unauthenticated_private_leg_is_never_healthy():
    """M1: dropping the auth requirement must turn this red."""
    transport = _two_leg_transport()
    socket = _make(transport, clock=FakeClock())
    await socket.connect()
    # Let BOTH legs deliver real traffic, so the ONLY failing property is the
    # authentication flag. Without this the silence check alone reports dead
    # and the test passes without exercising the auth branch at all.
    await socket.receive_once()
    assert socket._private_authenticated is True
    assert socket.check_freshness() is True, "precondition: healthy stream"
    # Simulate the private leg having authenticated then lost its login state
    # (e.g. a re-auth failure the reader swallowed).
    socket._private_authenticated = False
    assert socket.stale_reason() == "private-unauthenticated"
    assert socket.check_freshness() is False
    assert socket.stale_reason() == "private-unauthenticated"
    assert socket.state is not V2ConnectionState.CONNECTED
    await socket.close()


async def test_private_leg_silence_beyond_bound_is_unhealthy():
    """M2/M3: the silence bound must actually be enforced."""
    transport = _two_leg_transport()
    clock = FakeClock()
    socket = _make(transport, clock=clock)
    await socket.connect()
    await socket.receive_once()
    transport.public._script = []
    transport.public._orig = [_TICKER, "pong"]
    transport.public._loop = True
    transport.add_public(_TICKER, repeat=True)
    clock.advance(16.0)  # > private_silent_after (15)
    await _settle(socket, transport)
    assert socket._private_silent_age() == 16.0
    assert socket.stale_reason() == "private-silent"
    assert socket.check_freshness() is False
    await socket.close()


async def test_stale_verdict_drives_reconnect_in_run_loop():
    """M4: run() must act on a false freshness verdict."""
    transport = _two_leg_transport()
    clock = FakeClock()
    socket = _make(transport, clock=clock)
    await socket.connect()
    await socket.receive_once()
    # Public leg keeps delivering fresh marks, but private is silent.
    transport.public._script = []
    transport.public._orig = [_TICKER, "pong"]
    transport.public._loop = True
    transport.add_public(_TICKER, repeat=True)
    clock.advance(20.0)
    frame = json.loads(_ticker_frame())
    frame["ts"] = str(int(socket._wall_clock() * 1000))
    transport.add_public(json.dumps(frame))
    stop = asyncio.Event()
    seen: list[object] = []

    async def _on_event(event: object) -> None:
        seen.append(event)

    task = asyncio.ensure_future(socket.run(_on_event, stop))
    # Drive the loop until run() has had a real chance to evaluate freshness
    # and act on it. Polling with a deadline is deterministic; a fixed sleep
    # is not.
    deadline = 0.0
    while deadline < 1.0 and socket._public is not None and socket._private is not None:
        await asyncio.sleep(0.01)
        deadline += 0.01
    # Assert BEFORE tearing the loop down: cancelling run() runs its finally
    # block, which closes both legs and would make the assertion below true
    # no matter what run() decided. That made this test vacuous.
    assert socket._public is None or socket._private is None, (
        "run() must close the legs when the stream is not fresh"
    )
    assert socket.state is not V2ConnectionState.CONNECTED
    stop.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await socket.close()


async def test_error_frame_raises_instead_of_being_dropped():
    """M5: an error frame must never be silently ignored."""
    with pytest.raises(WebSocketProtocolError):
        normalize_v2_frame(json.dumps({"event": "error", "code": "30016", "msg": "Param error"}))


async def test_login_acknowledgement_is_required():
    """M9: a non-success login ack must not yield CONNECTED."""
    transport = FakeTransport()
    transport.add_public(_TICKER, repeat=True)
    transport.add_private(json.dumps({"event": "login", "code": "30006", "msg": "bad key"}))
    socket = _make(transport)
    with pytest.raises(WebSocketProtocolError):
        await socket.connect()
    assert socket.state is not V2ConnectionState.CONNECTED
    await socket.close()


async def test_stale_state_recovers_to_connected():
    """M7: a stale verdict must not be a one-way latch."""
    transport = _two_leg_transport()
    clock = FakeClock()
    socket = _make(transport, clock=clock)
    await socket.connect()
    await socket.receive_once()
    transport.public._script = []
    transport.public._orig = [_TICKER, "pong"]
    transport.public._loop = True
    transport.add_public(_TICKER, repeat=True)
    clock.advance(20.0)
    await _settle(socket, transport)
    # The verdict is computed on observation; the state records it.
    assert socket.check_freshness() is False
    assert socket.state is V2ConnectionState.STALE
    # A private pong must restore liveness AND clear the STALE state.
    transport.add_private("pong", repeat=True)
    for _ in range(6):
        await _settle(socket, transport)
    # The verdict is recomputed per observation, so the state must follow.
    assert socket.check_freshness() is True, "private pong must restore liveness"
    # Assert the state BEFORE close(): close() is not part of this property and
    # can mask a stuck state depending on teardown ordering.
    assert socket.state is V2ConnectionState.CONNECTED, (
        "a stale verdict must not be a one-way latch"
    )
    await socket.close()
