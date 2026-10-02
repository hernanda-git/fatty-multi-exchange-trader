"""Finite, offline reader-error delivery and health regressions."""

import asyncio

import pytest
from test_bitget_ws_v2 import _LOGIN_OK, _position_frame, _ticker_frame
from ws_v2_fakes import FakeConnection, FakeTransport

from fatty_trader.exchanges.bitget.websocket_v2 import BitgetV2WebSocket, V2ConnectionState


class FiniteErrorConnection(FakeConnection):
    """Consume a finite script, then fail rather than silently blocking."""

    async def recv(self):
        if not self._script:
            raise ConnectionResetError("finite reader failed")
        return await super().recv()


class BurstConnection(FakeConnection):
    """Already-buffered frames arrive as a finite burst without yielding."""

    async def recv(self):
        if self._script:
            return self._script.pop(0)
        return await super().recv()


class BurstErrorConnection(BurstConnection):
    async def recv(self):
        if not self._script:
            raise ConnectionResetError("finite reader failed")
        return await super().recv()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_leg", ["public", "private"])
@pytest.mark.parametrize("prequeued", [True, False])
async def test_finite_frames_before_reader_error_delivered_once(failed_leg, prequeued):
    transport = FakeTransport(private_script=[_LOGIN_OK])
    frames = (
        [_ticker_frame("BTCUSDT"), _ticker_frame("ETHUSDT")]
        if failed_leg == "public"
        else [_LOGIN_OK, _position_frame("BTCUSDT"), _position_frame("ETHUSDT")]
    )
    error_connection = FiniteErrorConnection if prequeued else BurstErrorConnection
    setattr(transport, failed_leg, error_connection(frames))
    if failed_leg == "public":
        transport.add_private(_position_frame("BTCUSDT"))
        transport.add_private(_position_frame("ETHUSDT"))
    else:
        transport.add_public(_ticker_frame("BTCUSDT"))
        transport.add_public(_ticker_frame("ETHUSDT"))
    if not prequeued:
        other_leg = "private" if failed_leg == "public" else "public"
        setattr(transport, other_leg, BurstConnection(getattr(transport, other_leg)._script))
    socket = BitgetV2WebSocket(
        api_key="k",
        api_secret="s",
        passphrase="p",
        symbols=["BTCUSDT", "ETHUSDT"],
        transport=transport,
        wall_clock=lambda: 1700000000,
    )
    try:
        await socket.connect()
        if prequeued:
            for _ in range(12):
                await asyncio.sleep(0)
            # The error must close health BEFORE the consumer drains the queue.
            assert not socket.check_freshness()
            assert not socket.check_freshness("BTCUSDT")
        events = await socket.receive_once()
        assert [(event.kind, event.symbol) for event in events] == [
            ("mark_price", "BTCUSDT"),
            ("mark_price", "ETHUSDT"),
            ("position", "BTCUSDT"),
            ("position", "ETHUSDT"),
        ]
        assert not socket.check_freshness()
        assert not socket.check_freshness("BTCUSDT")
        assert socket.state is V2ConnectionState.RECONNECTING
        assert socket.stale_reason() == f"reader-error:{failed_leg}"
        assert all(queue.empty() for queue in socket._queues.values())
        with pytest.raises(ConnectionResetError, match="finite reader failed"):
            await socket.receive_once()
        with pytest.raises(ConnectionResetError, match="finite reader failed"):
            socket._drain_ready()
        # Surviving traffic cannot rehabilitate a failed logical stream.
        if failed_leg == "private":
            transport.add_public(_ticker_frame("SOLUSDT"))
        else:
            transport.add_private(_position_frame("SOLUSDT"))
        for _ in range(6):
            await asyncio.sleep(0)
        assert not socket.check_freshness()
        assert socket.state is V2ConnectionState.RECONNECTING
    finally:
        await socket.close()


@pytest.mark.asyncio
async def test_error_without_observations_raises_immediately_and_reconnect_resets_latch():
    transport = FakeTransport(private_script=[_LOGIN_OK])
    transport.public = FiniteErrorConnection([])
    socket = BitgetV2WebSocket(
        api_key="k",
        api_secret="s",
        passphrase="p",
        symbols=["BTCUSDT"],
        transport=transport,
        wall_clock=lambda: 1700000000,
    )
    try:
        await socket.connect()
        with pytest.raises(ConnectionResetError, match="finite reader failed"):
            await socket.receive_once()
        assert not socket.check_freshness("BTCUSDT")
        transport.public = FakeConnection([_ticker_frame()])
        transport.private = FakeConnection([_LOGIN_OK])
        await socket.reconnect()
        events = await socket.receive_once()
        assert [(event.kind, event.symbol) for event in events] == [("mark_price", "BTCUSDT")]
        assert socket._drain_ready() == []
        assert socket.check_freshness("BTCUSDT")
        assert socket.stale_reason() == "fresh"
    finally:
        await socket.close()
