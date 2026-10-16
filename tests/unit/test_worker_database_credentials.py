from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load_script(name, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_worker_secret_is_private_separate_and_replay_safe(tmp_path, monkeypatch):
    provision = load_script("provision_worker_database", monkeypatch)
    path = tmp_path / ".env"
    original = "POSTGRES_PASSWORD='admin-only'\nCONSOLE_DB_PASSWORD='web-only'\nBITGET_MODE=LIVE\n"
    path.write_text(original)
    password = provision.prepare_worker_secret(path)
    assert len(password) >= 32
    assert password not in {"admin-only", "web-only"}
    assert provision.prepare_worker_secret(path) == password
    assert path.read_text().startswith(original)
    assert path.read_text().count("WORKER_DB_PASSWORD=") == 1
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("kind", ["duplicate", "shared", "short"])
def test_worker_secret_refuses_ambiguous_or_shared_credentials(tmp_path, monkeypatch, kind):
    provision = load_script("provision_worker_database", monkeypatch)
    password = "strong-but-shared-private-password" * 2
    content = "POSTGRES_PASSWORD='" + password + "'\n"
    content += "WORKER_DB_PASSWORD='" + ("short" if kind == "short" else password) + "'\n"
    if kind == "duplicate":
        content += "WORKER_DB_PASSWORD='another'\n"
    path = tmp_path / ".env"
    path.write_text(content)
    with pytest.raises(ValueError):
        provision.prepare_worker_secret(path)
    assert path.read_text() == content


def test_provisioner_uses_admin_only_and_never_password_arguments(tmp_path, monkeypatch):
    provision = load_script("provision_worker_database", monkeypatch)
    calls = []
    monkeypatch.setattr(
        provision.subprocess,
        "run",
        lambda args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=0),
    )
    password = "private-worker-password-that-must-not-be-an-argument"
    provision.provision_worker_role(tmp_path, password)
    args, kwargs = calls[0]
    assert args[args.index("-U") + 1] == "fatty_app"
    assert password not in " ".join(args)
    assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["input"].startswith(b"BEGIN;")
    assert kwargs["input"].endswith(b"COMMIT;\n")
    assert kwargs["env"]["WORKER_DB_PASSWORD"] == password


def test_host_launcher_never_uses_bootstrap_credential(tmp_path, monkeypatch):
    launcher = load_script("console_ops_worker", monkeypatch)
    state = tmp_path / "state"
    state.mkdir()
    env = {
        "CONSOLE_OPS_WORKER": "1",
        "CONSOLE_REPOSITORY_ROOT": str(tmp_path),
        "CONSOLE_OPS_STATE_DIR": str(state),
        "CONSOLE_COMPOSE_PROJECT": "approved-project",
        "POSTGRES_PASSWORD": "admin-private",
        "WORKER_DB_PASSWORD": "runtime-private",
        "DATABASE_URL": "must-not-select-the-admin",
    }
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        result = (
            "a" * 64
            if "inspect" not in args
            else json.dumps({"approved-project_default": {"IPAddress": "172.18.0.2"}})
        )
        return SimpleNamespace(returncode=0, stdout=result.encode())

    monkeypatch.setattr(launcher.subprocess, "run", run)
    prepared = launcher.prepare_host_environment(env)
    assert prepared["PGUSER"] == "fatty_worker"
    assert prepared["PGPASSWORD"] == "runtime-private"
    assert "DATABASE_URL" not in prepared
    assert "POSTGRES_PASSWORD" not in prepared
    assert "CONSOLE_DB_PASSWORD" not in prepared
    for password in ("", "admin-private"):
        calls.clear()
        with pytest.raises(ValueError, match="separate runtime"):
            launcher.prepare_host_environment({**env, "WORKER_DB_PASSWORD": password})
        assert not calls


def test_runtime_database_helpers_never_fall_back_to_superuser(monkeypatch):
    import psycopg

    from fatty_trader.execution import bitget_fallback_protection as fallback

    recovery = load_script("auto_recover_bitget", monkeypatch)
    monkeypatch.delenv("PGUSER", raising=False)
    monkeypatch.setattr(
        psycopg, "connect", lambda **kwargs: pytest.fail("unexpected implicit database login")
    )
    for connect in (fallback._psycopg_connect, recovery._db):
        with pytest.raises(KeyError, match="PGUSER"):
            connect()
