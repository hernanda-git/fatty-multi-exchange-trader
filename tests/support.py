"""Shared support for POSIX shell-script contract tests.

Compose, backup and runtime-verifier contracts are POSIX shell scripts. They run
on Linux CI and on Windows developer machines through Git Bash; the Windows
system32 `bash.exe` is the WSL launcher, a different environment, so it is never
selected. The scripts invoke `python3`, so Windows runs get a shim that maps
`python3` to the interpreter executing the tests.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest


def compose_cli(tmp_path: Path, environment: Mapping[str, str]) -> list[str]:
    """Return a Compose CLI that can render a project, or skip when it cannot.

    Some hosts ship a `docker` CLI whose Compose plugin cannot render at all (a
    missing plugin, or a curated environment without the paths the plugin needs),
    so probing `version` is not enough. This renders a minimal project with the
    same flags and environment the deployment tests use.
    """
    binary = shutil.which("docker")
    if binary is None:
        pytest.skip("a working docker compose CLI is required to render the deployment")
    project = tmp_path / "compose-probe"
    project.mkdir(exist_ok=True)
    (project / "docker-compose.yml").write_text(
        "services:\n  probe:\n    image: busybox\n", encoding="utf-8"
    )
    empty_env = project / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    probe = subprocess.run(
        [binary, "compose", "--env-file", str(empty_env), "config", "--format", "json"],
        cwd=project,
        env=dict(environment),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if probe.returncode != 0:
        pytest.skip("a working docker compose CLI is required to render the deployment")
    return [binary, "compose"]


def posix_bash() -> str:
    """Resolve a POSIX bash: Git Bash on Windows, the system shell on POSIX."""
    candidates: list[str] = []
    if os.name == "nt":
        for variable in ("ProgramFiles", "ProgramW6432"):
            base = os.environ.get(variable)
            if base:
                candidates += [
                    os.path.join(base, "Git", "bin", "bash.exe"),
                    os.path.join(base, "Git", "usr", "bin", "bash.exe"),
                ]
    else:
        candidates += ["/usr/bin/bash", "/bin/bash"]
    located = shutil.which("bash")
    if located and Path(located).name.lower() != "bash.exe":
        candidates.append(located)
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    pytest.skip("operational shell contract requires a POSIX bash")


def script_environment(
    tmp_path: Path, base: Mapping[str, str] | None = None, /, **overrides: str
) -> dict[str, str]:
    """Environment for a shell-script contract with a working `python3`.

    Windows hosts keep their platform paths (`SystemRoot`, `ProgramData`, ...):
    without them a child process can resolve `%SystemDrive%` relative to the
    working directory and litter the repository with OS caches.
    """
    env = dict(base) if base is not None else {"PATH": os.environ["PATH"], "HOME": "/tmp"}
    if os.name == "nt":
        for key in (
            "SystemDrive",
            "SystemRoot",
            "windir",
            "ProgramData",
            "ProgramFiles",
            "LOCALAPPDATA",
            "APPDATA",
            "USERPROFILE",
            "TEMP",
            "TMP",
            "COMSPEC",
            "NUMBER_OF_PROCESSORS",
        ):
            if key in os.environ:
                env[key] = os.environ[key]
        env.setdefault("HOME", os.environ.get("USERPROFILE", "/tmp"))
        shim_dir = tmp_path / "python3-shim"
        shim_dir.mkdir(exist_ok=True)
        shim = shim_dir / "python3"
        shim.write_text(
            '#!/bin/sh\nexec "' + sys.executable.replace(os.sep, "/") + '" "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)
        env["PATH"] = f"{shim_dir}{os.pathsep}{env.get('PATH', os.environ['PATH'])}"
    env.update(overrides)
    return env
