from __future__ import annotations

import io
import subprocess
from collections.abc import Callable
from typing import Any

from fatty_trader.analyzer.codex_runner import CodexRunner, CodexRunnerConfig


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        wait_results: list[int | BaseException] | None = None,
    ) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode: int | None = None
        self._final_returncode = returncode
        self._wait_results = list(wait_results or [returncode])
        self.terminated = False
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        outcome = self._wait_results.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        self.returncode = outcome
        return outcome

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def factory_for(
    process: FakeProcess, calls: list[tuple[list[str], dict[str, Any]]]
) -> Callable[..., FakeProcess]:
    def factory(argv: list[str], **kwargs: Any) -> FakeProcess:
        calls.append((argv, kwargs))
        return process

    return factory


def test_runner_uses_literal_codex_exec_argv_without_a_shell() -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    runner = CodexRunner(popen_factory=factory_for(FakeProcess(stdout=b'{"signal": null}'), calls))

    result = runner.run("analyze this; do not execute it")

    assert result.succeeded
    assert not result.terminal_failure
    assert calls == [
        (
            [
                "codex",
                "exec",
                "--model",
                "gpt-5.6-luna",
                "-c",
                'model_reasoning_effort="medium"',
                "analyze this; do not execute it",
            ],
            {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "shell": False,
            },
        )
    ]


def test_runner_returns_nonzero_exit_as_terminal_failure() -> None:
    runner = CodexRunner(
        popen_factory=factory_for(FakeProcess(stderr=b"bad request", returncode=7), [])
    )

    result = runner.run("analyze")

    assert not result.succeeded
    assert result.terminal_failure
    assert result.exit_code == 7
    assert result.failure_reason == "codex exited with status 7"


def test_runner_bounds_and_redacts_terminal_output() -> None:
    runner = CodexRunner(
        config=CodexRunnerConfig(max_output_bytes=36),
        popen_factory=factory_for(
            FakeProcess(stdout=b"token=super-secret-value and more diagnostic output"), []
        ),
    )

    result = runner.run("analyze")

    assert result.succeeded
    assert "super-secret-value" not in result.stdout
    assert "[REDACTED]" in result.stdout
    assert result.stdout.endswith("[truncated]")
    assert len(result.stdout.encode("utf-8")) <= 36


def test_redacted_output_stays_within_configured_bound() -> None:
    runner = CodexRunner(
        config=CodexRunnerConfig(max_output_bytes=15),
        popen_factory=factory_for(FakeProcess(stdout=b"token=x"), []),
    )

    result = runner.run("analyze")

    assert len(result.stdout.encode("utf-8")) <= 15


def test_runner_terminates_then_kills_when_grace_period_expires() -> None:
    process = FakeProcess(
        wait_results=[
            subprocess.TimeoutExpired(cmd="codex", timeout=3),
            subprocess.TimeoutExpired(cmd="codex", timeout=1),
            -9,
        ]
    )
    runner = CodexRunner(
        config=CodexRunnerConfig(timeout_seconds=3, terminate_grace_seconds=1),
        popen_factory=factory_for(process, []),
    )

    result = runner.run("analyze")

    assert not result.succeeded
    assert result.terminal_failure
    assert result.timed_out
    assert result.failure_reason == "codex timed out"
    assert process.terminated
    assert process.killed


def test_runner_returns_timeout_failure_when_kill_cannot_be_confirmed() -> None:
    process = FakeProcess(
        wait_results=[
            subprocess.TimeoutExpired(cmd="codex", timeout=3),
            subprocess.TimeoutExpired(cmd="codex", timeout=1),
            subprocess.TimeoutExpired(cmd="codex", timeout=1),
        ]
    )
    runner = CodexRunner(
        config=CodexRunnerConfig(timeout_seconds=3, terminate_grace_seconds=1),
        popen_factory=factory_for(process, []),
    )

    result = runner.run("analyze")

    assert result.terminal_failure
    assert result.timed_out
    assert result.exit_code is None
    assert process.terminated
    assert process.killed


def test_runner_returns_spawn_errors_as_terminal_failures() -> None:
    def unavailable(_: list[str], **__: Any) -> FakeProcess:
        raise FileNotFoundError("codex")

    result = CodexRunner(popen_factory=unavailable).run("analyze")

    assert not result.succeeded
    assert result.terminal_failure
    assert result.exit_code is None
    assert result.failure_reason == "codex executable unavailable"
