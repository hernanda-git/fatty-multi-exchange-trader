from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from threading import Thread
from typing import BinaryIO, Protocol, cast

_REDACTED = "[REDACTED]"
_TRUNCATED = "[truncated]"
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?P<name>api[_-]?key|token|secret|authorization)\s*(?P<separator>=|:)\s*"
    r"(?:bearer\s+)?[^\s,;]+"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_OPENAI_STYLE_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]+")


class _Process(Protocol):
    stdout: BinaryIO | None
    stderr: BinaryIO | None
    returncode: int | None

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


@dataclass(frozen=True)
class CodexRunnerConfig:
    """Resource limits for the local, literal `codex exec` subprocess."""

    executable: str = "codex"
    model: str = "gpt-5.6-luna"
    reasoning_effort: str = "medium"
    timeout_seconds: float = 30.0
    terminate_grace_seconds: float = 2.0
    max_output_bytes: int = 8_192

    def __post_init__(self) -> None:
        if not self.executable:
            raise ValueError("executable must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.terminate_grace_seconds <= 0:
            raise ValueError("terminate_grace_seconds must be positive")
        if self.max_output_bytes < len(_TRUNCATED.encode("utf-8")):
            raise ValueError("max_output_bytes is too small for truncation marker")


@dataclass(frozen=True)
class CodexRunResult:
    """Only terminal subprocess state; it contains no order or execution capability."""

    succeeded: bool
    terminal_failure: bool
    timed_out: bool
    exit_code: int | None
    failure_reason: str | None
    stdout: str
    stderr: str


class CodexRunner:
    """Fail closed around a literal Codex CLI invocation.

    A successful process only returns bounded, redacted terminal output. Interpreting that
    output and planning paper dispatches remain separate responsibilities.
    """

    def __init__(
        self,
        *,
        config: CodexRunnerConfig | None = None,
        popen_factory: Callable[..., _Process] | None = None,
    ) -> None:
        self._config = config or CodexRunnerConfig()
        self._popen_factory = popen_factory or cast(Callable[..., _Process], subprocess.Popen)

    def run(self, prompt: str) -> CodexRunResult:
        try:
            process = self._popen_factory(
                [
                    self._config.executable,
                    "exec",
                    "--model",
                    self._config.model,
                    "-c",
                    f'model_reasoning_effort="{self._config.reasoning_effort}"',
                    prompt,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except FileNotFoundError:
            return self._failure("codex executable unavailable")
        except OSError:
            return self._failure("codex could not be started")

        stdout = _BoundedCapture(self._config.max_output_bytes)
        stderr = _BoundedCapture(self._config.max_output_bytes)
        readers = _start_readers(process, stdout, stderr)
        timed_out = False

        exit_code: int | None
        try:
            exit_code = process.wait(timeout=self._config.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = self._stop_after_timeout(process)

        for reader in readers:
            reader.join()

        rendered_stdout = stdout.render()
        rendered_stderr = stderr.render()
        if timed_out:
            return CodexRunResult(
                succeeded=False,
                terminal_failure=True,
                timed_out=True,
                exit_code=exit_code,
                failure_reason="codex timed out",
                stdout=rendered_stdout,
                stderr=rendered_stderr,
            )
        if exit_code != 0:
            return CodexRunResult(
                succeeded=False,
                terminal_failure=True,
                timed_out=False,
                exit_code=exit_code,
                failure_reason=f"codex exited with status {exit_code}",
                stdout=rendered_stdout,
                stderr=rendered_stderr,
            )
        return CodexRunResult(
            succeeded=True,
            terminal_failure=False,
            timed_out=False,
            exit_code=exit_code,
            failure_reason=None,
            stdout=rendered_stdout,
            stderr=rendered_stderr,
        )

    def _stop_after_timeout(self, process: _Process) -> int | None:
        process.terminate()
        try:
            return process.wait(timeout=self._config.terminate_grace_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                return process.wait(timeout=self._config.terminate_grace_seconds)
            except subprocess.TimeoutExpired:
                return None

    @staticmethod
    def _failure(reason: str) -> CodexRunResult:
        return CodexRunResult(
            succeeded=False,
            terminal_failure=True,
            timed_out=False,
            exit_code=None,
            failure_reason=reason,
            stdout="",
            stderr="",
        )


class _BoundedCapture:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._data = bytearray()
        self._truncated = False

    def read_from(self, stream: BinaryIO) -> None:
        while chunk := stream.read(1_024):
            available = self._limit - len(self._data)
            if available > 0:
                self._data.extend(chunk[:available])
            if len(chunk) > available:
                self._truncated = True

    def render(self) -> str:
        text = _redact(bytes(self._data).decode("utf-8", errors="replace"))
        encoded = text.encode("utf-8")
        if not self._truncated and len(encoded) <= self._limit:
            return text
        suffix = _TRUNCATED.encode("utf-8")
        return encoded[: self._limit - len(suffix)].decode("utf-8", errors="ignore") + _TRUNCATED


def _start_readers(
    process: _Process, stdout: _BoundedCapture, stderr: _BoundedCapture
) -> tuple[Thread, Thread]:
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("codex output pipes were not created")
    stdout_reader = Thread(target=stdout.read_from, args=(process.stdout,))
    stderr_reader = Thread(target=stderr.read_from, args=(process.stderr,))
    stdout_reader.start()
    stderr_reader.start()
    return stdout_reader, stderr_reader


def _redact(text: str) -> str:
    text = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match['name']}{match['separator']}{_REDACTED}", text
    )
    text = _BEARER_TOKEN.sub(f"Bearer {_REDACTED}", text)
    return _OPENAI_STYLE_KEY.sub(_REDACTED, text)
