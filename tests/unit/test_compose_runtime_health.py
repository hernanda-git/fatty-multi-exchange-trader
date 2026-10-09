"""Offline proof of worker-local identity attestation over isolated shared sockets."""

import json
import os
import signal
import subprocess
import sys
import time

import pytest

from fatty_trader.web.runtime_probe import create_runtime_health_reader


def no_database(**kwargs):
    raise OSError("offline")


def test_socket_proves_owner_without_web_pid_namespace(monkeypatch, tmp_path):
    path, endpoint = tmp_path / "health.json", tmp_path / "owner.sock"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys,time; from fatty_trader.worker_health import WorkerHealth; "
            "h=WorkerHealth('analyzer', {'WORKER_HEALTH_PATH':sys.argv[1], "
            "'WORKER_HEALTH_SOCKET_PATH':sys.argv[2]}); h.progress(); print('ready',flush=True); "
            "exec('while True:\\n h.progress(); time.sleep(0.02)')",
            str(path),
            str(endpoint),
        ],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert child.stdout is not None
    env = {
        "WEB_ANALYZER_HEALTH_PATH": str(path),
        "WEB_ANALYZER_HEALTH_SOCKET_PATH": str(endpoint),
        "WORKER_HEALTH_MAX_AGE_SECONDS": "0.2",
    }
    read = create_runtime_health_reader(env, connection_factory=no_database)
    # The web container cannot see the sibling PID at all.
    monkeypatch.setattr(
        "fatty_trader.web.runtime_probe._process_identity",
        lambda pid: (_ for _ in ()).throw(FileNotFoundError()),
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        assert read()["analyzer"]["state"] == "ready"
        os.kill(child.pid, signal.SIGSTOP)
        time.sleep(0.3)
        assert read()["analyzer"]["state"] == "failed"
        os.kill(child.pid, signal.SIGCONT)
        deadline = time.monotonic() + 3
        while read()["analyzer"]["state"] != "ready" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert read()["analyzer"]["state"] == "ready"
        child.kill()
        child.wait(timeout=5)
        env["WORKER_HEALTH_MAX_AGE_SECONDS"] = "1000"
        assert read()["analyzer"]["state"] == "failed"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stdout.close()


def test_compose_isolates_each_owner_socket_and_web_has_read_only_mounts(monkeypatch):
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
    services = compose["services"]
    web = services["web"]
    assert "pid" not in web
    for component, service in [
        ("intake", "intake"),
        ("analyzer", "analyzer"),
        ("notification", "notification-sender"),
    ]:
        worker = services[service]
        mount = f"/run/fatty-health/{component}"
        volume = f"health-{component}"
        assert worker["environment"]["WORKER_HEALTH_PATH"] == f"{mount}/progress.json"
        assert worker["environment"]["WORKER_HEALTH_SOCKET_PATH"] == f"{mount}/owner.sock"
        assert (
            web["environment"][f"WEB_{component.upper()}_HEALTH_PATH"] == f"{mount}/progress.json"
        )
        assert (
            web["environment"][f"WEB_{component.upper()}_HEALTH_SOCKET_PATH"]
            == f"{mount}/owner.sock"
        )
        worker_mount = next(v for v in worker["volumes"] if v["source"] == volume)
        web_mount = next(v for v in web["volumes"] if v["source"] == volume)
        assert worker_mount["target"] == mount and not worker_mount.get("read_only", False)
        assert web_mount["target"] == mount and web_mount["read_only"] is True
        assert compose["volumes"][volume]["driver_opts"]["type"] == "tmpfs"
        for other_name, other in services.items():
            if other_name not in {"web", service}:
                assert not any(v["source"] == volume for v in other.get("volumes", []))
        assert "pid" not in worker
    assert "SERVICE_COMPONENTS" not in web["environment"]
    assert services["source-management"]["healthcheck"]["test"][-3:] == [
        "--service",
        "source-management",
        "--check",
    ]
    import io
    import urllib.request

    probe = web["healthcheck"]["test"][-1]
    for status, expected_exit in (("ok", 0), ("degraded", 1)):
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            (lambda s=status: lambda *args, **kwargs: io.StringIO(json.dumps({"status": s})))(),
        )
        with pytest.raises(SystemExit) as raised:
            exec(probe, {})
        assert raised.value.code == expected_exit


@pytest.mark.parametrize(
    "mutation", ["pid", "identity", "owner_token", "service", "missing_socket"]
)
def test_socket_rejects_wrong_owner_and_never_falls_back_to_local_pid(tmp_path, mutation):
    from fatty_trader.worker_health import WorkerHealth

    path, endpoint = tmp_path / "progress.json", tmp_path / "owner.sock"
    worker = WorkerHealth(
        "analyzer", {"WORKER_HEALTH_PATH": str(path), "WORKER_HEALTH_SOCKET_PATH": str(endpoint)}
    )
    worker.progress()
    env = {"WEB_ANALYZER_HEALTH_PATH": str(path), "WEB_ANALYZER_HEALTH_SOCKET_PATH": str(endpoint)}
    try:
        data = json.loads(path.read_text())
        if mutation == "missing_socket":
            env["WEB_ANALYZER_HEALTH_SOCKET_PATH"] += ".missing"
        else:
            data[mutation] = "wrong" if mutation != "pid" else 99999999
            path.write_text(json.dumps(data))
        assert (
            create_runtime_health_reader(env, connection_factory=no_database)()["analyzer"]["state"]
            == "failed"
        )
    finally:
        worker.close()
    assert not endpoint.exists()
    worker.progress()
    assert not path.exists()


def test_attestation_thread_cannot_refresh_progress_and_old_token_cannot_validate_restart(tmp_path):
    from fatty_trader.worker_health import WorkerHealth

    path, endpoint = tmp_path / "progress.json", tmp_path / "owner.sock"
    owner_env = {"WORKER_HEALTH_PATH": str(path), "WORKER_HEALTH_SOCKET_PATH": str(endpoint)}
    env = {
        "WEB_ANALYZER_HEALTH_PATH": str(path),
        "WEB_ANALYZER_HEALTH_SOCKET_PATH": str(endpoint),
        "WORKER_HEALTH_MAX_AGE_SECONDS": "0.05",
    }
    read = create_runtime_health_reader(env, connection_factory=no_database)
    worker = WorkerHealth("analyzer", owner_env)
    try:
        worker.progress()
        previous = path.read_text()
        assert read()["analyzer"]["state"] == "ready"
        time.sleep(0.08)  # Attestation thread remains responsive, worker did no work.
        assert read()["analyzer"]["state"] == "failed"
        assert path.read_text() == previous
    finally:
        worker.close()
    replacement = WorkerHealth("analyzer", owner_env)
    try:
        replacement.progress()
        env["WORKER_HEALTH_MAX_AGE_SECONDS"] = "1000"
        path.write_text(previous)  # Same process PID/start ticks, old worker generation.
        assert read()["analyzer"]["state"] == "failed"
    finally:
        replacement.close()
