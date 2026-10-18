#!/usr/bin/env python3
# ruff: noqa: E501
"""Gated, staged rollout of a verified revision onto fspmi-hostinger.

Run as the trusted host operator (valarion). Fail-fast, evidence at every gate.
Does not replay any historical signal, does not clear latches, and refuses to
start entry-capable consumers unless a fresh authenticated flat proof succeeds.

Safety properties added for the master-plan workstream:

* a deploy gate refuses to start while the lane is busy (in-flight dispatch, open
  entry intent or reservation, signal inside its entry window, recent message),
  unless ``--force-reason`` records an explicit override;
* a maintenance window is opened before anything stops and closed at the end, and
  every channel message that lapsed inside a window is recorded and reported, so a
  rollout can never silently cost a signal;
* the revision is an argument, so switching between the stable line and the plan
  line is ``--branch main`` / ``--branch master-plan`` (or an explicit SHA), with
  the same gate, window accounting and verification on both.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# The script imports from its own tree (so the plan workspace can run it), while the
# deploy target stays the live checkout, overridable for a rehearsal.
CODE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = Path(os.environ.get("FATTY_ROOT", "/home/valarion/apps/fatty-multi-exchange-trader"))
sys.path.insert(0, str(CODE_ROOT / "src"))
from fatty_trader.ops.deploy_safety import (  # noqa: E402
    close_maintenance_window,
    evaluate_deploy_gate,
    open_maintenance_window,
    open_windows_sql,
    sweep_missed_signals,
)

ROOT = str(PROJECT_ROOT)
UV = ROOT + "/runtime/console-ops/uv"
VENV_PY = ROOT + "/runtime/console-ops-venv/bin/python"
ENTRY_CAPABLE = ["dispatcher-bitget", "console-worker", "operator-bot", "source-management"]
READ_ONLY = ["web", "intake", "analyzer", "notification-sender"]

FLAT_PROBE = """import asyncio,json,os
from fatty_trader.exchanges.bitget.client import BitgetRestClient
from fatty_trader.storage.bitget_baseline import collect_flat_evidence
async def probe():
    client=BitgetRestClient(os.environ['BITGET_API_KEY'],os.environ['BITGET_API_SECRET'],os.environ['BITGET_API_PASSPHRASE'],mode='LIVE',max_get_retries=0)
    try:
        evidence=await collect_flat_evidence(client,os.environ['CONSOLE_ACCOUNT_ID'],'LIVE')
        print(json.dumps({'authenticated_flat_proof':True,'finished_ms':evidence['finished_ms'],'products':list(evidence['inventory']),'pending_families_per_product':{key:len(value['pending']) for key,value in evidence['inventory'].items()}}))
    finally: await client.aclose()
