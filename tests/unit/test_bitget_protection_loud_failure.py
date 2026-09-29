"""Make Bitget protection-stream failure loud instead of silent.

Covers the failure shapes that let a broken protection stream sit unnoticed
for hours, and the two places where a naive fix makes the system *less* safe:

  1. WebSocket connect failures are logged with a consecutive-failure count.
  2. Socket death latches an entry block, independent of the symbol set.
  3. Per-symbol quiet is reported but never halts the venue.
  4. The latch uses a dedicated scope, so it cannot disable fallback TP/SL.
  5. A DB error while latching fails loud without killing the monitor.
  6. The capability gate blocks entries for symbols with no fresh protection.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from fatty_trader.exchanges.bitget.websocket import (
    BitgetClassicWebSocket,
    WebSocketConnectionState,
)
from fatty_trader.execution.bitget_protection_watchdog import (
    PROTECTION_STREAM_SCOPE,
    BitgetProtectionWatchdog,
    WatchdogStatus,
)
from fatty_trader.service import build_bitget_protection_admission
from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository
from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository

_NOW = datetime(2026, 9, 29, 2, 0, tzinfo=UTC)


class _RefusingTransport:
    """Transport that always fails to connect, like Bitget's retired v1 URL."""

    def __init__(self) -> None:
        self.attempts = 0

    async def connect(self, url: str) -> Any:
        self.attempts += 1
        raise ConnectionRefusedError(f"server rejected WebSocket connection: HTTP 404 ({url})")


class _StaleSocket:
    """Connected socket whose per-symbol freshness can be controlled."""

    def __init__(self, *, fresh: bool = False) -> None:
        self.symbols = ("HBARUSDT",)
        self.state = WebSocketConnectionState.CONNECTED
        self._fresh = fresh

    def check_freshness(self, symbol: str | None = None) -> bool:
        return self._fresh


class _DeadSocket(_StaleSocket):
    """A socket that never opened, regardless of the symbol set.

    This is the shape of the retired-endpoint incident: the handshake fails
    with HTTP 404 forever. The watchdog must catch this even with zero symbols.
    """

    def __init__(self) -> None:
        super().__init__(fresh=False)
        self.state = WebSocketConnectionState.FAILED


class _RecordingKillSwitch:
    """Latch target that records what was latched, using the real repo API."""

    def __init__(self) -> None:
        self.latched: list[tuple[str, str]] = []

    def is_active(self, scope: str) -> bool:
        return any(s == scope for s, _ in self.latched)

    def latch_kill_switch(self, scope: str, reason: str) -> None:
        if not self.is_active(scope):
            self.latched.append((scope, reason))


class _ExplodingKillSwitch:
    """Latch target whose database write fails, as during a Postgres outage."""

    def __init__(self) -> None:
        self.attempts = 0

    def is_active(self, scope: str) -> bool:
        return False

    def latch_kill_switch(self, scope: str, reason: str) -> None:
        self.attempts += 1
        raise RuntimeError("postgres unavailable")


def _log_event():
    async def on_event(_: Any) -> None:
        return None

    return on_event


def _build(socket: Any, kill_switch: Any, *, threshold: int = 3, symbols: Any = None) -> Any:
    async def read_position(_: str) -> list[dict[str, str]]:
        return []

    return BitgetProtectionWatchdog(
        socket,
        InMemoryProtectionCapabilityRepository(),
        environment="LIVE",
        symbols=["HBARUSDT"] if symbols is None else symbols,
        read_position=read_position,
        now=lambda: _NOW,
        kill_switch=kill_switch,
        stale_cycles_before_latch=threshold,
    )


