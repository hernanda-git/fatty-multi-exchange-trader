import importlib

import httpx
import pytest

from fatty_trader.web.runtime_probe import create_runtime_health_reader
from fatty_trader.worker_health import WorkerHealth


class Database:
    def __init__(self):
        self.calls = []
        self.closed = 0
        self.failure = False

    def connect(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure:
            raise OSError("postgresql://secret:***@private/db")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed += 1

    def cursor(self):
        return Cursor()


class Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql):
        assert sql == "SELECT 1"

    def fetchone(self):
        return (1,)


async def test_actual_main_assembly_reads_live_progress_and_read_only_db(monkeypatch, tmp_path):
    import psycopg

    db = Database()
    monkeypatch.setattr(psycopg, "connect", db.connect)
    monkeypatch.setenv("SERVICE_COMPONENTS", "postgres=ready,migrate=ready")
    workers = []
    for component, name in [
        ("intake", "intake"),
        ("analyzer", "analyzer"),
        ("notification", "notification-sender"),
    ]:
        path = tmp_path / name
        monkeypatch.setenv(f"WEB_{component.upper()}_HEALTH_PATH", str(path))
        worker = WorkerHealth(name, {"WORKER_HEALTH_PATH": str(path)})
        worker.progress()
        workers.append(worker)
    from fatty_trader import main

    main = importlib.reload(main)
    assert not db.calls  # Import/startup must not connect or start workers.
    try:
        async with (
            main.app.router.lifespan_context(main.app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test"
            ) as client,
        ):
            response = await client.get("/health")
            assert response.json()["readiness"]["status"] == "ready"
            assert db.calls[-1]["connect_timeout"] == 2
            assert "default_transaction_read_only=on" in db.calls[-1]["options"]
            assert "statement_timeout=2000" in db.calls[-1]["options"]
            assert db.closed == 1
            workers[1].close()
            report = (await client.get("/health/telemetry")).json()
            assert report["liveness"]["status"] == "alive"
            assert report["readiness"]["components"]["analyzer"] != "ready"
            db.failure = True
            response = await client.get("/health")
            assert response.json()["readiness"]["components"]["postgres"] == "degraded"
            assert "secret" not in response.text
            assert "password" not in response.text
            assert "postgresql://" not in response.text
    finally:
        for worker in workers:
            worker.close()


@pytest.mark.parametrize("ticks", [(-1, -1), (10, 131)])
def test_invalid_clock_or_overdue_db_probe_cannot_be_ready(ticks):
    iterator = iter(ticks)
    read = create_runtime_health_reader(
        {}, connection_factory=Database().connect, clock=lambda: next(iterator)
    )
    assert read()["postgres"]["state"] != "ready"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "stale",
        "future",
        "nan",
        "inf",
        "failed",
        "disconnected",
        "pid",
        "identity",
        "wrong_service",
        "malformed",
    ],
)
def test_worker_evidence_fails_closed(tmp_path, mutation):
    import json

    path = tmp_path / "worker"
    worker = WorkerHealth("intake", {"WORKER_HEALTH_PATH": str(path)})
    worker.progress()
    data = json.loads(path.read_text())
    now = data["progress"] + 1
    if mutation == "missing":
        path.unlink()
    elif mutation == "malformed":
        path.write_text("[]")
    else:
        if mutation == "stale":
            data["progress"] = now - 121
        elif mutation == "future":
            data["progress"] = now + 1
        elif mutation in {"nan", "inf"}:
            data["progress"] = float(mutation)
        elif mutation in {"failed", "disconnected"}:
            data["state"] = mutation
        elif mutation == "pid":
            data["pid"] = 999999999
        elif mutation == "identity":
            data["identity"] = "other process"
        else:
            data["service"] = "analyzer"
        path.write_text(json.dumps(data))
    try:
        read = create_runtime_health_reader(
            {"WEB_INTAKE_HEALTH_PATH": str(path)},
            connection_factory=Database().connect,
            clock=lambda: now,
        )
        assert read()["intake"]["state"] != "ready"
    finally:
        worker.close()


