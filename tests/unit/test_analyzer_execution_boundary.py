import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest

from fatty_trader.analyzer.codex_runner import CodexRunner, CodexRunnerConfig


def test_subprocess_has_isolated_environment_cwd_and_readonly_policy(tmp_path):
    seen = {}

    def launch(argv, **kwargs):
        seen.update(kwargs)
        seen["argv"] = argv
        return subprocess.Popen([sys.executable, "-c", 'print("ok")'], **kwargs)

    result = CodexRunner(popen_factory=launch).run("untrusted")
    assert result.succeeded
    assert "env" in seen
    assert set(seen["env"]) <= {"PATH", "HOME", "TMPDIR", "LANG", "CODEX_HOME", "OPENAI_API_KEY"}
    assert seen["start_new_session"] is True
    assert "--sandbox" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--sandbox") + 1] == "read-only"
    assert seen["argv"][seen["argv"].index("--ask-for-approval") + 1] == "never"
    assert not Path(seen["cwd"]).exists()  # ephemeral, never repository cwd


@pytest.mark.parametrize("auth_key", ["OPENAI_API_KEY", "CODEX_HOME"])
def test_auth_is_explicit_not_inherited(auth_key):
    seen = {}

    def launch(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.Popen([sys.executable, "-c", 'print("ok")'], **kwargs)

    result = CodexRunner(auth_env={auth_key: "test-only-not-a-key"}, popen_factory=launch).run("x")
    assert result.succeeded
    assert seen["env"][auth_key] == "test-only-not-a-key"


def test_environment_does_not_inherit_credentials(monkeypatch):
    for key in ("OPENAI_API_KEY", "CODEX_HOME", "DATABASE_URL", "BITGET_SECRET", "TELEGRAM_TOKEN"):
        monkeypatch.setenv(key, "fake-inherited-value")
    seen = {}

    def launch(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.Popen([sys.executable, "-c", 'print("ok")'], **kwargs)

    assert CodexRunner(popen_factory=launch).run("x").succeeded
    assert not set(seen["env"]) & {
        "OPENAI_API_KEY",
        "CODEX_HOME",
        "DATABASE_URL",
        "BITGET_SECRET",
        "TELEGRAM_TOKEN",
    }


def test_auth_rejects_unscoped_capabilities():
    with pytest.raises(ValueError, match="auth_env"):
        CodexRunner(auth_env={"DATABASE_URL": "fake-db"})


def test_timeout_is_bounded_when_fake_cli_descendant_holds_pipes():
    # Run the actual runner in a separately bounded driver. Never invoke Codex.
    driver = """
import subprocess, sys
from fatty_trader.analyzer.codex_runner import CodexRunner, CodexRunnerConfig
fake = ("import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)']); "
        "time.sleep(10)")
def launch(argv, **kwargs):
    return subprocess.Popen([sys.executable, '-c', fake], **kwargs)
r = CodexRunner(
    config=CodexRunnerConfig(timeout_seconds=0.5, terminate_grace_seconds=0.15),
    popen_factory=launch,
).run('x')
assert r.timed_out and r.terminal_failure and not r.succeeded
print('bounded-timeout')
"""
    started = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, "-c", driver],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            pytest.fail("runner exceeded exact 2-second outer bound with descendant-held pipes")
        assert process.returncode == 0, stderr.decode()
        assert stdout.strip() == b"bounded-timeout"
        assert time.monotonic() - started < 2
    finally:
        # Cleanup even the pre-fix driver; sandbox also removes all descendants.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=1)


def test_reader_joins_have_one_bounded_deadline(monkeypatch):
    import io

    import fatty_trader.analyzer.codex_runner as module

    joins = []

    class Reader:
        def join(self, timeout=None):
            assert timeout is not None, "reader join must never be unbounded"
            joins.append(timeout)

        def is_alive(self):
            return True

    class Process:
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        returncode = 0

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    monkeypatch.setattr(module, "_start_readers", lambda *args: (Reader(), Reader()))
    r = CodexRunner(
        config=CodexRunnerConfig(terminate_grace_seconds=0.1),
        popen_factory=lambda *a, **k: Process(),
    ).run("x")
    assert len(joins) == 2 and all(0 <= t <= 0.1 for t in joins)
    assert r.terminal_failure and not r.succeeded
