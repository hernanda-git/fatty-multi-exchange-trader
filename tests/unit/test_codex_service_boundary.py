from __future__ import annotations

import io
from pathlib import Path

import pytest

from fatty_trader import service
from fatty_trader.analyzer.codex_runner import CodexRunner, CodexRunnerConfig


def test_service_passes_only_explicit_auth_to_isolated_process(monkeypatch, tmp_path):
    monkeypatch.setenv("BITGET_API_SECRET", "not-for-codex")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-not-for-codex")
    calls = []

    class Process:
        stdout = io.BytesIO(b"{}")
        stderr = io.BytesIO(b"")
        returncode = 0

        def wait(self, timeout=None):
            return 0

    def spawn(argv, **kwargs):
        calls.append((argv, kwargs))
        assert Path(kwargs["cwd"]).is_dir()
        return Process()

    runner = service.build_codex_runner(
        {
            "CODEX_HOME": str(tmp_path / "auth"),
            "OPENAI_API_KEY": "explicit-test-key",
            "PGPASSWORD": "not-for-codex",
            "BITGET_API_SECRET": "not-for-codex",
            "TELEGRAM_BOT_TOKEN": "not-for-codex",
            "PATH": "/untrusted/path",
        },
        popen_factory=spawn,
    )
    image = str(tmp_path / "chart.png")
    assert runner.run("chart-only", image_paths=[image]).succeeded
    argv, options = calls[0]
    assert options["env"] == {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": options["cwd"],
        "TMPDIR": options["cwd"],
        "LANG": "C.UTF-8",
        "CODEX_HOME": str(tmp_path / "auth"),
        "OPENAI_API_KEY": "explicit-test-key",
    }
    assert argv[-2:] == ["--image", image]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in argv
    assert "--ignore-rules" in argv
    assert "--ephemeral" in argv
    assert options["shell"] is False
    assert options["start_new_session"] is True
    assert not Path(options["cwd"]).exists()


def test_runner_does_not_inherit_ambient_auth(monkeypatch):
    monkeypatch.setenv("CODEX_HOME", "/production/auth")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-not-for-codex")
    calls = []

    class Process:
        stdout = io.BytesIO(b"{}")
        stderr = io.BytesIO(b"")
        returncode = 0

        def wait(self, timeout=None):
            return 0

    def spawn(argv, **kwargs):
        calls.append(kwargs)
        return Process()

    assert CodexRunner(popen_factory=spawn).run("test").succeeded
    assert "CODEX_HOME" not in calls[0]["env"]
    assert "OPENAI_API_KEY" not in calls[0]["env"]


@pytest.mark.parametrize("field", ["timeout_seconds", "terminate_grace_seconds"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), float("-inf"), 1_000_000])
def test_runner_rejects_unbounded_timeout_config(field, value):
    with pytest.raises(ValueError, match=field):
        CodexRunnerConfig(**{field: value})


@pytest.mark.parametrize(
    "key", ["PGPASSWORD", "BITGET_API_SECRET", "TELEGRAM_BOT_TOKEN", "PATH", "LD_PRELOAD"]
)
def test_auth_environment_rejects_non_auth_capabilities(key):
    with pytest.raises(ValueError, match="auth_env"):
        CodexRunner(auth_env={key: "must-not-pass"})
