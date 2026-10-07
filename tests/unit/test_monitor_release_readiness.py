"""Actual transport-owned facts, not a second socket or a timer."""

import asyncio
import json

import pytest
from ws_v2_fakes import FakeClock, FakeTransport

from fatty_trader.exchanges.bitget.websocket_v2 import BitgetV2WebSocket
from fatty_trader.exchanges.bitget.ws_v2_models import V2_PRIVATE_CHANNELS

LOGIN = json.dumps({"event": "login", "code": "0"})


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [None, False, "30016", ""])
async def test_login_without_explicit_success_code_cannot_prove_readiness(code):
    from fatty_trader.exchanges.bitget.ws_models import WebSocketProtocolError

    frame = {"event": "login"}
    if code is not None:
        frame["code"] = code
    socket = BitgetV2WebSocket(
        api_key="k",
        api_secret="s",
        passphrase="p",
        symbols=[],
        transport=FakeTransport(private_script=[json.dumps(frame)]),
    )
    with pytest.raises(WebSocketProtocolError):
        await socket.connect()
    assert not socket.release_readiness()["ready"]


@pytest.mark.asyncio
async def test_provider_error_bodies_are_not_printed_by_worker(capsys):
    error = json.dumps({"event": "error", "code": "30016", "msg": "private-secret-body"})
    transport = FakeTransport(private_script=[LOGIN, error])
    socket = BitgetV2WebSocket(
        api_key="k", api_secret="s", passphrase="p", symbols=[], transport=transport
    )
    task = asyncio.create_task(socket.run(lambda _: asyncio.sleep(0), asyncio.Event()))
    try:
        await asyncio.sleep(0.02)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert "private-secret-body" not in capsys.readouterr().out


def ack(channel, code=None):
    payload = {
        "event": "subscribe",
        "arg": {"instType": "USDT-FUTURES", "channel": channel, "instId": "default"},
    }
    if code is not None:
        payload["code"] = code
    return json.dumps(payload)


async def settle():
    for _ in range(15):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_private_proof_requires_all_acks_and_real_private_pong_even_without_tickers():
    clock = FakeClock()
    transport = FakeTransport(private_script=[LOGIN])
    socket = BitgetV2WebSocket(
        api_key="k", api_secret="s", passphrase="p", symbols=[], clock=clock, transport=transport
    )
    await socket.connect()
    try:
        proof = getattr(socket, "release_readiness", lambda: {})()
        assert proof.get("login_ack_at") == clock()
        assert proof["pong_at"] is None
        assert socket.last_pong_at is None
        assert not proof["ready"]
        transport.add_public("pong")
        for channel in V2_PRIVATE_CHANNELS:
            transport.add_private(ack(channel))
        await settle()
        assert not socket.release_readiness()["ready"]
        transport.add_private("pong")
        await settle()
        proof = socket.release_readiness()
        assert proof["ready"]
        assert proof["watched_symbols"] == []
        assert proof["watched_symbols_healthy"] is None
        assert proof["subscription_acks"] == sorted(V2_PRIVATE_CHANNELS)
        clock.advance(70)
        assert not socket.release_readiness()["ready"]
    finally:
        await socket.close()


@pytest.mark.asyncio
async def test_reconnect_and_failed_ack_cannot_reuse_private_facts():
    transport = FakeTransport(
        private_script=[LOGIN, *[ack(c) for c in V2_PRIVATE_CHANNELS], "pong"]
    )
    socket = BitgetV2WebSocket(
        api_key="k", api_secret="s", passphrase="p", symbols=[], transport=transport
    )
    await socket.connect()
    await settle()
    generation = socket.release_readiness()["connection_generation"]
    assert socket.release_readiness()["ready"]
    transport.add_private(LOGIN)
    await socket.reconnect()
    try:
        proof = socket.release_readiness()
        assert proof["connection_generation"] != generation
        assert proof["subscription_acks"] == []
        assert proof["pong_at"] is None
        transport.add_private(ack("positions", "30016"))
        transport.add_private("pong")
        await settle()
        assert not socket.release_readiness()["ready"]
    finally:
        await socket.close()


