"""Finite, offline regression evidence for audit E09-E11."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_bitget_monitor import ReadOnlyVenue
from test_bitget_websocket import FakeConnection as ClassicConnection
from test_bitget_websocket import FakeTransport as ClassicTransport
from test_bitget_ws_v2 import _LOGIN_OK, _position_frame, _ticker_frame
from ws_v2_fakes import FakeClock, FakeTransport

from fatty_trader.exchanges.bitget.websocket import BitgetClassicWebSocket
from fatty_trader.exchanges.bitget.websocket_v2 import BitgetV2WebSocket
from fatty_trader.exchanges.bitget.ws_models import BitgetWebSocketEvent
from fatty_trader.execution import bitget_fallback_protection
from fatty_trader.execution.bitget_monitor import BitgetMonitor
from fatty_trader.execution.bitget_protection_stream import (
    BitgetFallbackStreamEngine,
    BitgetProtectionStreamRuntime,
    StreamCloseResult,
)
from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository
from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository


class OfflineAuthenticatedVenue(ReadOnlyVenue):
    """Fake the authenticated environment, not a caller-selected provider scope."""

    environment = "DEMO"


@pytest.mark.asyncio
async def test_finite_prequeued_batch_delivered_once():
    transport = FakeTransport(
        public_script=[_ticker_frame()], private_script=[_LOGIN_OK, _position_frame()]
    )
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
        for _ in range(10):
            await asyncio.sleep(0)
        events = await socket.receive_once()
        assert [e.kind for e in events] == ["mark_price", "position"]
        assert socket._drain_ready() == []
        observed = []
        for event in events:
            observed.append((event.kind, event.symbol))
        assert observed == [("mark_price", "BTCUSDT"), ("position", "BTCUSDT")]
    finally:
        await socket.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["classic", "v2"])
async def test_marks_reject_stale_replayed_future_without_poisoning_watermark(version):
    clock = FakeClock()
    wall = [1700000000.0]

    def frame(ts):
        data = json.loads(_ticker_frame())
        data["ts"] = str(ts)
        if version == "classic":
            data["arg"]["instType"] = "mc"
            data["data"][0]["systemTime"] = ts
        return json.dumps(data)

    if version == "v2":
        transport = FakeTransport(private_script=[_LOGIN_OK])
        socket = BitgetV2WebSocket(
            api_key="k",
            api_secret="s",
            passphrase="p",
            symbols=["BTCUSDT"],
            transport=transport,
            clock=clock,
            wall_clock=lambda: wall[0],
        )

        async def feed(ts):
            transport.add_public(frame(ts))
            for _ in range(10):
                await asyncio.sleep(0)
            return socket._drain_ready()
    else:
        connection = ClassicConnection([_LOGIN_OK])
        socket = BitgetClassicWebSocket(
            api_key="k",
            api_secret="s",
            passphrase="p",
            symbols=["BTCUSDT"],
            transport=ClassicTransport([connection]),
            clock=clock,
            wall_clock=lambda: wall[0],
        )

        async def feed(ts):
            connection.incoming.append(frame(ts))
            return await socket.receive_once()

    await socket.connect()
    try:
        assert await feed(1700000000000)
        baseline = socket.last_mark_event_at("BTCUSDT")
        clock.advance(6)
        wall[0] += 6
        for ts in [1700000000000, 1699999999999, 1700000005999 - 10000, 1700000060000]:
            assert await feed(ts) == []
            assert socket.last_mark_event_at("BTCUSDT") == baseline
            assert not socket.check_freshness("BTCUSDT")
        assert await feed(1700000006000)
        assert socket.check_freshness("BTCUSDT")
    finally:
        await socket.close()


@pytest.mark.asyncio
async def test_runtime_provider_age_replay_and_transport_readiness():
    class Socket:
        healthy = True

        def check_freshness(self, symbol):
            return self.healthy

    socket = Socket()
    repository = InMemoryProtectionCapabilityRepository()
    now = [datetime.fromtimestamp(1700000000, UTC)]
    runtime = BitgetProtectionStreamRuntime(
        socket, repository, environment="DEMO", now=lambda: now[0]
    )

    def mark(ts):
        return BitgetWebSocketEvent(
            kind="mark_price", symbol="BTCUSDT", mark_price=Decimal("100"), event_time_ms=ts
        )

    await runtime._on_event(mark(1700000000000))
    baseline = repository.get("bitget", "DEMO", "BTCUSDT").last_stream_at
    now[0] = datetime.fromtimestamp(1700000006, UTC)
    for ts in [1700000000000, 1699999999999, 1700000060000]:
        await runtime._on_event(mark(ts))
        assert repository.get("bitget", "DEMO", "BTCUSDT").last_stream_at == baseline
    socket.healthy = False
    await runtime._on_event(mark(1700000006000))
    assert repository.get("bitget", "DEMO", "BTCUSDT").last_stream_at == baseline
    socket.healthy = True
    await runtime._on_event(mark(1700000006000))
    assert repository.get("bitget", "DEMO", "BTCUSDT").last_stream_at == now[0]


@pytest.mark.asyncio
async def test_engine_rejects_old_first_and_future_and_unready_transport():
    calls = []

    async def close(request):
        calls.append(request)
        return StreamCloseResult(request.fallback_id, True, "submitted")

    entry = dict(
        id="a", symbol="BTCUSDT", direction="LONG", entry_price="100", stop_loss="95", quantity="1"
    )
    ready = [False]
    legacy = BitgetFallbackStreamEngine(lambda: [entry], close)
    assert (
        await legacy.handle_event(
            BitgetWebSocketEvent(
                kind="mark_price", symbol="BTCUSDT", mark_price=Decimal("94"), event_time_ms=1
            )
        )
        == []
    )
    engine = BitgetFallbackStreamEngine(
        lambda: [entry],
        close,
        now=lambda: datetime.fromtimestamp(1700000000, UTC),
        transport_fresh=lambda symbol: ready[0],
    )

    def mark(ts):
        return BitgetWebSocketEvent(
            kind="mark_price", symbol="BTCUSDT", mark_price=Decimal("94"), event_time_ms=ts
        )

    for ts in [1699999990000, 1700000060000, 1700000000000]:
        assert await engine.handle_event(mark(ts)) == []
    assert engine.last_event_ms("BTCUSDT") is None
    ready[0] = True
    assert await engine.handle_event(mark(1700000000000))
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_anomaly", [False, True])
@pytest.mark.parametrize("reason", ["sl_hit", "tp_hit"])
async def test_existing_fallback_positions_survive_stop_and_entry_anomaly(
    monkeypatch, entry_anomaly, reason
):
    repository = InMemoryReconciliationRepository()
    if entry_anomaly:
        repository.latch_kill_switch("bitget", "unexpected-order:foreign")
    remaining = ["BTCUSDT", "ETHUSDT"]
    calls = []

    async def run(client, *, environment):
        assert environment == "DEMO"
        calls.append(tuple(remaining))
        symbol = remaining.pop(0)
        return [dict(symbol=symbol, reason=reason)]

    monkeypatch.setattr(bitget_fallback_protection, "run_fallback_monitor_async", run)
    monitor = BitgetMonitor(
        OfflineAuthenticatedVenue(), repository, fallback_mutations_enabled=True
    )
    await monitor.run_once()
    await monitor.run_once()
    assert calls == [("BTCUSDT", "ETHUSDT"), ("ETHUSDT",)]
    assert repository.kill_switch_active("bitget") is entry_anomaly


@pytest.mark.asyncio
async def test_explicit_fallback_mutation_gate_stays_closed(monkeypatch):
    async def forbidden(client, *, environment):
        raise AssertionError("mutation gate bypassed")

    monkeypatch.setattr(bitget_fallback_protection, "run_fallback_monitor_async", forbidden)
    repository = InMemoryReconciliationRepository()
    repository.latch_kill_switch("bitget", "unexpected-order:foreign")
    await BitgetMonitor(ReadOnlyVenue(), repository, fallback_mutations_enabled=False).run_once()


@pytest.mark.asyncio
async def test_runtime_real_v2_private_silence_cannot_be_overridden_by_public_mark():
    clock = FakeClock()
    wall = [1700000000.0]
    transport = FakeTransport(public_script=[_ticker_frame()], private_script=[_LOGIN_OK])
    socket = BitgetV2WebSocket(
        api_key="k",
        api_secret="s",
        passphrase="p",
        symbols=["BTCUSDT"],
        transport=transport,
        clock=clock,
        wall_clock=lambda: wall[0],
        private_silent_after=15,
    )
    repository = InMemoryProtectionCapabilityRepository()
    runtime = BitgetProtectionStreamRuntime(
        socket, repository, environment="DEMO", now=lambda: datetime.fromtimestamp(wall[0], UTC)
    )
    try:
        await socket.connect()
        for _ in range(10):
            await asyncio.sleep(0)
        for event in await socket.receive_once():
            await runtime._on_event(event)
        baseline = repository.get("bitget", "DEMO", "BTCUSDT").last_stream_at
        clock.advance(20)
        wall[0] += 20
        frame = json.loads(_ticker_frame())
        frame["ts"] = str(int(wall[0] * 1000))
        transport.add_public(json.dumps(frame))
        for _ in range(10):
            await asyncio.sleep(0)
        for event in await socket.receive_once():
            await runtime._on_event(event)
        assert socket.stale_reason() == "private-silent"
        assert repository.get("bitget", "DEMO", "BTCUSDT").last_stream_at == baseline
    finally:
        await socket.close()


@pytest.mark.asyncio
async def test_engine_multiple_positions_single_cycle():
    calls = []

    async def close(request):
        calls.append(request.fallback_id)
        return StreamCloseResult(request.fallback_id, True, "submitted")

    entries = [
        dict(
            id=id,
            symbol="BTCUSDT",
            direction="LONG",
            entry_price="100",
            stop_loss="95",
            quantity="1",
        )
        for id in ["a", "b"]
    ]
    engine = BitgetFallbackStreamEngine(
        lambda: entries,
        close,
        now=lambda: datetime.fromtimestamp(1700000000, UTC),
        transport_fresh=lambda symbol: True,
    )
    event = BitgetWebSocketEvent(
        kind="mark_price", symbol="BTCUSDT", mark_price=Decimal("94"), event_time_ms=1700000000000
    )
    assert len(await engine.handle_event(event)) == 2
    assert await engine.handle_event(event) == []
    assert calls == ["a", "b"]


@pytest.mark.asyncio
async def test_classic_fresh_public_mark_does_not_override_missing_account_heartbeat():
    clock = FakeClock()
    wall = [1700000000.0]
    connection = ClassicConnection([_LOGIN_OK])
    socket = BitgetClassicWebSocket(
        api_key="k",
        api_secret="s",
        passphrase="p",
        symbols=["BTCUSDT"],
        transport=ClassicTransport([connection]),
        clock=clock,
        wall_clock=lambda: wall[0],
        heartbeat_interval=10,
    )
    await socket.connect()
    try:
        clock.advance(21)
        wall[0] += 21
        frame = dict(
            action="update",
            arg=dict(instType="mc", channel="ticker", instId="BTCUSDT"),
            data=[dict(instId="BTCUSDT", markPrice="100", systemTime=int(wall[0] * 1000))],
        )
        connection.incoming.append(json.dumps(frame))
        assert await socket.receive_once()
        assert not socket.check_freshness("BTCUSDT")
    finally:
        await socket.close()


@pytest.mark.parametrize("timestamp", [1700000000001, 10**400])
def test_future_mark_timestamps_are_rejected_without_overflow(timestamp):
    from fatty_trader.exchanges.bitget.websocket import fresh_mark

    event = BitgetWebSocketEvent(
        kind="mark_price",
        symbol="BTCUSDT",
        mark_price=Decimal("100"),
        event_time_ms=timestamp,
    )
    assert not fresh_mark(event, 1700000000.0, 5.0)


@pytest.mark.asyncio
async def test_provider_age_is_preserved_in_runtime_capability():
    class Socket:
        def check_freshness(self, symbol):
            return True

    repository = InMemoryProtectionCapabilityRepository()
    runtime = BitgetProtectionStreamRuntime(
        Socket(),
        repository,
        environment="DEMO",
        now=lambda: datetime.fromtimestamp(1700000004, UTC),
    )
    await runtime._on_event(
        BitgetWebSocketEvent(
            kind="mark_price",
            symbol="BTCUSDT",
            mark_price=Decimal("100"),
            event_time_ms=1700000000000,
        )
    )
    capability = repository.get("bitget", "DEMO", "BTCUSDT")
    assert capability.last_stream_at == datetime.fromtimestamp(1700000000, UTC)


@pytest.mark.asyncio
async def test_fallback_unknown_close_latches_entry_but_next_protection_cycle_runs(monkeypatch):
    calls = []

    async def run(client, *, environment):
        assert environment == "DEMO"
        calls.append(client)
        return [dict(symbol="BTCUSDT", reason="close-result-unknown")]

    monkeypatch.setattr(bitget_fallback_protection, "run_fallback_monitor_async", run)
    repository = InMemoryReconciliationRepository()
    monitor = BitgetMonitor(
        OfflineAuthenticatedVenue(), repository, fallback_mutations_enabled=True
    )
    first = await monitor.run_once()
    assert "fallback-close-result-unknown:BTCUSDT" in first.reasons
    assert repository.kill_switch_active("bitget")
    await monitor.run_once()
    assert len(calls) == 2
