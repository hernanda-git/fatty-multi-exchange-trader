"""Offline worker health proofs; no provider or database connections are allowed."""

import argparse

import pytest

from fatty_trader import service


@pytest.mark.parametrize("name", ["dispatcher-bitget", "dispatcher-binance", "source-management"])
def test_checks_require_runtime_evidence_not_configuration(monkeypatch, tmp_path, name):
    monkeypatch.setenv("WORKER_HEALTH_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(
        service,
        "parse_args",
        lambda: argparse.Namespace(
            service=name, check=True, heartbeat_path=None, heartbeat_max_age=None
        ),
    )
    monkeypatch.setattr(service, "service_config", lambda *args: object())
    assert service.main() == 1


@pytest.mark.parametrize("name", ["dispatcher-bitget", "source-management"])
def test_check_consumes_live_evidence_and_rejects_stale_identity(monkeypatch, tmp_path, name):
    import json

    from fatty_trader.worker_health import WorkerHealth

    env = {"WORKER_HEALTH_PATH": str(tmp_path / "health")}
    monkeypatch.setenv("WORKER_HEALTH_PATH", env["WORKER_HEALTH_PATH"])
    monkeypatch.setattr("sys.argv", ["service", "--service", name, "--check"])
    health = WorkerHealth(name, env)
    try:
        assert service.main() == 1
        health.progress()
        assert service.main() == 0
        data = json.loads(health.path.read_text())
        data["progress"] -= 1000
        health.path.write_text(json.dumps(data))
        assert service.main() == 1
        health.progress()
        data = json.loads(health.path.read_text())
        data["identity"] = "recycled-process"
        health.path.write_text(json.dumps(data))
        assert service.main() == 1
    finally:
        health.close()
    assert service.main() == 1


def wire_offline_worker(monkeypatch, name, run_once):
    """Replace all provider/database constructors at the service boundary."""
    from fatty_trader.exchanges.bitget import client
    from fatty_trader.execution import bitget_dispatcher, source_management
    from fatty_trader.operator import bitget_gateway
    from fatty_trader.storage import source_management as source_store

    monkeypatch.setattr(client, "BitgetRestClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(bitget_gateway, "BitgetOperatorGateway", lambda *args: object())
    monkeypatch.setattr(source_store, "PostgresSourceManagementStore", lambda *args: object())
    monkeypatch.setattr(service, "build_bitget_execution_runtime", lambda env: None)
    monkeypatch.setattr(service, "build_bitget_protection_admission", lambda env: None)

    class Worker:
        def __init__(self, *args, **kwargs):
            pass

    Worker.run_once = staticmethod(run_once)
    if name == "dispatcher-bitget":
        monkeypatch.setattr(bitget_dispatcher, "BitgetDispatcher", Worker)
        return service.run_bitget_dispatcher
    monkeypatch.setattr(source_management, "SourceManagementExecutor", Worker)
    return service.run_source_management


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, state",
    [
        ("dispatcher-bitget", "idle"),
        ("dispatcher-bitget", "filled"),
        ("dispatcher-bitget", "rejected"),
        ("dispatcher-bitget", "cutover-gated"),
        ("dispatcher-bitget", "kill-switch-latched"),
        ("dispatcher-bitget", "protection-stream-latched"),
        ("source-management", "idle"),
        ("source-management", "reconciled"),
    ],
)
async def test_completed_cycle_publishes_only_after_startup_then_expires(
    monkeypatch, tmp_path, name, state
):
    import asyncio
    import json

    from fatty_trader.worker_health import WorkerHealth, check_worker_health

    env = {
        "WORKER_HEALTH_PATH": str(tmp_path / "health"),
        "BITGET_DISPATCH_POLL_SECONDS": "100",
        "SOURCE_MANAGEMENT_POLL_SECONDS": "100",
        "BITGET_API_KEY": "offline",
        "BITGET_API_SECRET": "offline",
        "BITGET_API_PASSPHRASE": "offline",
    }
    # A previous generation's ready evidence must be cleared at startup.
    old = WorkerHealth(name, env)
    old.progress()
    completed = asyncio.Event()

    def cycle(*args):
        assert check_worker_health(name, env) == 1
        return state

    async def async_cycle(*args):
        return cycle(*args)

    run = wire_offline_worker(
        monkeypatch, name, async_cycle if name == "dispatcher-bitget" else cycle
    )

    async def sleep(interval):
        completed.set()
        await asyncio.Future()

    monkeypatch.setattr(service.asyncio, "sleep", sleep)
    task = asyncio.create_task(run(env))
    try:
        await asyncio.wait_for(completed.wait(), timeout=3)
        assert check_worker_health(name, env) == 0
        path = tmp_path / "health"
        data = json.loads(path.read_text())
        data["progress"] -= 1000
        path.write_text(json.dumps(data))
        assert check_worker_health(name, env) == 1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        old.close()
    assert check_worker_health(name, env) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, state",
    [
        ("dispatcher-bitget", "unknown"),
        ("dispatcher-bitget", "retry"),
        ("source-management", "failed"),
        ("source-management", "mutations-disabled"),
        ("source-management", "reconciliation-pending"),
    ],
)
async def test_unsuccessful_cycles_do_not_refresh_progress(monkeypatch, tmp_path, name, state):
    import asyncio
    import json

    from fatty_trader.worker_health import check_worker_health

    env = {
        "WORKER_HEALTH_PATH": str(tmp_path / "health"),
        "BITGET_API_KEY": "offline",
        "BITGET_API_SECRET": "offline",
        "BITGET_API_PASSPHRASE": "offline",
    }
    completed = asyncio.Event()
    calls = 0
    path = tmp_path / "health"

    def cycle(*args):
        nonlocal calls
        calls += 1
        return "idle" if calls == 1 else state

    async def async_cycle(*args):
        return cycle(*args)

    run = wire_offline_worker(
        monkeypatch, name, async_cycle if name == "dispatcher-bitget" else cycle
    )

    async def sleep(interval):
        if calls == 1:
            assert check_worker_health(name, env) == 0
            data = json.loads(path.read_text())
            data["progress"] -= 1000
            path.write_text(json.dumps(data))
            return
        completed.set()
        await asyncio.Future()

    monkeypatch.setattr(service.asyncio, "sleep", sleep)
    task = asyncio.create_task(run(env))
    try:
        await asyncio.wait_for(completed.wait(), timeout=3)
        assert calls == 2
        assert check_worker_health(name, env) == 1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not path.exists()


