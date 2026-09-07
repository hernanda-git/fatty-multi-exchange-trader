#!/usr/bin/env python3
"""Persistent supervised DEMO-only replay maintenance worker."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "runtime" / "autonomous-demo-cycle-state.json"


def _state() -> dict[str, object]:
    try:
        state = json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"cycle_count": 0, "failure_count": 0}
    if not isinstance(state, dict):
        return {"cycle_count": 0, "failure_count": 0}
    return state


def _write_state(state: dict[str, object]) -> None:
    STATE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, sort_keys=True) + "\n")
    temporary.replace(STATE_PATH)


def _assert_demo_only() -> None:
    if os.environ.get("TRADER_MODE", "").upper() != "DEMO":
        raise RuntimeError("autonomous worker refuses non-DEMO trader mode")
    if os.environ.get("BITGET_MODE", "").upper() != "DEMO":
        raise RuntimeError("autonomous worker refuses non-DEMO Bitget mode")
    if os.environ.get("BITGET_EXECUTION_ENABLED", "") != "0":
        raise RuntimeError("autonomous worker requires BITGET_EXECUTION_ENABLED=0")


def run_cycle() -> dict[str, object]:
    _assert_demo_only()
    state = _state()
    cycle_id = str(state.get("pending_cycle_id") or uuid4())
    state["pending_cycle_id"] = cycle_id
    _write_state(state)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "replay_paper_pipeline.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    replay = json.loads(result.stdout)
    cycle_count = state.get("cycle_count", 0)
    if not isinstance(cycle_count, int) or isinstance(cycle_count, bool):
        cycle_count = 0
    state.update(
        {
            "cycle_count": cycle_count + 1,
            "cycle_id": cycle_id,
            "last_success_at": datetime.now(UTC).isoformat(),
            "failure_count": 0,
            "replay": replay,
        }
    )
    state.pop("pending_cycle_id", None)
    _write_state(state)
    return state


def main() -> int:
    interval = max(30, int(os.environ.get("AUTONOMOUS_DEMO_CYCLE_SECONDS", "60")))
    failure_count = 0
    while True:
        try:
            state = run_cycle()
            print(f"cycle_id={state['cycle_id']} status=PASS", flush=True)
            failure_count = 0
            delay = interval
        except Exception as exc:
            failure_count += 1
            delay = min(interval * (2 ** min(failure_count, 5)), 1800)
            state = _state()
            state.update(
                {
                    "last_failure_at": datetime.now(UTC).isoformat(),
                    "failure_count": failure_count,
                    "last_error": type(exc).__name__,
                }
            )
            _write_state(state)
            print(f"status=FAIL error={type(exc).__name__} backoff_seconds={delay}", flush=True)
        time.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(main())