def test_worker_publication_requires_clean_monitor_clock_and_watchdog(tmp_path):
    from types import SimpleNamespace

    from fatty_trader.execution import bitget_release_readiness as readiness

    private = dict(
        ready=True,
        connection_generation="a" * 32,
        login_ack_at=100.0,
        subscription_acks=sorted(V2_PRIVATE_CHANNELS),
        pong_at=100.0,
        pong_max_age_seconds=65,
        watched_symbols=[],
        watched_symbols_healthy=None,
    )
    socket = SimpleNamespace(release_readiness=lambda: private)
    publisher = readiness.MonitorReadinessPublisher(
        {
            "BITGET_MODE": "LIVE",
            "BITGET_API_KEY": "test-key",
            "BITGET_MONITOR_READINESS_PATH": str(tmp_path / "proof.json"),
        },
        socket=socket,
        clock=lambda: 100.0,
        wall_clock=lambda: 1000.0,
    )
    try:
        assert not json.loads(publisher.path.read_text())["ready"]
        report = SimpleNamespace(
            status="kill-switch-latched",
            reasons=(),
            observed_at=1000.0,
            observed_monotonic=100.0,
            clock_observation=dict(
                observed_at=1000.0,
                observed_monotonic=100.0,
                lower_ms=-10.0,
                upper_ms=10.0,
                bound_ms=10000,
                clean=True,
            ),
        )
        publisher.monitor_completed(report)
        assert not json.loads(publisher.path.read_text())["ready"]
        publisher.watchdog_completed(
            SimpleNamespace(status="HEALTHY", reasons=()),
            observed_at=1000.0,
            observed_monotonic=100.0,
        )
        proof = json.loads(publisher.path.read_text())
        assert proof["ready"]
        assert proof["owner_token"] == publisher.owner_token
        assert proof["schema"] == readiness.SCHEMA
        publisher.monitor_completed(SimpleNamespace(status="degraded", reasons=("read-failed",)))
        assert not json.loads(publisher.path.read_text())["ready"]
    finally:
        publisher.close()
    assert not publisher.path.exists()


@pytest.mark.asyncio
async def test_monitor_loop_wires_readiness_cycles_and_closes_on_exit():
    from test_bitget_monitor_runtime import FakeMonitor, FakeStream, FakeWatchdog

    from fatty_trader.service import run_bitget_monitor_loop

    stop = asyncio.Event()
    events = []

    class Publication:
        def monitor_completed(self, report):
            events.append("monitor")

        def watchdog_completed(self, report, **kwargs):
            assert kwargs["observed_monotonic"] is not None
            events.append("watchdog")

        def close(self):
            events.append("closed")

    await run_bitget_monitor_loop(
        FakeMonitor(),
        interval=1,
        stream=FakeStream(),
        watchdog=FakeWatchdog(stop),
        stop_event=stop,
        readiness=Publication(),
    )
    assert "monitor" in events and "watchdog" in events
    assert events[-1] == "closed"


@pytest.mark.asyncio
async def test_actual_watchdog_empty_set_does_not_green_missing_private_proof():
    from datetime import UTC, datetime

    from fatty_trader.execution.bitget_protection_watchdog import BitgetProtectionWatchdog
    from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository

    transport = FakeTransport(private_script=[LOGIN])
    socket = BitgetV2WebSocket(
        api_key="k", api_secret="s", passphrase="p", symbols=[], transport=transport
    )
    await socket.connect()
    try:

        async def position(_):
            raise AssertionError("empty watch set is not a provider read")

        watchdog = BitgetProtectionWatchdog(
            socket,
            InMemoryProtectionCapabilityRepository(),
            environment="LIVE",
            symbols=[],
            read_position=position,
            now=lambda: datetime.now(UTC),
        )
        report = await watchdog.run_once()
        assert report.status == "FAILED"
        assert "private-stream-unavailable" in report.reasons
        for channel in V2_PRIVATE_CHANNELS:
            transport.add_private(ack(channel))
        transport.add_private("pong")
        await settle()
        assert (await watchdog.run_once()).status == "HEALTHY"
        await socket.subscribe_symbols(["BTCUSDT"])
        watchdog.refresh_symbols(["BTCUSDT"])

        async def flat_position(_):
            return []

        watchdog._read_position = flat_position
        report = await watchdog.run_once()
        assert report.status == "STALE"
        assert "socket-not-connected" not in report.reasons
        assert not socket.release_readiness()["ready"]
        assert socket.release_readiness()["account_stream_fresh"]
    finally:
        await socket.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("latency, step, clean", [(0.02, 0, True), (6, 0, False), (0.02, 1, False)])