asyncio.run(probe())
"""

PRE_SQL = (
    "SELECT now() AS observed_at; "
    "SELECT count(*) AS active_dispatch_leases FROM dispatches WHERE lease_until>now(); "
    "SELECT count(*) AS nonterminal_dispatches FROM dispatches WHERE state NOT IN "
    "('FILLED','REJECTED','EXPIRED','CANCELLED','RECONCILED','FAILED','CLOSED'); "
    "SELECT count(*) AS open_ledger_positions FROM positions WHERE exchange='bitget' AND closed_at IS NULL; "
    "SELECT count(*) AS running_console_jobs FROM console_jobs WHERE state='running' AND lease_until>now(); "
    "SELECT count(*) FILTER (WHERE sent_at IS NULL AND failed_at IS NULL) AS pending_notifications, "
    "count(*) FILTER (WHERE lease_until>now()) AS leased_notifications, "
    "count(*) FILTER (WHERE failed_at IS NOT NULL) AS failed_notifications FROM notifications_outbox; "
    "SELECT max(message_id) AS intake_watermark FROM telegram_messages; "
    "SELECT max(version) AS schema_version FROM schema_migrations;"
)

POST_SQL = (
    "SELECT max(version) AS schema_version FROM schema_migrations; "
    "SELECT version,applied_at FROM schema_migrations WHERE version>=30 ORDER BY version; "
    "SELECT count(*) AS retention_indexes FROM pg_indexes WHERE schemaname='public' AND indexname IN "
    "('console_snapshot_retention','console_snapshot_retention_latest','console_snapshot_retention_good','console_event_retention','console_events_replay_bounds'); "
    "SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolinherit FROM pg_roles WHERE rolname IN ('fatty_worker','fatty_console_web','fatty_worker') ORDER BY rolname; "
    "SELECT count(*) AS parked_source_updates FROM source_management_updates "
    "WHERE state NOT IN ('reconciled','failed','cancelled-flat','entries-cancelled');"
)


def log(payload: object) -> None:
    print(json.dumps(payload), flush=True)


def run(args: list[str], timeout: float | None = None) -> None:
    log(
        {
            "run": [str(a) for a in args][:4],
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True, timeout=timeout)


def out(args: list[str], timeout: float = 60) -> str:
    result = subprocess.run(
        [str(a) for a in args],
        cwd=ROOT,
        check=True,
        timeout=timeout,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def psql(sql: str, *, timeout: float = 60, tuples_only: bool = False) -> str:
    args = [
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
    ]
    args += ["-At", "-F", "\t"] if tuples_only else ["-P", "pager=off"]
    return out(args + ["-c", sql], timeout=timeout)


def count(sql: str) -> str:
    return psql(sql, tuples_only=True)


def query_one(sql: str) -> tuple[str, ...] | None:
    rows = query_all(sql)
    return rows[0] if rows else None


def query_all(sql: str) -> list[tuple[str, ...]]:
    lines = [line for line in psql(sql, tuples_only=True).splitlines() if line]
    return [tuple(line.split("\t")) for line in lines]


def execute(sql: str) -> str:
    return psql(sql)


def resolve_revision(branch: str | None, revision: str | None) -> str:
    run(["git", "fetch", "origin", "--tags"])
    if branch:
        target = f"origin/{branch}"
        run(["git", "checkout", "--detach", target])
    elif revision:
        run(["git", "checkout", "--detach", revision])
    else:  # pragma: no cover - argparse enforces this
        raise SystemExit("either --branch or --revision is required")
    return out(["git", "rev-parse", "HEAD"])


def gate(quiet_minutes: int, force_reason: str | None) -> dict[str, object]:
    verdict = evaluate_deploy_gate(query_one, quiet_minutes=quiet_minutes)
    if verdict["allowed"]:
        log({"deploy_gate": "open", **verdict})
        return verdict
    if not force_reason:
        log({"deploy_gate": "blocked", **verdict})
        raise SystemExit(
            "deploy gate blocked: "
            + ", ".join(str(reason) for reason in verdict["blocked_by"])
            + " (use --force-reason to override)"
        )
    log({"deploy_gate": "forced", "force_reason": force_reason, **verdict})
    return verdict


def settle_previous_windows() -> None:
    """Close windows left open by a crashed rollout and account for them."""
    try:
        windows = query_all(open_windows_sql())
    except Exception as exc:  # noqa: BLE001 - accounting needs migration 34; the rollout does not
        log({"maintenance_accounting": "unavailable", "reason": type(exc).__name__})
        return
    for window_id, started_at, revision, reason in windows:
        log({"stale_window": {"id": window_id, "started_at": started_at, "revision": revision}})
        close_maintenance_window(execute, window_id)
        log({"stale_window_closed": window_id, "reason": reason})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--branch", help="origin branch to deploy (e.g. main, master-plan)")
    target.add_argument("--revision", help="exact commit SHA to deploy")
    parser.add_argument("--quiet-minutes", type=int, default=5)
    parser.add_argument("--force-reason", default=None)
    args = parser.parse_args()

    settle_previous_windows()
    gate(args.quiet_minutes, args.force_reason)

    # ── 1. Stop the host operations worker; no console mutations during rollout.
    run(["systemctl", "--user", "stop", "fatty-console-ops-worker"])

    # ── 2. Fetch the verified revision and detach onto it.
    revision = resolve_revision(args.branch, args.revision)
    log({"revision": revision, "subject": out(["git", "log", "-1", "--format=%s"])})

    # ── 3. Install the updated backup unit (UMask=0077) and reload user units.
    run(
        [
            "cp",
            ROOT + "/deploy/systemd/fatty-backup.service",
            "/home/valarion/.config/systemd/user/fatty-backup.service",
        ]
    )
    run(["systemctl", "--user", "daemon-reload"])

    # ── 4. Refresh the host operations worker's installed package from this tree.
    run([UV, "pip", "install", "--python", VENV_PY, "--no-deps", "--reinstall", "."], timeout=600)
    log(
        {
            "ops_venv": out(
                [
                    VENV_PY,
                    "-c",
                    "import fatty_trader,os;print(os.path.dirname(fatty_trader.__file__))",
                ]
            )
        }
    )

    # ── 5. Provision the least-privilege runtime role and its private secret.
    run([VENV_PY, "scripts/provision_worker_database.py", "--confirm"], timeout=180)
    role = count(
        "SELECT rolname||':'||rolsuper||':'||rolcreatedb||':'||rolcreaterole||':'||rolinherit "
        "FROM pg_roles WHERE rolname='fatty_worker'"
    )
    if role != "fatty_worker:false:false:false:false":
        raise SystemExit(f"worker role privileges unexpected: {role}")
    log({"worker_role": role})

    # ── 6. Build every image at the verified revision.
    run(
        [
            "docker",
            "compose",
            "build",
            "--build-arg",
            "SOURCE_REVISION=" + revision,
            "web",
            "intake",
            "analyzer",
            "dispatcher-bitget",
            "monitor-bitget",
            "console-worker",
            "operator-bot",
            "source-management",
            "notification-sender",
            "migrate",
        ],
        timeout=3600,
    )

    # ── 7. Open the maintenance window before anything stops.
    window_id: str | None
    try:
        window_id = open_maintenance_window(
            execute, revision=revision, reason="staged-rollout", opened_by="rollout"
        )
        log({"maintenance_window": window_id})
    except Exception as exc:  # noqa: BLE001 - accounting needs migration 34; the rollout does not
        window_id = None
        log({"maintenance_accounting": "unavailable", "reason": type(exc).__name__})

    # ── 8. Pre-maintenance durable snapshot (read-only).
    log({"pre_maintenance": psql(PRE_SQL)})

    # ── 9. Quiesce every application service.
    run(["docker", "compose", "stop"], timeout=240)

    # ── 10. Recreate PostgreSQL with bounded logging; the data volume persists.
    run(
        [
            "docker",
            "compose",
            "up",
            "-d",
            "--no-deps",
            "--wait",
            "--wait-timeout",
            "180",
            "postgres",
        ],
        timeout=300,
    )
    log(
        {
            "postgres_after_recreate": psql(
                "SELECT max(version) AS schema_version FROM schema_migrations; "
                "SELECT count(*) AS fills FROM fills; SELECT count(*) AS dispatches FROM dispatches;"
            )
        }
    )

    # ── 11. Apply migrations through the administrative migrator.
    run(["docker", "compose", "run", "--rm", "--no-deps", "migrate"], timeout=600)
    log({"post_migration": psql(POST_SQL)})

    # ── 12. Protection/monitor lane first (no entry capability).
    run(
        [
            "docker",
            "compose",
            "up",
            "-d",
            "--no-deps",
            "--no-build",
            "--wait",
            "--wait-timeout",
            "180",
            "monitor-bitget",
        ],
        timeout=300,
    )

    # ── 13. Fresh authenticated flat proof before any entry-capable consumer.
    proof = out(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "monitor-bitget",
            "/app/.venv/bin/python",
            "-c",
            FLAT_PROBE,
        ],
        timeout=120,
    )
    log({"flat_proof": proof})
    if '"authenticated_flat_proof": true' not in proof.replace("'", '"'):
        raise SystemExit("flat proof did not succeed")
    if count("SELECT count(*) FROM dispatches WHERE lease_until>now()") != "0":
        raise SystemExit("active dispatch lease prevents protection restart")

    # ── 14. Start the remaining read-only and entry-capable consumers.
    run(
        [
            "docker",
            "compose",
            "up",
            "-d",
            "--no-deps",
            "--no-build",
            "--wait",
            "--wait-timeout",
            "240",
        ]
        + READ_ONLY
        + ENTRY_CAPABLE,
        timeout=600,
    )

    # ── 15. Full runtime verifier: health, log bounds, database role boundary.
    run(["bash", "scripts/verify_bitget_runtime.sh"], timeout=300)

    # ── 16. Resume the host operations worker under the new credential model.
    run(["systemctl", "--user", "start", "fatty-console-ops-worker"])
    time.sleep(5)
    log(
        {
            "ops_worker_active": out(
                ["systemctl", "--user", "is-active", "fatty-console-ops-worker"]
            ),
        }
    )

    # ── 17. Post-deployment evidence: log settings and queue state.
    services = READ_ONLY + ENTRY_CAPABLE + ["monitor-bitget", "postgres"]
    log_setting = {}
    for service in services:
        container = out(["docker", "compose", "ps", "-q", service])
        log_setting[service] = out(
            [
                "docker",
                "inspect",
                "--format",
                '{{.HostConfig.LogConfig.Type}}:{{index .HostConfig.LogConfig.Config "max-size"}}:'
                '{{index .HostConfig.LogConfig.Config "max-file"}}',
                container,
            ]
        )
    log({"container_log_config": log_setting})
    log({"post_rollout": psql(POST_SQL)})

    # ── 18. Close the window and account for every signal that lapsed inside it.
    if window_id is None:
        log({"maintenance_accounting": "skipped", "reason": "no-window-recorded"})
    else:
        try:
            close_maintenance_window(execute, window_id)
            missed = sweep_missed_signals(query_all, execute, window_id=window_id)
            log({"maintenance_window_closed": window_id, "missed_signals": missed})
        except Exception as exc:  # noqa: BLE001 - never fail a rollout on accounting
            log(
                {
                    "maintenance_accounting": "failed",
                    "window_id": window_id,
                    "reason": type(exc).__name__,
                }
            )

    log({"rollout": "complete", "revision": revision, "signal_replay": False})


if __name__ == "__main__":
    main()
