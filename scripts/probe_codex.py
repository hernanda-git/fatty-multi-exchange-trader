"""Read-only Phase-0 Codex CLI capability probe; never sends a trade or credentials."""

from __future__ import annotations

import shutil
import subprocess
import sys


def main() -> int:
    executable = shutil.which("codex")
    if executable is None:
        print("BLOCKED: codex executable is unavailable", file=sys.stderr)
        return 2
    for arguments in ([executable, "--version"], [executable, "exec", "--help"]):
        result = subprocess.run(arguments, check=False, text=True, capture_output=True, timeout=20)
        print(result.stdout[:8000])
        if result.returncode != 0:
            print(result.stderr[:2000], file=sys.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
