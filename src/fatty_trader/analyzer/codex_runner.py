from __future__ import annotations

import ctypes
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
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
        if not 0 < self.timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be finite and in (0, 300]")
        if not 0 < self.terminate_grace_seconds <= 10:
            raise ValueError("terminate_grace_seconds must be finite and in (0, 10]")
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

    The caller is made permanently non-dumpable on Linux before each launch, with
    verified no-new-privs and no inherited CAP_SYS_PTRACE. This blocks same-UID
    children from reading the credential-bearing parent's proc environ/memory.
    The temporary cwd, allowlisted environment and CLI read-only sandbox are NOT
    filesystem read confinement. Deploy with only dedicated read-only auth.json
    and intake media mounts; never mount other credential files or host homes.
    """

    def __init__(
        self,
        *,
        config: CodexRunnerConfig | None = None,
        popen_factory: Callable[..., _Process] | None = None,
        auth_env: Mapping[str, str] | None = None,
    ) -> None:
        self._auth_env = dict(auth_env or {})
        if set(self._auth_env) - {"OPENAI_API_KEY", "CODEX_HOME"}:
            raise ValueError("auth_env only permits OPENAI_API_KEY and CODEX_HOME")
        self._config = config or CodexRunnerConfig()
        self._popen_factory = popen_factory or cast(Callable[..., _Process], subprocess.Popen)

    def build_argv(self, prompt: str, *, image_paths: Sequence[str] = ()) -> list[str]:
        argv = [
            self._config.executable,
            "--ask-for-approval",
            "never",
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--model",
            self._config.model,
            "-c",
            f'model_reasoning_effort="{self._config.reasoning_effort}"',
        ]
        argv.append(prompt)
        for image_path in image_paths:
            argv.extend(("--image", image_path))
        return argv

    def run(self, prompt: str, *, image_paths: Sequence[str] = ()) -> CodexRunResult:
        try:
            _harden_credential_parent()
        except (OSError, AttributeError, ValueError, KeyError):
            # No child (including an injected factory) may start without this boundary.
            return self._failure("codex parent credential boundary unavailable")
        with TemporaryDirectory(prefix="codex-run-") as cwd:
            return self._run(prompt, image_paths=image_paths, cwd=cwd)

    def _run(self, prompt: str, *, image_paths: Sequence[str], cwd: str) -> CodexRunResult:
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": cwd,
            "TMPDIR": cwd,
            "LANG": "C.UTF-8",
        }
        env.update(self._auth_env)
        try:
            process = self._popen_factory(
                self.build_argv(prompt, image_paths=image_paths),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env=env,
                cwd=cwd,
                start_new_session=True,
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

        deadline = time.monotonic() + self._config.terminate_grace_seconds
        for reader in readers:
            reader.join(timeout=max(0.0, deadline - time.monotonic()))
        if any(reader.is_alive() for reader in readers):
            _signal_process(process, signal.SIGKILL)
            if not timed_out:
                return self._failure("codex output pipes did not close")

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
        _signal_process(process, signal.SIGTERM)
        try:
            return process.wait(timeout=self._config.terminate_grace_seconds)
        except subprocess.TimeoutExpired:
            _signal_process(process, signal.SIGKILL)
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


def _harden_credential_parent() -> None:
    """Permanently deny unprivileged same-UID proc/ptrace reads before spawning.

    Harden the credential-bearing caller, NOT a preexec child. Exec resets child
    dumpability; it cannot reset the parent's. Never restore the parent's flag
    after wait: descendants may survive. Reapply and verify on every invocation.
    No-new-privs prevents a child from gaining a ptrace bypass via setuid/file caps.
    This is not filesystem confinement; deployment must exclude secret mounts.
    """
    if sys.platform != "linux":
        raise OSError("Linux parent boundary required")
    status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    # Reject authority that an exec child could inherit to bypass non-dumpability.
    for field in ("CapPrm", "CapEff", "CapInh", "CapAmb"):
        if int(status[field], 16) & (1 << 19):  # CAP_SYS_PTRACE
            raise OSError("ptrace-capable identity is not isolated")
    prctl = ctypes.CDLL(None, use_errno=True).prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    for option, value in ((38, 1), (4, 0)):  # PR_SET_NO_NEW_PRIVS, PR_SET_DUMPABLE
        if prctl(option, value, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "parent hardening failed")
    if prctl(39, 0, 0, 0, 0) != 1 or prctl(3, 0, 0, 0, 0) != 0:
        raise OSError("parent hardening verification failed")


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
    stdout_reader = Thread(target=stdout.read_from, args=(process.stdout,), daemon=True)
    stderr_reader = Thread(target=stderr.read_from, args=(process.stderr,), daemon=True)
    stdout_reader.start()
    stderr_reader.start()
    return stdout_reader, stderr_reader


def _signal_process(process: _Process, sig: signal.Signals) -> None:
    # Real children own a new session; never signal the caller's process group.
    try:
        if isinstance(process, subprocess.Popen):
            os.killpg(process.pid, sig)
        elif sig == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:
        pass


def _redact(text: str) -> str:
    text = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match['name']}{match['separator']}{_REDACTED}", text
    )
    text = _BEARER_TOKEN.sub(f"Bearer {_REDACTED}", text)
    return _OPENAI_STYLE_KEY.sub(_REDACTED, text)