async def test_actual_monitor_carries_pre_read_cycle_time_and_clock_interval(latency, step, clean):
    from test_bitget_monitor import ReadOnlyVenue

    from fatty_trader.execution.bitget_monitor import BitgetMonitor
    from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository

    mono = FakeClock(100)
    wall = FakeClock(1000)

    class Venue(ReadOnlyVenue):
        async def get_server_time_ms(self):
            mono.advance(latency)
            wall.advance(latency + step)
            return 1000000 + int(latency * 500)

    monitor = BitgetMonitor(
        Venue(), InMemoryReconciliationRepository(), clock=mono, wall_clock=wall
    )
    report = await monitor.run_once()
    assert report.observed_at == 1000
    assert report.observed_monotonic == 100
    assert report.clock_observation["clean"] is clean
    if clean:
        assert report.clock_observation["lower_ms"] == pytest.approx(-10)
        assert report.clock_observation["upper_ms"] == pytest.approx(10)
        assert report.reasons == ()
    else:
        assert "clock-skew-unavailable" in report.reasons


def ready_publisher(tmp_path):
    import time
    from types import SimpleNamespace

    from fatty_trader.execution.bitget_release_readiness import MonitorReadinessPublisher

    mono, wall = time.monotonic(), time.time()
    private = dict(
        ready=True,
        connection_generation="a" * 32,
        login_ack_at=mono,
        subscription_acks=sorted(V2_PRIVATE_CHANNELS),
        pong_at=mono,
        pong_max_age_seconds=65,
        watched_symbols=[],
        watched_symbols_healthy=None,
    )
    publisher = MonitorReadinessPublisher(
        {
            "BITGET_MODE": "LIVE",
            "BITGET_API_KEY": "key",
            "BITGET_MONITOR_READINESS_PATH": str(tmp_path / "proof.json"),
        },
        socket=SimpleNamespace(release_readiness=lambda: private),
    )
    publisher.monitor_completed(
        SimpleNamespace(
            status="ok",
            reasons=(),
            observed_at=wall,
            observed_monotonic=mono,
            clock_observation=dict(
                observed_at=wall,
                observed_monotonic=mono,
                lower_ms=-10,
                upper_ms=10,
                bound_ms=10000,
                clean=True,
            ),
        )
    )
    publisher.watchdog_completed(
        SimpleNamespace(status="HEALTHY", reasons=()), observed_at=wall, observed_monotonic=mono
    )
    return publisher, private


def test_reader_requires_current_owner_challenge_not_file_or_old_connection(tmp_path):
    from fatty_trader.execution import bitget_release_readiness as readiness

    publisher, private = ready_publisher(tmp_path)
    try:
        proof = readiness.read_monitor_readiness(publisher.path)
        assert proof["owner_token"] == publisher.owner_token
        private["connection_generation"] = "b" * 32
        with pytest.raises(readiness.ReadinessUnavailable):
            readiness.read_monitor_readiness(publisher.path)
        private["connection_generation"] = "a" * 32
        private["ready"] = False
        with pytest.raises(readiness.ReadinessUnavailable):
            readiness.read_monitor_readiness(publisher.path)
    finally:
        content = publisher.path.read_text()
        publisher.close()
    publisher.path.write_text(content)
    with pytest.raises(readiness.ReadinessUnavailable):
        readiness.read_monitor_readiness(publisher.path)


