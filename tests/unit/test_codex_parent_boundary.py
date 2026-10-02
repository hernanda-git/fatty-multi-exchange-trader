"""Parent credential boundary must be kernel-enforced, not env filtering alone."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_codex_runner import FakeProcess

from fatty_trader.analyzer import codex_runner


def test_same_uid_child_cannot_read_parent_credentials_or_memory():
    root = Path(__file__).resolve().parents[2]
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(root / "src"),
        "SYNTHETIC_DB_SECRET": "kernel-proof-only",
    }
    proof = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/codex_parent_kernel_probe.py"),
            "--allow-host-hidepid",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proof.returncode == 0, proof.stdout + proof.stderr
    assert '"dumpable": 0' in proof.stdout
    assert '"no_new_privs": 1' in proof.stdout


def assert_no_spawn():
    def spawn(*args, **kwargs):
        pytest.fail("unhardened child started")

    result = codex_runner.CodexRunner(popen_factory=spawn).run("test")
    assert result.terminal_failure
    assert not result.succeeded
    assert result.exit_code is None
    assert result.stdout == result.stderr == ""
    assert result.failure_reason == "codex parent credential boundary unavailable"


def test_missing_kernel_capability_evidence_does_not_spawn(monkeypatch):
    monkeypatch.setattr(codex_runner.Path, "read_text", lambda self: "Name: synthetic\n")
    assert_no_spawn()


@pytest.mark.parametrize("platform", ["darwin", "win32", "unknown"])
def test_unsupported_kernel_does_not_spawn(monkeypatch, platform):
    monkeypatch.setattr(codex_runner.sys, "platform", platform)
    assert_no_spawn()


@pytest.mark.parametrize("field", ["CapPrm", "CapEff", "CapInh", "CapAmb"])
def test_ptrace_capability_does_not_spawn(monkeypatch, field):
    status = {key: "0" for key in ("CapPrm", "CapEff", "CapInh", "CapAmb")}
    status[field] = "80000"
    monkeypatch.setattr(
        codex_runner.Path,
        "read_text",
        lambda self: "\n".join(f"{k}: {v}" for k, v in status.items()),
    )
    assert_no_spawn()


@pytest.mark.parametrize("option,value", [(38, -1), (4, -1), (39, 0), (3, 1), (3, -1)])
def test_failed_or_unverified_hardening_does_not_spawn(monkeypatch, option, value):
    def prctl(command, *args):
        return value if command == option else {38: 0, 4: 0, 39: 1, 3: 0}[command]

    monkeypatch.setattr(codex_runner.ctypes, "CDLL", lambda *a, **k: SimpleNamespace(prctl=prctl))
    assert_no_spawn()


@pytest.mark.parametrize("failure", [OSError("denied"), AttributeError("no prctl")])
def test_unavailable_hardening_does_not_spawn(monkeypatch, failure):
    def load(*args, **kwargs):
        raise failure

    monkeypatch.setattr(codex_runner.ctypes, "CDLL", load)
    assert_no_spawn()


def test_every_invocation_verifies_parent_before_spawn(monkeypatch):
    events = []

    def prctl(command, value, *args):
        events.append((command, value))
        return {38: 0, 4: 0, 39: 1, 3: 0}[command]

    def spawn(*args, **kwargs):
        events.append("spawn")
        return FakeProcess()

    monkeypatch.setattr(codex_runner.ctypes, "CDLL", lambda *a, **k: SimpleNamespace(prctl=prctl))
    runner = codex_runner.CodexRunner(popen_factory=spawn)
    assert runner.run("first").succeeded
    assert runner.run("second").succeeded
    assert events == [(38, 1), (4, 0), (39, 0), (3, 0), "spawn"] * 2
