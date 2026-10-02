"""Offline real-process probe; synthetic credentials only, no Codex/provider calls."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from fatty_trader.analyzer.codex_runner import CodexRunner
from fatty_trader.worker_health import WorkerHealth, _process_identity

CHILD = r"""
import ctypes, errno, json, os
from pathlib import Path
pid = os.getppid()
result = {"uid": os.getuid()}
try:
    result["stat_identity"] = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
except FileNotFoundError:
    result["stat_identity"] = None
for name in ("environ", "mem"):
    try:
        with open(f"/proc/{pid}/{name}", "rb") as stream:
            data = stream.read(65536) if name == "environ" else b""
        result[name] = {
            "opened": True,
            "synthetic_secret_found": b"SYNTHETIC_DB_SECRET=kernel-proof-only" in data,
        }
    except OSError as exc:
        result[name] = {"opened": False, "errno": exc.errno}
result["child_has_db_secret"] = "SYNTHETIC_DB_SECRET" in os.environ
# A child may reset its OWN dumpability; this cannot unlock the parent.
ctypes.CDLL(None).prctl(4, 1, 0, 0, 0)
try:
    open(f"/proc/{pid}/environ", "rb").close()
    result["reset_self_still_denied"] = False
except OSError as exc:
    result["reset_self_still_denied"] = exc.errno in (errno.EACCES, errno.ENOENT)
print(json.dumps(result))
"""


def main() -> None:
    assert os.environ.get("SYNTHETIC_DB_SECRET") == "kernel-proof-only"
    baseline = json.loads(
        subprocess.check_output([sys.executable, "-c", CHILD], env={"PATH": "/usr/bin:/bin"})
    )
    assert baseline["environ"]["synthetic_secret_found"], baseline
    with TemporaryDirectory() as root:
        env = {
            "WORKER_HEALTH_PATH": root + "/progress.json",
            "WORKER_HEALTH_SOCKET_PATH": root + "/owner.sock",
        }
        health = WorkerHealth("analyzer", env)
        identity = _process_identity(os.getpid())

        def spawn(argv, **kwargs):
            assert argv[argv.index("--sandbox") + 1] == "read-only"
            return subprocess.Popen([sys.executable, "-c", CHILD], **kwargs)

        result = CodexRunner(popen_factory=spawn).run("synthetic offline proof")
        assert result.succeeded, result
        hardened = json.loads(result.stdout)
        health.progress()
        check = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json,sys; "
                "from fatty_trader.worker_health import check_worker_health; "
                "sys.exit(check_worker_health('analyzer', json.loads(sys.argv[1])))",
                json.dumps(env),
            ],
            capture_output=True,
            text=True,
        )
        hidden = (
            "--allow-host-hidepid" in sys.argv and "hidepid=" in Path("/proc/mounts").read_text()
        )
        assert check.returncode == (1 if hidden else 0), check.stdout + check.stderr
        assert _process_identity(os.getpid()) == identity
        assert hardened["stat_identity"] == (None if hidden else identity)
        health.close()
    print(
        json.dumps(
            {
                "baseline": baseline,
                "hardened": hardened,
                "health_check": check.stdout.strip(),
                "identity_stable": True,
                "dumpable": ctypes.CDLL(None).prctl(3, 0, 0, 0, 0),
                "no_new_privs": ctypes.CDLL(None).prctl(39, 0, 0, 0, 0),
                "source": str(Path(sys.modules[CodexRunner.__module__].__file__).resolve()),
            },
            sort_keys=True,
        )
    )
    assert hardened["uid"] == os.getuid()
    assert not hardened["child_has_db_secret"]
    assert hardened["reset_self_still_denied"]
    for name in ("environ", "mem"):
        assert hardened[name] == {"opened": False, "errno": 2 if hidden else 13}, hardened


if __name__ == "__main__":
    main()
