import asyncio
import json

import pytest

from fatty_trader import notification_service, service


def test_config_check_cannot_claim_worker_health(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKER_HEALTH_PATH", str(tmp_path / "missing"))
    for name in ("intake", "analyzer", "operator-bot"):
        monkeypatch.setattr(
            service,
            "parse_args",
            lambda name=name: type(
                "Args",
                (),
                {
                    "service": name,
                    "check": True,
                    "heartbeat_path": None,
                    "heartbeat_max_age": None,
                },
            )(),
        )
        assert service.main() != 0


def test_notification_check_requires_live_progress(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKER_HEALTH_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr("sys.argv", ["notification-sender", "--check"])
    assert notification_service.main() != 0


def test_owned_health_requires_progress_and_invalidates_on_exit(tmp_path):
    from fatty_trader.worker_health import WorkerHealth, check_worker_health

    env = {"WORKER_HEALTH_PATH": str(tmp_path / "health")}
    health = WorkerHealth("analyzer", env)
    assert check_worker_health("analyzer", env) == 1
    health.progress()
    assert check_worker_health("analyzer", env) == 0
    assert check_worker_health("intake", env) == 1
    health.close()
    assert check_worker_health("analyzer", env) == 1
    # A thread finishing after its asyncio owner is cancelled cannot revive it.
    health.progress()
    assert check_worker_health("analyzer", env) == 1


@pytest.mark.parametrize("mutation", ["stale", "future", "nan", "pid", "identity", "disabled"])
def test_health_rejects_invalid_evidence(tmp_path, mutation):
    from fatty_trader.worker_health import WorkerHealth, check_worker_health

    env = {"WORKER_HEALTH_PATH": str(tmp_path / "health")}
    health = WorkerHealth("analyzer", env)
    health.progress()
    data = json.loads(health.path.read_text())
    if mutation == "stale":
        data["progress"] -= 1000
    elif mutation == "future":
        data["progress"] += 1000
    elif mutation == "nan":
        data["progress"] = float("nan")
    elif mutation == "pid":
        data["pid"] = 999999999
    elif mutation == "identity":
        data["identity"] = "wrong"
    else:
        data["state"] = "disabled"
    health.path.write_text(json.dumps(data))
    assert check_worker_health("analyzer", env) == 1


def test_probe_rejects_stopped_and_dead_worker_process(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    import time

    from fatty_trader.worker_health import check_worker_health

    path = tmp_path / "child-health"
    env = {"WORKER_HEALTH_PATH": str(path), "WORKER_HEALTH_MAX_AGE_SECONDS": "0.2"}
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys,time; from fatty_trader.worker_health import WorkerHealth; "
                "h=WorkerHealth('analyzer', {'WORKER_HEALTH_PATH':sys.argv[1]}); "
                "h.progress(); print('ready',flush=True); "
                "exec('while True:\\n h.progress(); time.sleep(0.02)')"
            ),
            str(path),
        ],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert child.stdout is not None
    try:
        assert child.stdout.readline().strip() == "ready"
        assert check_worker_health("analyzer", env) == 0
        os.kill(child.pid, signal.SIGSTOP)
        time.sleep(0.3)
        assert check_worker_health("analyzer", env) == 1
        child.kill()
        child.wait(timeout=5)
        env["WORKER_HEALTH_MAX_AGE_SECONDS"] = "1000"
        assert check_worker_health("analyzer", env) == 1
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stdout.close()


@pytest.mark.asyncio
async def test_owned_worker_decorator_clears_restart_and_exit(tmp_path):
    from fatty_trader.worker_health import (
        check_worker_health,
        owned_worker_health,
        worker_progress,
    )

    env = {"WORKER_HEALTH_PATH": str(tmp_path / "health")}

    @owned_worker_health("analyzer")
    async def worker(environ):
        assert check_worker_health("analyzer", environ) == 1
        worker_progress()
        assert check_worker_health("analyzer", environ) == 0
        raise RuntimeError("worker died")

    with pytest.raises(RuntimeError, match="worker died"):
        await worker(env)
    assert check_worker_health("analyzer", env) == 1


@pytest.mark.asyncio
async def test_analyzer_idle_cycle_records_progress_then_cancellation_invalidates(
    monkeypatch, tmp_path
):
    import asyncio

    from fatty_trader.worker_health import check_worker_health

    env = {"WORKER_HEALTH_PATH": str(tmp_path / "health"), "ANALYZER_POLL_SECONDS": "100"}
    monkeypatch.setattr(service, "process_received_batch", lambda *args, **kwargs: 0)
    task = asyncio.create_task(service.run_analyzer(env))
    try:
        await asyncio.sleep(0)
        assert check_worker_health("analyzer", env) == 0
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert check_worker_health("analyzer", env) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state, expected", [("idle", 0), ("sent", 0), ("retry", 1), ("failed", 1)])
async def test_notification_progress_requires_successful_cycle(
    monkeypatch, tmp_path, state, expected
):
    import asyncio

    from fatty_trader.worker_health import check_worker_health

    env = {"WORKER_HEALTH_PATH": str(tmp_path / "health"), "NOTIFICATION_POLL_SECONDS": "100"}
    monkeypatch.setattr(notification_service, "notification_settings", lambda env: object())
    monkeypatch.setattr(notification_service, "TelegramBotSender", lambda settings: object())

    class Worker:
        def __init__(self, *args, **kwargs):
            pass

        async def run_once(self, *args):
            return state

    monkeypatch.setattr(notification_service, "NotificationWorker", Worker)
    task = asyncio.create_task(notification_service.run(env))
    try:
        await asyncio.sleep(0)
        assert check_worker_health("notification-sender", env) == expected
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert check_worker_health("notification-sender", env) == 1


@pytest.mark.asyncio
async def test_operator_health_is_written_by_polling_thread_only(monkeypatch, tmp_path):
    from fatty_trader.operator import telegram_polling
    from fatty_trader.worker_health import check_worker_health

    env = {
        "WORKER_HEALTH_PATH": str(tmp_path / "health"),
        "TG_BOT_TOKEN": "test",
        "TG_OPERATOR_ID": "123",
        "BITGET_API_KEY": "test",
        "BITGET_API_SECRET": "test",
        "BITGET_API_PASSPHRASE": "test",
    }
    monkeypatch.setattr(telegram_polling.TelegramBotApi, "set_my_commands", lambda self: None)

    class Poller:
        def __init__(self, **kwargs):
            self.calls = 0

        def run_once(self):
            self.calls += 1
            if self.calls == 1:
                assert check_worker_health("operator-bot", env) == 1
                return
            assert check_worker_health("operator-bot", env) == 0
            raise ValueError("stop polling")

    monkeypatch.setattr(telegram_polling, "TelegramCommandPoller", Poller)
    with pytest.raises(ValueError, match="stop polling"):
        await service.run_operator_bot(env)
    assert check_worker_health("operator-bot", env) == 1


@pytest.mark.asyncio
async def test_intake_connected_event_loop_progress_and_disconnect_cleanup(monkeypatch, tmp_path):
    from fatty_trader.worker_health import check_worker_health

    env = {
        "WORKER_HEALTH_PATH": str(tmp_path / "health"),
        "TG_API_ID": "123",
        "TG_API_HASH": "test",
        "TELEGRAM_SESSION": "test",
        "TELEGRAM_SOURCE_CHANNELS": "@test",
        "TELEGRAM_TARGET_CHAT_ID": "123",
        "TELEGRAM_CATCHUP_SECONDS": "0",
    }

    class Client:
        async def start(self):
            assert check_worker_health("intake", env) == 1

        def is_connected(self):
            return True

        async def run_until_disconnected(self):
            await asyncio.sleep(0)
            assert check_worker_health("intake", env) == 0

    class Forwarder:
        def __init__(self, *args):
            pass

        async def attach(self):
            pass

    monkeypatch.setattr(service, "TelegramForwarder", Forwarder)
    await service.run_intake(env, client_factory=lambda settings: Client(), repository=object())
    assert check_worker_health("intake", env) == 1
