#!/usr/bin/env python3
# ruff: noqa: E501
"""Print the deploy-gate verdict for the live lane (read-only).

The rollout calls the same gate before it stops anything; this helper exists so an
operator can ask "may I deploy right now?" without running a rollout.

    cd ~/apps/fatty-plan && python3 scripts/deploy_gate_check.py

Runs under the host system interpreter (3.9) on purpose: that is what the
rollout uses. Override the target with FATTY_ROOT.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from fatty_trader.ops.deploy_safety import evaluate_deploy_gate  # noqa: E402

LIVE_ROOT = os.environ.get("FATTY_ROOT", "/home/valarion/apps/fatty-multi-exchange-trader")


def query_one(sql: str):
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "fatty_app",
            "-d",
            "fatty_trader",
            "-At",
            "-F",
            "\t",
            "-c",
            sql,
        ],
        cwd=LIVE_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    text = result.stdout.strip()
    return tuple(text.split("\t")) if text else None


if __name__ == "__main__":
    quiet = int(os.environ.get("FATTY_GATE_QUIET_MINUTES", "5"))
    print(
        json.dumps(evaluate_deploy_gate(query_one, quiet_minutes=quiet), indent=1, sort_keys=True)
    )