@pytest.mark.parametrize("now", [float("nan"), float("inf"), -1, "bad"])
def test_nonfinite_clock_never_probes_database(now):
    db = Database()
    report = create_runtime_health_reader({}, connection_factory=db.connect, clock=lambda: now)()
    assert not db.calls
    assert all(item["state"] != "ready" for item in report.values())


@pytest.mark.parametrize("max_age", ["nan", "inf", "0", "-1", "bad"])
def test_invalid_max_age_never_probes_database(max_age):
    db = Database()
    report = create_runtime_health_reader(
        {"WORKER_HEALTH_MAX_AGE_SECONDS": max_age}, connection_factory=db.connect
    )()
    assert not db.calls
    assert all(item["state"] != "ready" for item in report.values())


@pytest.mark.parametrize("count", [0, 3])
async def test_actual_analyzer_idle_and_busy_cycles_produce_readiness(monkeypatch, tmp_path, count):
    import asyncio

    from fatty_trader import service

    path = tmp_path / "analyzer"
    env = {"WORKER_HEALTH_PATH": str(path), "ANALYZER_POLL_SECONDS": "100"}
    monkeypatch.setattr(service, "process_received_batch", lambda *args, **kwargs: count)
    read = create_runtime_health_reader(
        {"WEB_ANALYZER_HEALTH_PATH": str(path)}, connection_factory=Database().connect
    )
    assert read()["analyzer"]["state"] != "ready"
    task = asyncio.create_task(service.run_analyzer(env))
    try:
        await asyncio.sleep(0)
        assert read()["analyzer"]["state"] == "ready"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert read()["analyzer"]["state"] != "ready"


def test_real_worker_subprocess_stall_and_death_invalidate_web_probe(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    import time

    path = tmp_path / "child"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys,time; from fatty_trader.worker_health import WorkerHealth; "
            "h=WorkerHealth('analyzer', {'WORKER_HEALTH_PATH':sys.argv[1]}); "
            "h.progress(); print('ready',flush=True); "
            "exec('while True:\\n h.progress(); time.sleep(0.02)')",
            str(path),
        ],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    read = create_runtime_health_reader(
        {"WEB_ANALYZER_HEALTH_PATH": str(path), "WORKER_HEALTH_MAX_AGE_SECONDS": "0.2"},
        connection_factory=Database().connect,
    )
    assert child.stdout is not None
    try:
        assert child.stdout.readline().strip() == "ready"
        assert read()["analyzer"]["state"] == "ready"
        os.kill(child.pid, signal.SIGSTOP)
        time.sleep(0.3)
        assert read()["analyzer"]["state"] != "ready"
        child.kill()
        child.wait(timeout=5)
        # Death invalidates even fresh evidence, not merely by age.
        assert (
            create_runtime_health_reader(
                {"WEB_ANALYZER_HEALTH_PATH": str(path)}, connection_factory=Database().connect
            )()["analyzer"]["state"]
            != "ready"
        )
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stdout.close()


@pytest.mark.parametrize("state", ["idle", "sent", "retry", "failed"])
async def test_actual_notification_cycle_success_controls_web_evidence(
    monkeypatch, tmp_path, state
):
    import asyncio

    from fatty_trader import notification_service

    path = tmp_path / "notification"
    env = {"WORKER_HEALTH_PATH": str(path), "NOTIFICATION_POLL_SECONDS": "100"}
    monkeypatch.setattr(notification_service, "notification_settings", lambda env: object())
    monkeypatch.setattr(notification_service, "TelegramBotSender", lambda settings: object())

    class Worker:
        def __init__(self, *args, **kwargs):
            pass

        async def run_once(self, *args):
            return state

    monkeypatch.setattr(notification_service, "NotificationWorker", Worker)
    read = create_runtime_health_reader(
        {"WEB_NOTIFICATION_HEALTH_PATH": str(path)}, connection_factory=Database().connect
    )
    task = asyncio.create_task(notification_service.run(env))
    try:
        await asyncio.sleep(0)
        assert (read()["notification"]["state"] == "ready") == (state in {"idle", "sent"})
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert read()["notification"]["state"] != "ready"