def test_source_management_compose_probes_owned_progress():
    import json
    import subprocess

    compose = json.loads(
        subprocess.check_output(
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "config",
                "--no-interpolate",
                "--no-env-resolution",
                "--format",
                "json",
            ],
            text=True,
        )
    )
    worker = compose["services"]["source-management"]
    assert worker["healthcheck"]["test"] == [
        "CMD",
        "/app/.venv/bin/python",
        "-m",
        "fatty_trader.service",
        "--service",
        "source-management",
        "--check",
    ]
    assert worker["healthcheck"]["interval"] == "30s"
    assert worker["healthcheck"]["timeout"] == "10s"
    assert worker["healthcheck"]["retries"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["dispatcher-bitget", "source-management"])
async def test_cycle_exception_invalidates_worker(monkeypatch, tmp_path, name):
    from fatty_trader.worker_health import check_worker_health

    env = {
        "WORKER_HEALTH_PATH": str(tmp_path / "health"),
        "BITGET_API_KEY": "offline",
        "BITGET_API_SECRET": "offline",
        "BITGET_API_PASSPHRASE": "offline",
    }

    def cycle(*args):
        assert check_worker_health(name, env) == 1
        raise OSError("offline dependency failed")

    async def async_cycle(*args):
        return cycle(*args)

    run = wire_offline_worker(
        monkeypatch, name, async_cycle if name == "dispatcher-bitget" else cycle
    )
    with pytest.raises(OSError, match="offline dependency failed"):
        await run(env)
    assert check_worker_health(name, env) == 1
    assert not (tmp_path / "health").exists()


@pytest.mark.asyncio
async def test_dispatcher_recovery_failure_never_publishes(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from fatty_trader.worker_health import check_worker_health

    env = {"WORKER_HEALTH_PATH": str(tmp_path / "health")}

    async def cycle(*args):
        pytest.fail("dispatch cycle must not run before recovery is ready")

    run = wire_offline_worker(monkeypatch, "dispatcher-bitget", cycle)

    class Execution:
        recovery_ready = False

        async def recover_entry_lifecycles(self):
            assert check_worker_health("dispatcher-bitget", env) == 1

    class Client:
        closed = False

        async def aclose(self):
            self.closed = True

    client = Client()
    runtime = SimpleNamespace(execution=Execution(), preflight=None, client=client)
    monkeypatch.setattr(service, "build_bitget_execution_runtime", lambda env: runtime)
    with pytest.raises(RuntimeError, match="recovery is not ready"):
        await run(env)
    assert client.closed
    assert check_worker_health("dispatcher-bitget", env) == 1


@pytest.mark.asyncio
async def test_cancelled_source_thread_cannot_publish_late_progress(monkeypatch, tmp_path):
    import asyncio
    from threading import Event

    from fatty_trader.worker_health import check_worker_health, worker_progress

    env = {
        "WORKER_HEALTH_PATH": str(tmp_path / "health"),
        "BITGET_API_KEY": "offline",
        "BITGET_API_SECRET": "offline",
        "BITGET_API_PASSPHRASE": "offline",
    }
    started, release, finished = Event(), Event(), Event()

    def cycle(*args):
        started.set()
        assert release.wait(timeout=5)
        # Delayed work cannot resurrect the cancelled owner, even if it tries.
        worker_progress()
        finished.set()
        return "idle"

    run = wire_offline_worker(monkeypatch, "source-management", cycle)
    task = asyncio.create_task(run(env))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        assert check_worker_health("source-management", env) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert await asyncio.to_thread(finished.wait, 3)
    assert check_worker_health("source-management", env) == 1
    assert not (tmp_path / "health").exists()