# --------------------------------------------------------------------------
# 1. Loud transport failure
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_failure_is_logged_with_consecutive_count(capsys: Any) -> None:
    """A refused handshake must emit a log line, not just retry silently."""
    socket = BitgetClassicWebSocket(
        api_key="k",
        api_secret="s",
        passphrase="p",
        symbols=["HBARUSDT"],
        transport=_RefusingTransport(),
        clock=lambda: 0.0,
        wall_clock=lambda: 0.0,
        reconnect_base_delay=0.001,
        reconnect_max_delay=0.001,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(socket.run(_log_event(), stop))
    await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    joined = capsys.readouterr().out
    assert "connect_error" in joined
    assert "consecutive_failures=1" in joined
    assert "HTTP 404" in joined


def test_reconnect_delay_never_overflows() -> None:
    """2**(attempt-1) raises OverflowError past ~1024 attempts.

    On a permanently dead endpoint that is ~8.5h, and the exception was
    swallowed by the reconnect handler, leaving a tight no-sleep spin loop at
    100% CPU emitting one log line per iteration.
    """
    socket = BitgetClassicWebSocket(api_key="k", api_secret="s", passphrase="p", symbols=[])
    assert socket.reconnect_delay(1024) > 0
    assert socket.reconnect_delay(100_000) == socket.reconnect_delay(1024)


# --------------------------------------------------------------------------
# 2. Socket death latches, independent of the symbol set
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_socket_latches_with_no_active_fallback_positions() -> None:
    """The latch must not depend on the symbol set being non-empty.

    The symbol set is derived from active fallback rows, so it is empty in the
    normal steady state and immediately after a crash or liquidation. The
    retired-v1-endpoint incident happened exactly there.
    """
    latch = _RecordingKillSwitch()
    watchdog = _build(_DeadSocket(), latch, symbols=[])
    assert watchdog._symbols == (), "precondition: no active fallback positions"

    for _ in range(3):
        report = await watchdog.run_once()
        assert report.status is WatchdogStatus.FAILED, "a dead socket is not healthy"
        assert "socket-not-connected" in report.reasons

    assert latch.latched, "dead socket with no symbols must still latch"
    assert latch.latched == [(PROTECTION_STREAM_SCOPE, "socket-not-connected")]
    assert len(latch.latched) == 1, "must latch exactly once, not every cycle"


@pytest.mark.asyncio
async def test_socket_without_state_is_treated_as_dead() -> None:
    """Fail closed when the socket cannot report its state at all.

    `getattr(socket, "state", None)` returns None for a socket type that does
    not expose state. Treating that as "alive" is the silent-pass failure this
    watchdog exists to catch, so absence must be treated as death.
    """

    class _StatelessSocket:
        def check_freshness(self, symbol: str) -> bool:
            return True  # would look perfectly healthy if state were trusted

    latch = _RecordingKillSwitch()
    watchdog = _build(_StatelessSocket(), latch, symbols=[])

    for _ in range(3):
        report = await watchdog.run_once()
        assert report.status is WatchdogStatus.FAILED
        assert "socket-not-connected" in report.reasons

    assert latch.latched == [(PROTECTION_STREAM_SCOPE, "socket-not-connected")]


@pytest.mark.asyncio
async def test_single_dead_cycle_does_not_latch() -> None:
    """Transient blips must not halt a live venue."""
    latch = _RecordingKillSwitch()
    await _build(_DeadSocket(), latch, threshold=3, symbols=[]).run_once()
    assert not latch.latched, "one failed cycle is tolerated"


@pytest.mark.asyncio
async def test_healthy_socket_does_not_latch() -> None:
    latch = _RecordingKillSwitch()
    for _ in range(5):
        report = await _build(_StaleSocket(fresh=True), latch).run_once()
        assert report.status is WatchdogStatus.HEALTHY
    assert not latch.latched


@pytest.mark.asyncio
async def test_healthy_cycle_resets_the_counter() -> None:
    """A recovered stream must clear the accumulated failure count."""
    latch = _RecordingKillSwitch()
    sequence = iter([True, True, True, True, False, False, False, False])

    class _FlappingSocket(_StaleSocket):
        def check_freshness(self, symbol: str | None = None) -> bool:
            return next(sequence)

    watchdog = _build(_FlappingSocket(), latch, threshold=3, symbols=[])
    for _ in range(4):
        await watchdog.run_once()
    for _ in range(3):
        await watchdog.run_once()
    assert not latch.latched, "counter must reset after a healthy cycle"


# --------------------------------------------------------------------------
# 3. Per-symbol quiet is never a halt
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_symbol_staleness_does_not_latch_on_its_own() -> None:
    """A connected-but-quiet symbol is not a dead stream.

    A subscribed symbol can legitimately go seconds without a tick. Halting the
    venue on that would latch on ordinary market quiet, and the only release
    path is manual SQL.
    """
    latch = _RecordingKillSwitch()
    for _ in range(20):
        report = await _build(_StaleSocket(fresh=False), latch, threshold=3).run_once()
        assert report.status is WatchdogStatus.STALE
        assert "protection-stream-stale" in report.reasons
    assert not latch.latched, "per-symbol quiet must not halt the venue"


# --------------------------------------------------------------------------
# 4. Dedicated scope
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_latch_never_uses_venue_scope() -> None:
    """A dead stream must not disarm bot-managed TP/SL.

    `bitget_monitor._run_fallback_monitor` returns early when the venue-wide
    `bitget` scope is latched. Symbols that reject native SL/TP rely on that
    monitor, so latching `bitget` on a dead WebSocket would switch off the
    stop-loss path for exactly the positions that need it.
    """
    latch = _RecordingKillSwitch()
    watchdog = _build(_DeadSocket(), latch, symbols=[])
    for _ in range(3):
        await watchdog.run_once()

    assert latch.latched, "must latch"
    scopes = {scope for scope, _ in latch.latched}
    assert scopes == {PROTECTION_STREAM_SCOPE}
    assert "bitget" not in scopes, "venue scope also gates fallback protection"


@pytest.mark.asyncio
async def test_dispatcher_rejects_entries_on_protection_stream_scope() -> None:
    """The entry block must be wired into the dispatch path.

    The end-to-end dispatcher behaviour is covered in test_bitget_dispatcher.py;
    this asserts only that the scope constant the latch writes is the same one
    the dispatcher reads, so the two cannot drift apart.
    """
    from fatty_trader.execution.bitget_dispatcher import PROTECTION_STREAM_SCOPE as imported

    assert imported is PROTECTION_STREAM_SCOPE
    assert PROTECTION_STREAM_SCOPE != "bitget", "must not collide with the venue scope"


# --------------------------------------------------------------------------
# 5. Latch failures
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_latch_db_failure_does_not_crash_and_keeps_counting() -> None:
    """A DB error at latch time must not kill the monitor.

    The counter is in-process. If the latch raised, the watchdog task died, the
    process restarted, and the counter reset to 0 - so a monitor restarting
    faster than the threshold could never latch at all.
    """
    latch = _ExplodingKillSwitch()
    watchdog = _build(_DeadSocket(), latch, symbols=[])
    for _ in range(6):
        report = await watchdog.run_once()  # must not raise
        assert report.status is WatchdogStatus.FAILED
    assert latch.attempts >= 3, "keeps retrying the latch every cycle"


@pytest.mark.asyncio
async def test_latch_failure_is_reported_loudly(capsys: Any) -> None:
    """Fail loud: a latch we could not record must never look like success."""
    watchdog = _build(_DeadSocket(), _ExplodingKillSwitch(), symbols=[])
    for _ in range(3):
        await watchdog.run_once()
    assert "latch-failed" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_does_not_rewrite_the_reason_once_latched() -> None:
    """Re-latching every cycle churns the DB and overwrites the root cause."""
    latch = _RecordingKillSwitch()
    watchdog = _build(_DeadSocket(), latch, symbols=[])
    for _ in range(10):
        await watchdog.run_once()
    assert len(latch.latched) == 1


def test_unknown_latch_api_is_not_called_blindly() -> None:
    """A duck-typed `latch` attribute may be an unrelated method."""

    class _WrongDuckType:
        def __init__(self) -> None:
            self.called = False

        def latch(self, *args: Any) -> None:
            self.called = True

    target = _WrongDuckType()
    asyncio.run(_build(_DeadSocket(), target, threshold=1, symbols=[]).run_once())
    assert not target.called, "unknown latch API must not be invoked blindly"


@pytest.mark.asyncio
async def test_latch_uses_real_repository_api_end_to_end() -> None:
    """Guard the production path: the real repo latches via latch_kill_switch.

    The dispatcher gate reads exactly this call, in a different process.
    """
    from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository

    repository = InMemoryReconciliationRepository()
    assert not hasattr(repository, "latch"), "precondition: real repo has no bare latch()"

    watchdog = _build(_DeadSocket(), repository, threshold=1, symbols=[])
    await watchdog.run_once()

    assert repository.is_active(PROTECTION_STREAM_SCOPE) is True
    assert not repository.is_active("bitget"), "must not trip the venue scope"
    assert "socket-not-connected" in repository.alerts


@pytest.mark.asyncio
async def test_latched_stream_does_not_auto_clear_on_recovery() -> None:
    """A later healthy cycle must not silently release the entry block.

    The only release path is `release_kill_switch`, which requires an explicit
    approval reference. Nothing in the stack may clear it automatically.
    """
    repository = InMemoryReconciliationRepository()
    watchdog = _build(_DeadSocket(), repository, threshold=1, symbols=[])
    await watchdog.run_once()
    assert repository.is_active(PROTECTION_STREAM_SCOPE) is True

    # Swap in a healthy socket on the same watchdog instance.
    watchdog._socket = _StaleSocket(fresh=True)
    for _ in range(5):
        report = await watchdog.run_once()
        assert report.status is WatchdogStatus.HEALTHY

    assert repository.is_active(PROTECTION_STREAM_SCOPE) is True, (
        "recovery must not auto-clear; release is a manual, approved action"
    )


# --------------------------------------------------------------------------
# 6. Service wiring
# --------------------------------------------------------------------------


def _builder_env(mode: str) -> dict[str, str]:
    return {"TRADER_MODE": "LIVE", "BITGET_MODE": mode}


def test_builder_passes_kill_switch_through_in_live() -> None:
    """The monitor must hand the watchdog a latch target, or the latch is inert."""
    from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository

    repository = InMemoryReconciliationRepository()

    class _Stream:
        def __init__(self) -> None:
            self.socket = _StaleSocket()
            self.repository = InMemoryProtectionCapabilityRepository()
            self.symbols = ("HBARUSDT",)

    class _Client:
        async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
            return []

    import fatty_trader.service as service_module

    original = service_module.build_bitget_protection_stream
    service_module.build_bitget_protection_stream = lambda *a, **k: _Stream()
    try:
        _stream, watchdog, _interval = service_module.build_bitget_monitor_protection(
            _builder_env("LIVE"), _Client(), kill_switch=repository
        )
    finally:
        service_module.build_bitget_protection_stream = original

    assert watchdog is not None
    assert watchdog._kill_switch is repository


def test_builder_does_not_latch_in_demo() -> None:
    """A DEMO latch would block paper execution via the shared test repository."""
    from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository

    repository = InMemoryReconciliationRepository()

    class _Stream:
        def __init__(self) -> None:
            self.socket = _StaleSocket()
            self.repository = InMemoryProtectionCapabilityRepository()
            self.symbols = ("HBARUSDT",)

    class _Client:
        async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
            return []

    import fatty_trader.service as service_module

    original = service_module.build_bitget_protection_stream
    service_module.build_bitget_protection_stream = lambda *a, **k: _Stream()
    try:
        _stream, watchdog, _interval = service_module.build_bitget_monitor_protection(
            _builder_env("DEMO"), _Client(), kill_switch=repository
        )
    finally:
        service_module.build_bitget_protection_stream = original

    assert watchdog is not None
    assert watchdog._kill_switch is None


def test_mixed_mode_config_does_not_latch() -> None:
    """TRADER_MODE=DEMO with BITGET_MODE=LIVE must not latch.

    The monitor's existing latches require both modes LIVE. If this watchdog
    latched on BITGET_MODE alone it would block paper execution through the
    shared kill-switch table - the same class of bug this change is fixing.
    """
    from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository

    repository = InMemoryReconciliationRepository()

    class _Stream:
        def __init__(self) -> None:
            self.socket = _StaleSocket()
            self.repository = InMemoryProtectionCapabilityRepository()
            self.symbols = ("HBARUSDT",)

    class _Client:
        async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
            return []

    import fatty_trader.service as service_module

    original = service_module.build_bitget_protection_stream
    service_module.build_bitget_protection_stream = lambda *a, **k: _Stream()
    try:
        _stream, watchdog, _interval = service_module.build_bitget_monitor_protection(
            {"TRADER_MODE": "DEMO", "BITGET_MODE": "LIVE"}, _Client(), kill_switch=repository
        )
    finally:
        service_module.build_bitget_protection_stream = original

    assert watchdog is not None
    assert watchdog._kill_switch is None


def test_stale_cycles_knob_is_honoured() -> None:
    """The env knob must actually change the threshold, not just the default."""
    import fatty_trader.service as service_module

    class _Stream:
        def __init__(self) -> None:
            self.socket = _StaleSocket()
            self.repository = InMemoryProtectionCapabilityRepository()
            self.symbols = ("HBARUSDT",)

    class _Client:
        async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
            return []

    original = service_module.build_bitget_protection_stream
    service_module.build_bitget_protection_stream = lambda *a, **k: _Stream()
    try:
        env = _builder_env("LIVE")
        env["BITGET_PROTECTION_STALE_CYCLES_BEFORE_LATCH"] = "7"
        _stream, watchdog, _interval = service_module.build_bitget_monitor_protection(
            env, _Client(), kill_switch=object()
        )
    finally:
        service_module.build_bitget_protection_stream = original

    assert watchdog is not None
    assert watchdog._stale_cycles_before_latch == 7


def test_compose_plumbs_the_stale_cycles_knob() -> None:
    """DEFECT 6: an operator tuning .env must actually reach the container."""
    from pathlib import Path

    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    assert "BITGET_PROTECTION_STALE_CYCLES_BEFORE_LATCH" in text, (
        "knob is read in code but never passed through Compose, so .env is inert"
    )


# --------------------------------------------------------------------------
# 7. Capability gate
# --------------------------------------------------------------------------


def test_capability_gate_blocks_symbol_without_fresh_protection() -> None:
    """With the gate on, a stale capability must deny entry admission."""
    repository = InMemoryProtectionCapabilityRepository()
    admit = build_bitget_protection_admission(
        {
            "BITGET_PROTECTION_CAPABILITY_GATE_ENABLED": "1",
            "BITGET_MODE": "LIVE",
            "BITGET_PROTECTION_STALE_SECONDS": "5",
        },
        repository=repository,
        now=lambda: _NOW,
    )
    assert admit is not None

    allowed, reason = admit("HBARUSDT")
    assert allowed is False
    assert reason == "protection-capability-unknown"