def test_reader_reages_independent_cycle_pong_and_clock_after_wait(tmp_path):
    import time

    from fatty_trader.execution import bitget_release_readiness as readiness

    publisher, _ = ready_publisher(tmp_path)
    try:
        assert readiness.read_monitor_readiness(publisher.path)["ready"]
        time.sleep(0.025)
        with pytest.raises(readiness.ReadinessUnavailable):
            readiness.read_monitor_readiness(publisher.path, max_age_seconds=0.01)
    finally:
        publisher.close()


def test_dead_or_stopped_worker_cannot_attest_young_metadata(tmp_path):
    import os
    import select
    import signal
    import subprocess
    import sys
    from pathlib import Path

    from fatty_trader.execution import bitget_release_readiness as readiness

    script = (
        "import sys,time; from pathlib import Path; "
        "sys.path.insert(0, sys.argv[2]); "
        "from test_monitor_release_readiness import ready_publisher; "
        "publisher,_=ready_publisher(Path(sys.argv[1])); "
        "print('started',flush=True); time.sleep(60)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path), str(Path(__file__).parent)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline().strip() == "started"
        path = tmp_path / "proof.json"
        assert readiness.read_monitor_readiness(path)["pid"] == process.pid
        os.kill(process.pid, signal.SIGSTOP)
        with pytest.raises(readiness.ReadinessUnavailable):
            readiness.read_monitor_readiness(path)
        os.kill(process.pid, signal.SIGCONT)
        assert readiness.read_monitor_readiness(path)["ready"]
        process.kill()
        process.wait(timeout=5)
        assert path.exists()
        with pytest.raises(readiness.ReadinessUnavailable):
            readiness.read_monitor_readiness(path)
    finally:
        if process.poll() is None:
            os.kill(process.pid, signal.SIGCONT)
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()


@pytest.mark.parametrize("field", ["pong_at", "login_ack_at"])
def test_null_private_times_refuse_release(tmp_path, field):
    from fatty_trader.execution import bitget_release_readiness as readiness

    publisher, private = ready_publisher(tmp_path)
    try:
        private[field] = None
        publisher.publish()
        with pytest.raises(readiness.ReadinessUnavailable):
            readiness.read_monitor_readiness(publisher.path)
    finally:
        publisher.close()


def test_shared_progress_identity_and_single_socket_with_readiness(tmp_path):
    import time
    from types import SimpleNamespace

    from fatty_trader.execution import bitget_release_readiness as readiness

    generic = tmp_path / "progress.json"
    endpoint = tmp_path / "owner.sock"
    private = dict(
        ready=True,
        connection_generation="a" * 32,
        login_ack_at=time.monotonic(),
        subscription_acks=sorted(V2_PRIVATE_CHANNELS),
        pong_at=time.monotonic(),
        watched_symbols=[],
        watched_symbols_healthy=None,
    )
    publisher = readiness.MonitorReadinessPublisher(
        {
            "BITGET_MODE": "LIVE",
            "BITGET_API_KEY": "key",
            "BITGET_MONITOR_READINESS_PATH": str(tmp_path / "release.json"),
            "WORKER_HEALTH_PATH": str(generic),
            "WORKER_HEALTH_SOCKET_PATH": str(endpoint),
        },
        socket=SimpleNamespace(release_readiness=lambda: private),
    )
    try:
        assert endpoint.exists()
        assert not generic.exists()
        mono, wall = time.monotonic(), time.time()
        publisher.monitor_completed(
            SimpleNamespace(
                status="ok",
                reasons=(),
                observed_at=wall,
                observed_monotonic=mono,
                clock_observation=dict(
                    observed_at=wall,
                    observed_monotonic=mono,
                    lower_ms=-10,
                    upper_ms=10,
                    bound_ms=10000,
                    clean=True,
                ),
            )
        )
        publisher.watchdog_completed(
            SimpleNamespace(status="HEALTHY", reasons=()), observed_at=wall, observed_monotonic=mono
        )
        assert json.loads(generic.read_text())["owner_token"] == publisher.owner_token
        assert readiness.read_monitor_readiness(publisher.path, socket_path=endpoint)["ready"]
        publisher.invalidate("watchdog-cycle-failed")
        assert not generic.exists()
        with pytest.raises(readiness.ReadinessUnavailable):
            readiness.read_monitor_readiness(publisher.path, socket_path=endpoint)
    finally:
        publisher.close()
    assert not endpoint.exists() and not generic.exists()


@pytest.mark.asyncio
async def test_failed_empty_symbol_source_cannot_publish_ready(tmp_path):
    from types import SimpleNamespace

    from fatty_trader.execution import bitget_release_readiness as readiness
    from fatty_trader.execution.bitget_protection_stream import BitgetProtectionStreamRuntime

    publisher, private = ready_publisher(tmp_path)

    class Socket:
        symbols = ()

        def release_readiness(self):
            return private

        async def subscribe_symbols(self, symbols):
            return ()

        async def unsubscribe_symbols(self, symbols):
            return ()

    failed = [False]

    def active_symbols():
        if failed[0]:
            raise RuntimeError("do-not-log-secret")
        return []

    stream = BitgetProtectionStreamRuntime(
        Socket(), SimpleNamespace(), environment="LIVE", active_symbol_source=active_symbols
    )
    try:
        assert getattr(stream, "symbol_source_ready", None) is False
        await stream.sync_active_symbols()
        assert stream.symbol_source_ready is True
        publisher._owned_stream = stream
        publisher.publish()
        assert readiness.read_monitor_readiness(publisher.path)["ready"]
        failed[0] = True
        await stream.sync_active_symbols()
        with pytest.raises(readiness.ReadinessUnavailable):
            readiness.read_monitor_readiness(publisher.path)
        publisher.publish()
        assert not json.loads(publisher.path.read_text())["ready"]
    finally:
        publisher.close()


@pytest.mark.asyncio
async def test_watchdog_exception_invalidates_previous_good_publication(tmp_path):
    from fatty_trader.execution import bitget_release_readiness as readiness
    from fatty_trader.service import _run_bitget_watchdog_loop

    publisher, _ = ready_publisher(tmp_path)

    class Watchdog:
        async def run_once(self):
            raise RuntimeError("do-not-publish-raw-provider-body")

    try:
        assert readiness.read_monitor_readiness(publisher.path)["ready"]
        with pytest.raises(RuntimeError):
            await _run_bitget_watchdog_loop(Watchdog(), 1, asyncio.Event(), readiness=publisher)
        with pytest.raises(readiness.ReadinessUnavailable):
            readiness.read_monitor_readiness(publisher.path)
        assert "do-not-publish" not in publisher.path.read_text()
    finally:
        publisher.close()


@pytest.mark.asyncio
async def test_actual_worker_socket_cycles_attest_without_second_provider_connection(tmp_path):
    import time

    from test_bitget_monitor import ReadOnlyVenue

    from fatty_trader.execution import bitget_release_readiness as readiness
    from fatty_trader.execution.bitget_monitor import BitgetMonitor
    from fatty_trader.execution.bitget_protection_stream import BitgetProtectionStreamRuntime
    from fatty_trader.execution.bitget_protection_watchdog import BitgetProtectionWatchdog
    from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository
    from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository

    transport = FakeTransport(private_script=[LOGIN])
    socket = BitgetV2WebSocket(
        api_key="k", api_secret="s", passphrase="p", symbols=[], transport=transport
    )
    capabilities = InMemoryProtectionCapabilityRepository()
    stream = BitgetProtectionStreamRuntime(
        socket, capabilities, environment="LIVE", active_symbol_source=lambda: []
    )

    class Venue(ReadOnlyVenue):
        async def get_server_time_ms(self):
            return int(time.time() * 1000)

    async def no_position_read(_):
        raise AssertionError("empty tickers must not manufacture a position read")

    from datetime import UTC, datetime

    monitor = BitgetMonitor(Venue(), InMemoryReconciliationRepository())
    watchdog = BitgetProtectionWatchdog(
        socket, capabilities, environment="LIVE", symbols=[], read_position=no_position_read,
        now=lambda: datetime.now(UTC),
    )
    await socket.connect()
    publisher = readiness.MonitorReadinessPublisher(
        {"BITGET_MODE": "LIVE", "BITGET_API_KEY": "k",
         "BITGET_MONITOR_READINESS_PATH": str(tmp_path / "release.json")},
        socket=socket, stream=stream,
    )

    async def completed_cycles():
        await stream.sync_active_symbols()
        publisher.monitor_completed(await monitor.run_once())
        wall, mono = time.time(), time.monotonic()
        publisher.watchdog_completed(
            await watchdog.run_once(), observed_at=wall, observed_monotonic=mono
        )

    async def read():
        return await asyncio.to_thread(readiness.read_monitor_readiness, publisher.path)

    try:
        await completed_cycles()
        with pytest.raises(readiness.ReadinessUnavailable):
            await read()
        for channel in V2_PRIVATE_CHANNELS:
            transport.add_private(ack(channel))
        transport.add_public("pong")
        await settle()
        await completed_cycles()
        with pytest.raises(readiness.ReadinessUnavailable):
            await read()
        transport.add_private("pong")
        await settle()
        await completed_cycles()
        proof = await read()
        assert proof["private"]["account_stream_fresh"] is True
        assert proof["private"]["watched_symbols_healthy"] is None
        assert proof["monitor"]["clean"] and proof["watchdog"]["clean"]
        assert proof["clock"]["clean"]
        assert len(transport.urls) == 2  # Reader never creates a provider socket.
        transport.add_private(LOGIN)
        await socket.reconnect()
        with pytest.raises(readiness.ReadinessUnavailable):
            await read()
        for channel in V2_PRIVATE_CHANNELS:
            transport.add_private(ack(channel))
        transport.add_private("pong")
        await settle()
        publisher.publish()
        with pytest.raises(readiness.ReadinessUnavailable):
            await read()  # Reconnected account requires new actual provider cycles.
        await completed_cycles()
        assert (await read())["private"]["connection_generation"] != proof["private"][
            "connection_generation"
        ]
        assert len(transport.urls) == 4
    finally:
        publisher.close()
        await socket.close()


@pytest.mark.asyncio
async def test_release_proof_rejects_watched_mark_from_future_monotonic_time():
    from test_bitget_ws_v2 import _ticker_frame

    clock = FakeClock(100)
    transport = FakeTransport(
        private_script=[LOGIN, *[ack(c) for c in V2_PRIVATE_CHANNELS], "pong"]
    )
    socket = BitgetV2WebSocket(
        api_key="k", api_secret="s", passphrase="p", symbols=["BTCUSDT"],
        clock=clock, wall_clock=lambda: 1700000000.0, transport=transport,
    )
    await socket.connect()
    try:
        await settle()
        clock.advance(10)
        transport.add_public(_ticker_frame())
        await settle()
        assert socket.release_readiness()["ready"]
        clock.advance(-5)
        proof = socket.release_readiness()
        assert proof["account_stream_fresh"]
        assert proof["watched_symbols_healthy"] is False
        assert proof["ready"] is False
    finally:
        await socket.close()


def test_cycle_before_current_connection_login_is_not_release_evidence(tmp_path):
    import time

    from fatty_trader.execution import bitget_release_readiness as readiness

    publisher, private = ready_publisher(tmp_path)
    try:
        private["login_ack_at"] = time.monotonic()
        publisher.publish()
        with pytest.raises(readiness.ReadinessUnavailable):
            readiness.read_monitor_readiness(publisher.path)
    finally:
        publisher.close()
