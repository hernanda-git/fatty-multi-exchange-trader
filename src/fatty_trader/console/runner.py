"""Host-only executor. No shell, arbitrary refs, filesystem arguments or POST replay."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from fatty_trader.console.operations import (
    CONFIG_FIELDS,
    HOST_ACTIONS,
    SECRET_KINDS,
    SERVICES,
    OperationRefused,
    SecretStages,
    digest,
    validate_arguments,
)
from fatty_trader.console.store import ConsoleStore

MUTATION_CONSUMERS = frozenset(
    ("dispatcher-bitget", "monitor-bitget", "operator-bot", "source-management", "console-worker")
)
BITGET_SERVICES = frozenset(
    ("dispatcher-bitget", "monitor-bitget", "operator-bot", "source-management", "console-worker")
)
SETTINGS_CONSUMERS = {
    "BITGET_DISPATCH_POLL_SECONDS": ("dispatcher-bitget", "console-worker"),
    "BITGET_MONITOR_POLL_SECONDS": ("monitor-bitget",),
    "NOTIFICATION_RETRY_SECONDS": ("notification-sender",),
    "NOTIFICATION_MAX_ATTEMPTS": ("notification-sender",),
    "ANALYZER_CHANNEL_IDS": ("analyzer",),
    "TELEGRAM_SOURCE_CHANNELS": ("intake",),
    "BITGET_API_KEY": tuple(BITGET_SERVICES),
    "BITGET_API_SECRET": tuple(BITGET_SERVICES),
    "BITGET_API_PASSPHRASE": tuple(BITGET_SERVICES),
    "TG_BOT_TOKEN": ("notification-sender", "operator-bot"),
}
SOURCE_PROBE = (
    "import "
    "hashlib,pathlib;root=pathlib.Path('/app/src');files=sorted(root.rglob('*.py'));assert "
    "files;h=hashlib.sha256();"
    "[(h.update(str(p.relative_to(root)).encode()+b'\\0'+p.read_bytes()+b'\\0')) for p in "
    "files];print(h.hexdigest())"
)


class HostOperationsRunner:
    def __init__(
        self,
        environ: Mapping[str, str],
        store: ConsoleStore,
        command_runner: Callable[..., Any] | None = None,
    ) -> None:
        if environ.get("CONSOLE_OPS_WORKER") != "1":
            raise OperationRefused("host runner is not enabled in this process")
        self.environ, self.store = dict(environ), store
        if not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]{0,62}", environ.get("CONSOLE_COMPOSE_PROJECT", "")
        ):
            raise OperationRefused("exact approved Compose project is required")
        self.root = Path(environ["CONSOLE_REPOSITORY_ROOT"]).resolve(strict=True)
        state_path = Path(environ["CONSOLE_OPS_STATE_DIR"])
        backup_path = Path(environ["CONSOLE_BACKUP_DIR"])
        for directory in (state_path, backup_path):
            if (
                directory.is_symlink()
                or not directory.is_dir()
                or (os.name != "nt" and stat.S_IMODE(directory.stat().st_mode) & 0o077)
            ):
                raise OperationRefused("host state and backups must be dedicated 0700 directories")
        self.state_dir = state_path.resolve(strict=True)
        self.backup_dir = backup_path.resolve(strict=True)
        if self.state_dir == self.backup_dir:
            raise OperationRefused("backups require a separate dedicated directory")
        self.scope = {
            "account_id": environ.get("CONSOLE_ACCOUNT_ID")
            or environ.get("BITGET_ACCOUNT_ID")
            or "configured-bitget",
            "venue": "bitget",
            "environment": "LIVE",
        }
        self.command_runner = command_runner or subprocess.run
        self._observation_index = 0
        self._service_evidence: dict[str, dict[str, Any]] = {}
        self._log_evidence: dict[str, dict[str, Any]] = {}
        self.override = self.state_dir / "runtime.compose.json"
        self.release_override = self.state_dir / "release.compose.json"
        self.compose_files = [self.root / "docker-compose.yml"]
        for path in (self.override, self.release_override):
            if path.exists():
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or (os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077)
                ):
                    raise OperationRefused("host Compose overrides must be private regular files")
                self.compose_files.append(path)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        import fcntl

        with (self.state_dir / "operations.lock").open("a+b") as stream:
            os.chmod(stream.name, 0o600)
            cast(Any, fcntl).flock(stream, cast(Any, fcntl).LOCK_EX)
            try:
                yield
            finally:
                cast(Any, fcntl).flock(stream, cast(Any, fcntl).LOCK_UN)

    def _environment(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        allowed = {
            "PATH",
            "HOME",
            "LANG",
            "TZ",
            "POSTGRES_PASSWORD",
            "CONSOLE_DB_PASSWORD",
            "POSTGRES_USER",
            "POSTGRES_DB",
            "PGHOST",
            "PGPORT",
            "PGDATABASE",
            "PGUSER",
            "PGPASSWORD",
            "DATABASE_URL",
            "TRADER_MODE",
            "BITGET_MODE",
            "BITGET_EXECUTION_ENABLED",
            "TG_BOT_TOKEN",
            "TG_OPERATOR_ID",
            "TELEGRAM_TARGET_CHAT_ID",
            "TELEGRAM_SOURCE_CHANNELS",
            "TELEGRAM_API_ID",
            "TELEGRAM_API_HASH",
            "CODEX_HOST_UID",
            "CODEX_HOST_GID",
            "CONSOLE_ORIGIN",
            "CONSOLE_WEBAUTHN_RP_ID",
            "CONSOLE_ACCOUNT_ID",
            "BITGET_ACCOUNT_ID",
        }
        allowed.update(("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"))
        # Approved host environment only. None of these names/values come from job arguments.
        allowed.update(
            k
            for k in self.environ
            if k.startswith(("BITGET_", "CONSOLE_", "NOTIFICATION_", "ANALYZER_"))
        )
        env = {k: v for k, v in self.environ.items() if k in allowed and isinstance(v, str)}
        env.update(
            {
                "COMPOSE_FILE": os.pathsep.join(str(p) for p in self.compose_files),
                "COMPOSE_PROJECT_NAME": self.environ["CONSOLE_COMPOSE_PROJECT"],
            }
        )
        if extra:
            env.update(extra)
        return env

    def _run(
        self,
        argv: list[str],
        *,
        input_file: Path | None = None,
        extra: dict[str, str] | None = None,
        timeout: int = 300,
        binary: bool = False,
    ) -> Any:
        if not isinstance(argv, list) or any(not isinstance(arg, str) for arg in argv):
            raise OperationRefused("invalid fixed command")
        stdin = input_file.open("rb") if input_file else None
        try:
            completed = self.command_runner(
                argv,
                cwd=self.root,
                env=self._environment(extra),
                stdin=stdin,
                capture_output=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
        finally:
            if stdin:
                stdin.close()
        if completed.returncode:
            # Do not surface subprocess stdout/stderr; Docker/config can contain secrets.
            raise OperationRefused("fixed host command failed; reconcile observations before retry")
        if binary:
            return completed.stdout
        return (
            completed.stdout.decode("utf-8")
            if isinstance(completed.stdout, bytes)
            else completed.stdout
        )

    def _compose(self, *args: str, **kwargs: Any) -> Any:
        return self._run(["/usr/bin/docker", "compose", *args], **kwargs)

    def observe(self) -> dict[str, Any]:
        if self.environ.get("CONSOLE_SECRET_STAGE_DIR"):
            SecretStages(self.environ).cleanup()
        raw = self._compose("ps", "--all", "--format", "json")
        try:
            decoded = json.loads(raw or "[]")
            rows = decoded if isinstance(decoded, list) else [decoded]
        except ValueError:
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        items = []
        for row in rows:
            if row.get("Service") in SERVICES or row.get("Service") == "postgres":
                items.append(
                    {
                        "service_id": row["Service"],
                        "state": row.get("State", "unknown"),
                        "health": row.get("Health") or None,
                        "container_id": row.get("ID"),
                        "image": row.get("Image"),
                    }
                )
        for service_id in set(self._log_evidence) - {item["service_id"] for item in items}:
            self._log_evidence.pop(service_id, None)
            self._service_evidence.pop(service_id, None)
        if items:
            selected = items[self._observation_index % len(items)]
            self._observation_index += 1
            self._observe_service(selected)
        for item in items:
            evidence = self._service_evidence.get(item["service_id"], {})
            if evidence.get("container_id") == item.get("container_id"):
                item.update(evidence)
        unreviewed_running = [
            row.get("Service")
            for row in rows
            if row.get("Service") not in (*SERVICES, "postgres")
            and row.get("State") in ("running", "restarting", "paused")
        ]
        data = {
            "items": items,
            "unreviewed_running_services": unreviewed_running,
            "observed_at": datetime.now(UTC).isoformat(),
            "source": "host docker compose ps GET",
        }
        self.store.put_snapshot("services", self.scope, data, observed_at=datetime.now(UTC))
        try:
            self.observe_schedule()
        except Exception:
            self.store.put_snapshot(
                "notification_schedule",
                self.scope,
                None,
                error={"code": "timer_observation_unavailable"},
            )
        return data

    def _observe_service(self, service: dict[str, Any]) -> None:
        from fatty_trader.console.reads import sanitize

        service_id = service["service_id"]
        if service_id not in SERVICES:
            return
        now = datetime.now(UTC)
        try:
            raw = self._compose(
                "logs",
                "--no-color",
                "--timestamps",
                "--since",
                "15m",
                "--tail",
                "100",
                service_id,
                timeout=20,
            )
            for kind in SECRET_KINDS:
                secret = self.environ.get(kind)
                if secret:
                    raw = raw.replace(secret, "[redacted]")
            content = sanitize(raw[-65536:])
            self._log_evidence[service_id] = {
                "id": service_id,
                "service_id": service_id,
                "observed_at": now.isoformat(),
                "content": content,
                "coverage": "last 100 lines within 15 minutes; bounded to 64 KiB",
            }
        except Exception:
            self._log_evidence[service_id] = {
                "id": service_id,
                "service_id": service_id,
                "observed_at": None,
                "content": "",
                "error": "host_logs_unavailable",
            }
        self.store.put_snapshot(
            "service_logs",
            self.scope,
            {
                "items": list(self._log_evidence.values()),
                "complete": False,
                "source": "allowlisted host Compose logs",
            },
            observed_at=now,
        )
        container = service.get("container_id")
        if not isinstance(container, str) or not re.fullmatch(r"[a-f0-9]{12,64}", container):
            return
        evidence = {"container_id": container, "runtime_observed_at": now.isoformat()}
        try:
            image = self._run(
                ["/usr/bin/docker", "inspect", "--format", "{{.Image}}", container], timeout=20
            ).strip()
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
                raise OperationRefused("actual image digest unavailable")
            evidence["image_digest"] = image
            if service.get("state") == "running":
                source = self._compose(
                    "exec",
                    "-T",
                    service_id,
                    "/app/.venv/bin/python",
                    "-c",
                    SOURCE_PROBE,
                    timeout=20,
                ).strip()
                if re.fullmatch(r"[a-f0-9]{64}", source):
                    evidence["source_digest"] = source
        except Exception:
            evidence["runtime_evidence_error"] = "image_or_source_observation_unavailable"
        self._service_evidence[service_id] = evidence

    def _running(self) -> set[str]:
        observation = self.observe()
        if observation["unreviewed_running_services"]:
            raise OperationRefused("unreviewed running services prohibit a quiescence proof")
        return {
            r["service_id"]
            for r in observation["items"]
            if r["state"] in ("running", "restarting", "paused")
        }

    def _quiescent(self, job: dict[str, Any], *, all_apps: bool = False) -> None:
        running = self._running()
        prohibited = (running - {"postgres"}) if all_apps else (running & MUTATION_CONSUMERS)
        if prohibited:
            raise OperationRefused("mutation consumers must remain stopped through proof/apply")
        active = self.store.one(
            "SELECT EXISTS(SELECT 1 FROM console_jobs WHERE id<>%s AND lease_until>now() "
            "AND state='running') OR EXISTS(SELECT 1 FROM dispatches WHERE "
            "lease_until>now()) OR EXISTS(SELECT 1 FROM source_management_updates WHERE "
            "state='claimed' AND claimed_at>now()-interval '5 minutes') AS active",
            (job["id"],),
        )
        if active is None or active["active"]:
            raise OperationRefused("active worker leases prohibit maintenance")

    def _flat(self) -> dict[str, Any]:
        from fatty_trader.exchanges.bitget.client import BitgetRestClient
        from fatty_trader.storage.bitget_baseline import collect_flat_evidence

        async def collect() -> dict[str, Any]:
            client = BitgetRestClient(
                self.environ["BITGET_API_KEY"],
                self.environ["BITGET_API_SECRET"],
                self.environ["BITGET_API_PASSPHRASE"],
                mode="LIVE",
                max_get_retries=0,
            )
            try:
                return await collect_flat_evidence(client, self.scope["account_id"], "LIVE")
            finally:
                await client.aclose()

        return asyncio.run(collect())

    def _policy(self) -> None:
        if any(
            self.environ.get(k) != v
            for k, v in {
                "TRADER_MODE": "LIVE",
                "BITGET_MODE": "LIVE",
                "BITGET_EXECUTION_ENABLED": "1",
            }.items()
        ):
            raise OperationRefused("host policy must remain LIVE/LIVE/1")
        self._run(["/bin/bash", "scripts/check_production_live_policy.sh"])

    def _write_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        if path.parent != self.state_dir or path.is_symlink():
            raise OperationRefused("host target is outside the dedicated state directory")
        temporary = self.state_dir / ("." + str(uuid4()) + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(json.dumps(payload, sort_keys=True).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if (
                os.name == "posix"
            ):  # directories cannot be opened on Windows; the host worker is Linux
                directory_fd = os.open(self.state_dir, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _claim(self, job: dict[str, Any]) -> bool:
        journal = self.state_dir / (str(UUID(job["id"])) + ".receipt.json")
        if journal.exists() or self.store.one(
            "SELECT job_id FROM console_operation_receipts WHERE job_id=%s", (job["id"],)
        ):
            return False
        # Persist filesystem claim first: database restore cannot erase this fence.
        self._write_atomic(
            journal,
            {
                "job_id": job["id"],
                "action": job["action"],
                "state": "claimed",
                "arguments_digest": digest(job["arguments"]),
                "scope": job["scope"],
            },
        )
        with self.store.connection() as conn:
            cur = self.store.cursor(conn)
            current = self.store.effect_authority(cur, job)
            if current.get("cancellation_requested_at"):
                raise OperationRefused("operation cancelled before host claim")
            cur.execute(
                "UPDATE console_jobs SET effect_claimed=true WHERE id=%s AND "
                "lease_token=%s AND state='running' AND lease_until>now()",
                (job["id"], job["lease_token"]),
            )
            if cur.rowcount != 1:
                raise OperationRefused("host job lease lost before claim")
            cur.execute(
                "INSERT INTO console_operation_receipts(job_id,action,state,evidence) "
                "VALUES (%s,%s,'claimed',%s::jsonb)",
                (
                    job["id"],
                    job["action"],
                    json.dumps({"arguments_digest": digest(job["arguments"])}),
                ),
            )
        return True

    def _finish(self, job: dict[str, Any], state: str, result: dict[str, Any]) -> dict[str, Any]:
        self._write_atomic(
            self.state_dir / (str(UUID(job["id"])) + ".receipt.json"),
            {
                "job_id": job["id"],
                "action": job["action"],
                "state": state,
                "arguments_digest": digest(job["arguments"]),
                "result": result,
            },
        )
        self.store.execute(
            "UPDATE console_operation_receipts SET "
            "state=%s,evidence=%s::jsonb,updated_at=now() WHERE job_id=%s",
            (state, json.dumps(result, default=str), job["id"]),
        )
        return {"state": state, "result": result}

    def execute(self, job: dict[str, Any], resource: dict[str, Any]) -> dict[str, Any]:
        validate_arguments(job["action"], job["arguments"])
        if job["action"] not in HOST_ACTIONS or job["scope"] != self.scope:
            raise OperationRefused("host action/scope mismatch")
        self._effect_guard(job)
        with self._lock():
            journal = self.state_dir / (str(UUID(job["id"])) + ".receipt.json")
            if journal.exists():
                return self.reconcile(job)
            self._policy()
            action, args = job["action"], job["arguments"]
            flat = None
            if action in (
                "backup.restore",
                "latch.prove",
                "latch.release_approved",
                "config.apply",
            ) or action.startswith(("release.", "baseline.")):
                self._quiescent(job, all_apps=action == "backup.restore")
            if action not in (
                "backup.create",
                "backup.verify",
                "backup.delete",
                "latch.prove",
                "latch.release_approved",
                "notification.configure_schedule",
            ):
                # Broad disruptive maintenance cannot sacrifice protection when nonflat.
                flat = self._flat()
            if action in ("backup.verify", "backup.restore", "backup.delete"):
                self._verify_backup(resource)
            if action == "backup.restore" and self._safety_digest() != resource["safety_digest"]:
                raise OperationRefused(
                    "ledger changed after backup; restore would replay or erase durable evidence"
                )
            if not self._claim(job):
                return self.reconcile(job)
            try:
                if action.startswith("service."):
                    verb = action.split(".")[1]
                    self._effect_guard(job)
                    from fatty_trader.storage.bitget_baseline import _fresh

                    assert flat is not None
                    _fresh(flat)
                    self._compose(verb, *args["services"])
                    result = {
                        "services": self.observe()["items"],
                        "flat_proof_digest": digest(flat),
                        "requested": args["services"],
                    }
                    states = {r["service_id"]: r["state"] for r in result["services"]}
                    expected = "exited" if verb == "stop" else "running"
                    if any(states.get(s) != expected for s in args["services"]):
                        return self._finish(job, "reconciliation_pending", result)
                elif action.startswith("release."):
                    result = self._release(job, resource)
                elif action == "config.apply":
                    result = self._config(job, resource)
                elif action == "backup.create":
                    result = self._backup_create(job)
                elif action == "backup.verify":
                    self.store.execute(
                        "UPDATE console_backups SET state='verified',verified_at=now() WHERE id=%s",
                        (resource["id"],),
                    )
                    result = {
                        "backup_id": resource["id"],
                        "sha256": resource["sha256"],
                        "integrity": "pg_restore --list and SHA256 verified",
                    }
                elif action == "backup.delete":
                    if resource.get("metadata", {}).get("held"):
                        raise OperationRefused("held backup cannot be deleted")
                    retention = resource.get("metadata", {}).get("retention_until")
                    if retention and datetime.fromisoformat(retention) > datetime.now(UTC):
                        raise OperationRefused("backup is inside its approved retention period")
                    self._effect_guard(job)
                    self._backup_path(resource).unlink()
                    self.store.execute(
                        "UPDATE console_backups SET state='deleted' WHERE id=%s", (resource["id"],)
                    )
                    result = {
                        "backup_id": resource["id"],
                        "deleted": True,
                        "catalog_preserved": True,
                    }
                elif action == "backup.restore":
                    self._quiescent(job, all_apps=True)
                    control_dump = self._preserve_console(job)
                    self._effect_guard(job)
                    self._quiescent(job, all_apps=True)
                    from fatty_trader.storage.bitget_baseline import _fresh

                    assert flat is not None
                    _fresh(flat)
                    self._run(
                        [
                            "/bin/bash",
                            "scripts/restore_postgres.sh",
                            str(self._backup_path(resource)),
                        ],
                        extra={"COMPOSE_BIN": "/usr/bin/docker compose", "CONFIRM_RESTORE": "YES"},
                        timeout=1800,
                    )
                    self._compose(
                        "exec",
                        "-T",
                        "postgres",
                        "pg_restore",
                        "--clean",
                        "--if-exists",
                        "--no-owner",
                        "--username=fatty_app",
                        "--dbname=fatty_trader",
                        input_file=control_dump,
                        timeout=300,
                    )
                    if self._safety_digest() != resource["safety_digest"]:
                        raise OperationRefused(
                            "restored durable evidence did not match verified backup"
                        )
                    control_dump.unlink()
                    # Restore does not start any consumer. Admission remains explicitly paused.
                    self.store.execute(
                        "INSERT INTO "
                        "console_admission_pauses(scope,active,reason,updated_at) VALUES "
                        "(%s,true,'console restore: reconciliation required',now()) ON "
                        "CONFLICT(scope) DO UPDATE SET "
                        "active=true,reason=EXCLUDED.reason,updated_at=now()",
                        (f"bitget:{self.scope['account_id']}:LIVE",),
                    )
                    result = {
                        "backup_id": resource["id"],
                        "restored": True,
                        "consumers_started": False,
                        "admission_paused": True,
                        "reconciliation_required": True,
                    }
                elif action.startswith("latch."):
                    result = self._recovery(job)
                elif action.startswith("baseline."):
                    result = self._baseline(job)
                elif action == "notification.configure_schedule":
                    result = self._schedule(job)
                else:
                    raise OperationRefused("host operation not allowlisted")
                return self._finish(job, "succeeded", result)
            except Exception:
                # A command might have crossed its effect boundary. Never call it again.
                return self._finish(
                    job,
                    "reconciliation_pending",
                    {
                        "reason": (
                            "host effect claimed; terminal outcome requires GET-only reobservation"
                        )
                    },
                )

    def observe_schedule(self) -> dict[str, Any]:
        raw = self._run(
            [
                "/usr/bin/systemctl",
                "--user",
                "show",
                "fatty-health-report.timer",
                "--property=LoadState,ActiveState,TimersMonotonic,NextElapseUSecRealtime,"
                "NextElapseUSecMonotonic,LastTriggerUSec",
            ]
        )
        values = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        if values.get("LoadState") != "loaded":
            raise OperationRefused("fixed health report timer is not installed")
        schedule_file = self.state_dir / "notification-schedule.json"
        desired = json.loads(schedule_file.read_text()) if schedule_file.exists() else {}
        result = {
            "unit": "fatty-health-report.timer",
            "fields": values,
            "desired_interval_hours": desired.get("interval_hours"),
            "source": "systemctl user show GET",
        }
        self.store.put_snapshot(
            "notification_schedule", self.scope, result, observed_at=datetime.now(UTC)
        )
        return result

    def _schedule(self, job: dict[str, Any]) -> dict[str, Any]:
        hours = job["arguments"]["interval_hours"]
        self.observe_schedule()
        self._effect_guard(job)
        directory = Path(self.environ["HOME"]) / ".config/systemd/user/fatty-health-report.timer.d"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink():
            raise OperationRefused("timer override must be the fixed owned directory")
        temporary = directory / ("." + str(uuid4()) + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(f"[Timer]\nOnUnitActiveSec=\nOnUnitActiveSec={hours}h\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / "console.conf")
        self._write_atomic(
            self.state_dir / "notification-schedule.json",
            {"interval_hours": hours, "job_id": job["id"]},
        )
        self._effect_guard(job)
        self._run(["/usr/bin/systemctl", "--user", "daemon-reload"])
        self._effect_guard(job)
        self._run(["/usr/bin/systemctl", "--user", "restart", "fatty-health-report.timer"])
        result = self.observe_schedule()
        # systemd readback must contain the actual requested monotonic interval.
        interval = re.search(
            r"OnUnitActiveUSec=\s*([^;}]*)", result["fields"].get("TimersMonotonic", "")
        )
        units = {"us": 0.000001, "ms": 0.001, "s": 1, "min": 60, "h": 3600, "d": 86400}
        duration = interval.group(1).strip() if interval else ""
        parts = list(re.finditer(r"([0-9]+(?:\.[0-9]+)?)(us|ms|min|s|h|d)", duration))
        seconds = sum(float(part.group(1)) * units[part.group(2)] for part in parts)
        if (
            not parts
            or re.sub(r"([0-9]+(?:\.[0-9]+)?)(us|ms|min|s|h|d)", "", duration).strip()
            or seconds != hours * 3600
            or result["fields"].get("ActiveState") != "active"
        ):
            raise OperationRefused("health timer effective interval readback failed")
        return {**result, "effective_interval_hours": hours}

    def _config(self, job: dict[str, Any], resource: dict[str, Any]) -> dict[str, Any]:
        payload = resource["payload"]
        settings = payload.get("settings", {})
        from jsonschema import Draft202012Validator

        from fatty_trader.console.operations import object_schema

        if list(Draft202012Validator(object_schema(CONFIG_FIELDS)).iter_errors(settings)):
            raise OperationRefused("draft schema no longer matches fixed settings")
        latest = self.store.one(
            "SELECT max(version) AS version FROM console_config_versions WHERE kind='runtime'"
        )
        if (
            latest is None
            or resource["state"] != "draft"
            or resource["version"] != latest["version"]
        ):
            raise OperationRefused("draft is not the current immutable revision")
        overrides = (
            json.loads(self.override.read_text()) if self.override.exists() else {"services": {}}
        )
        stages = None
        replacements = []
        if payload.get("secret_stage_ids"):
            stages = SecretStages(self.environ)
            for stage_id in payload["secret_stage_ids"]:
                replacement = stages.read(stage_id, resource["actor_id"], self.scope["account_id"])
                if replacement["kind"] in settings:
                    raise OperationRefused("duplicate replacement kind")
                settings = {**settings, replacement["kind"]: replacement["value"]}
                replacements.append(stage_id)
        for key, value in settings.items():
            if key not in CONFIG_FIELDS and key not in SECRET_KINDS:
                raise OperationRefused("setting is policy-pinned or not allowlisted")
            encoded = ",".join(str(x) for x in value) if isinstance(value, list) else str(value)
            encoded = encoded.replace("$", "$$")
            for service in SETTINGS_CONSUMERS[key]:
                overrides["services"].setdefault(service, {}).setdefault("environment", {})[key] = (
                    encoded
                )
        self._effect_guard(job)
        self._quiescent(job)
        self._flat()
        self._write_atomic(self.override, overrides)
        if self.override not in self.compose_files:
            self.compose_files.append(self.override)
        self._policy()
        self.store.execute(
            "UPDATE console_config_versions SET state='desired',applied_at=now() WHERE "
            "id=%s AND state='draft'",
            (resource["id"],),
        )
        for key in SECRET_KINDS:
            if key in settings:
                self.environ[key] = settings[key]
        for stage_id in replacements:
            if stages is not None:
                stages.consume(stage_id)
        affected = sorted({s for key in settings for s in SETTINGS_CONSUMERS[key]})
        self._effect_guard(job)
        self._compose(
            "up", "-d", "--no-build", "--no-deps", "--force-recreate", *affected, timeout=900
        )
        self._wait_services(affected)
        effective = self._effective_config(affected, settings, resource["version"])
        if not effective["matches_desired"]:
            raise OperationRefused("consumer config readback differs from desired revision")
        self.store.execute(
            "UPDATE console_config_versions SET state='effective' WHERE id=%s AND state='desired'",
            (resource["id"],),
        )
        result = {
            "draft_id": resource["id"],
            "config_version": resource["version"],
            "state": "effective",
            "effective": True,
            "consumers": affected,
            "changed_fields": sorted(settings),
            "secret_values": "write-only",
        }
        self.store.put_snapshot("config", self.scope, result, observed_at=datetime.now(UTC))
        return result

    def _wait_services(self, services: list[str]) -> dict[str, dict[str, Any]]:
        deadline = time.monotonic() + 90
        while True:
            states = {row["service_id"]: row for row in self.observe()["items"]}
            if all(
                states.get(service, {}).get("state") == "running"
                and states.get(service, {}).get("health") == "healthy"
                for service in services
            ):
                return states
            if time.monotonic() >= deadline:
                raise OperationRefused(
                    "bounded consumer running/health proof failed; no automatic replay"
                )
            time.sleep(2)

    def _effective_config(
        self, services: list[str], settings: dict[str, Any], version: int
    ) -> dict[str, Any]:
        items = []
        matches = True
        for service in services:
            container = self._compose("ps", "-q", service).strip()
            if not re.fullmatch(r"[a-f0-9]{12,64}", container):
                raise OperationRefused("exact consumer container identity unavailable")
            raw = self._run(
                ["/usr/bin/docker", "inspect", "--format", "{{json .Config.Env}}", container]
            )
            observed = dict(item.split("=", 1) for item in json.loads(raw) if "=" in item)
            values = {}
            for key, desired in settings.items():
                if service not in SETTINGS_CONSUMERS[key]:
                    continue
                expected = (
                    ",".join(str(x) for x in desired) if isinstance(desired, list) else str(desired)
                )
                agrees = observed.get(key) == expected
                matches = matches and agrees
                values[key] = {
                    "value": "write-only" if key in SECRET_KINDS else observed.get(key),
                    "matches_desired": agrees,
                }
            if service in BITGET_SERVICES and any(
                observed.get(k) != v
                for k, v in {
                    "TRADER_MODE": "LIVE",
                    "BITGET_MODE": "LIVE",
                    "BITGET_EXECUTION_ENABLED": "1",
                }.items()
            ):
                raise OperationRefused("effective LIVE policy changed")
            items.append(
                {
                    "service_id": service,
                    "config_version": version,
                    "fields": values,
                    "container_id": container,
                }
            )
        result = {
            "items": items,
            "config_version": version,
            "matches_desired": matches,
            "source": "host exact-container inspect GET",
        }
        self.store.put_snapshot(
            "host_effective_config", self.scope, result, observed_at=datetime.now(UTC)
        )
        return result

    def _preserve_console(self, job: dict[str, Any]) -> Path:
        # Fixed table pattern, never supplied by a client; control-plane claims
        # must survive financial restore to prevent lost approvals/POST fences.
        dump = self.state_dir / (str(UUID(job["id"])) + ".control.dump")
        raw = self._compose(
            "exec",
            "-T",
            "postgres",
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--table=public.console_*",
            "--username=fatty_app",
            "--dbname=fatty_trader",
            timeout=300,
            binary=True,
        )
        fd = os.open(dump, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return dump

    def register_release(
        self, release_id: str, image: str, approval_reference: str
    ) -> dict[str, Any]:
        """Trusted SSH administrator input only; not reachable from browser jobs."""
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", release_id)
            or not approval_reference.strip()
        ):
            raise OperationRefused("release identifier and approval reference required")
        if not re.fullmatch(r"(?:[a-zA-Z0-9._/-]+@)?sha256:[a-f0-9]{64}", image):
            raise OperationRefused("image must be an immutable digest, never a tag or branch")
        self._policy()
        if not image.startswith("sha256:"):
            self._run(["/usr/bin/docker", "pull", image], timeout=900)
        inspected = json.loads(self._run(["/usr/bin/docker", "image", "inspect", image]))
        if len(inspected) != 1:
            raise OperationRefused("exact image identity unavailable")
        identity = inspected[0]["Id"] if image.startswith("sha256:") else image.split("@", 1)[1]
        if image.startswith("sha256:") and identity != image:
            raise OperationRefused("local image digest differs")
        source = self._run(
            [
                "/usr/bin/docker",
                "run",
                "--rm",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--entrypoint=/app/.venv/bin/python",
                image,
                "-c",
                SOURCE_PROBE,
            ]
        ).strip()
        if not re.fullmatch(r"[a-f0-9]{64}", source):
            raise OperationRefused("approved image source tree digest unavailable")
        schema = self.store.one("SELECT max(version) AS version FROM schema_migrations")
        if schema is None:
            raise OperationRefused("schema evidence unavailable")
        manifest = {
            "image": image,
            "compose_digest": hashlib.sha256(
                (self.root / "docker-compose.yml").read_bytes()
            ).hexdigest(),
            "schema_version": schema["version"],
            "approval_reference": approval_reference,
        }
        self.store.execute(
            "INSERT INTO "
            "console_artifacts(id,kind,image_digest,source_digest,manifest,approved,accou"
            "nt_id,environment) VALUES (%s,'release',%s,%s,%s::jsonb,true,%s,'LIVE')",
            (release_id, identity, source, json.dumps(manifest), self.scope["account_id"]),
        )
        return {
            "release_id": release_id,
            "image_digest": identity,
            "source_digest": source,
            "schema_version": schema["version"],
            "approved": True,
        }

    def _release(self, job: dict[str, Any], resource: dict[str, Any]) -> dict[str, Any]:
        manifest = resource.get("manifest") or {}
        image = manifest.get("image")
        if (
            not isinstance(image, str)
            or not re.fullmatch(r"(?:[a-zA-Z0-9._/-]+@)?sha256:[a-f0-9]{64}", image)
            or image.rsplit(":", 1)[1] != resource["image_digest"].removeprefix("sha256:")
        ):
            raise OperationRefused("release must name an approved immutable OCI image digest")
        compose_digest = hashlib.sha256((self.root / "docker-compose.yml").read_bytes()).hexdigest()
        if manifest.get("compose_digest") != compose_digest:
            raise OperationRefused("artifact Compose compatibility proof changed")
        schema = self.store.one("SELECT max(version) AS version FROM schema_migrations")
        if schema is None or manifest.get("schema_version") != schema["version"]:
            raise OperationRefused("release schema mismatch; no unsafe down-migration")
        previous = (
            json.loads(self.release_override.read_text())
            if self.release_override.exists()
            else {"services": {}}
        )
        for service in job["arguments"]["services"]:
            previous["services"].setdefault(service, {})["image"] = image
        self._effect_guard(job)
        self._quiescent(job)
        self._flat()
        self._write_atomic(self.release_override, previous)
        if self.release_override not in self.compose_files:
            self.compose_files.append(self.release_override)
        self._policy()
        services = job["arguments"]["services"]
        if not image.startswith("sha256:"):
            self._compose("pull", *services, timeout=900)
        self._effect_guard(job)
        self._quiescent(job)
        self._flat()
        self._compose(
            "up", "-d", "--no-build", "--no-deps", "--force-recreate", *services, timeout=900
        )
        self._policy()
        states = self._wait_services(services)
        for service in services:
            row = states.get(service)
            if not row or row["state"] != "running":
                raise OperationRefused("release service state not observed running")
            container = self._compose("ps", "-q", service).strip()
            if not re.fullmatch(r"[a-f0-9]{12,64}", container):
                raise OperationRefused("release exact container identity unavailable")
            expected_image = self._run(
                ["/usr/bin/docker", "image", "inspect", "--format", "{{.Id}}", image]
            ).strip()
            actual_image = self._run(
                ["/usr/bin/docker", "inspect", "--format", "{{.Image}}", container]
            ).strip()
            if (
                not re.fullmatch(r"sha256:[a-f0-9]{64}", expected_image)
                or actual_image != expected_image
            ):
                raise OperationRefused("release actual container image digest mismatch")
            source = self._compose(
                "exec", "-T", service, "/app/.venv/bin/python", "-c", SOURCE_PROBE
            ).strip()
            if source != resource["source_digest"]:
                raise OperationRefused("release copied-source digest mismatch")
        self._effective_config([s for s in services if s in BITGET_SERVICES], {}, 0)
        return {
            "release_id": resource["id"],
            "image_digest": resource["image_digest"],
            "source_digest": resource["source_digest"],
            "services": self.observe()["items"],
            "schema_version": schema["version"],
            "rollback_semantics": "approved immutable artifact only; no database rollback",
        }

    def _effect_guard(self, job: dict[str, Any]) -> None:
        with self.store.connection() as connection:
            current = self.store.effect_authority(self.store.cursor(connection), job)
            if current.get("cancellation_requested_at"):
                raise OperationRefused("operation was cancelled before a new effect")

    def _baseline(self, job: dict[str, Any]) -> dict[str, Any]:
        args = job["arguments"]
        self._quiescent(job)
        self._effect_guard(job)
        argv = [
            sys.executable,
            "scripts/audit_bitget_baseline.py",
            "--approval-reference",
            args["approval_reference"],
            "--account-id",
            self.scope["account_id"],
            "--environment",
            "LIVE",
            "--historical-before",
            args["historical_before"],
        ]
        if job["action"] == "baseline.apply_retire":
            argv.extend(["--apply", "--expected-digest", args["expected_digest"]])
        else:
            argv.append("--dry-run")
        result = json.loads(self._run(argv, timeout=120))
        self._quiescent(job)
        if not result.get("candidate_digest") or (
            job["action"] == "baseline.apply_retire" and not result.get("applied")
        ):
            raise OperationRefused("baseline script did not prove the scoped approved outcome")
        return dict(result)

    def _safety_digest(self) -> str:
        tables = (
            "live_order_intents",
            "bitget_margin_reservations",
            "positions",
            "orders",
            "venue_kill_switches",
            "source_management_updates",
            "source_management_provider_intents",
            "fills",
            "dispatches",
            "dispatch_transitions",
            "telegram_messages",
            "canonical_signals",
            "bitget_audited_baselines",
            "bitget_baseline_records",
            "fallback_protection",
            "bitget_verified_close_bindings",
            "bitget_post_fill_reconciliations",
            "provider_reconciliation_events",
            "intent_reconciliation_audit",
            "protection_states",
        )
        # Full typed domain rows: no filtered omission of existing provider claims.
        with self.store.connection() as connection:
            cursor = self.store.cursor(connection)
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            rows = {}
            for table in tables:
                cursor.execute(f"SELECT * FROM {table} ORDER BY 1")
                rows[table] = list(cursor.fetchall())
            return digest(rows)

    def _backup_create(self, job: dict[str, Any]) -> dict[str, Any]:
        before = self._safety_digest()
        raw = self._run(
            ["/bin/bash", "scripts/backup_postgres.sh"],
            extra={
                "BACKUP_DIR": str(self.backup_dir),
                "BACKUP_RETAIN_DAYS": "0",
                "COMPOSE_BIN": "/usr/bin/docker compose",
            },
            timeout=1800,
        )
        match = re.search(r"^backup_created=(.+) bytes=([0-9]+)$", raw, re.M)
        if not match:
            raise OperationRefused("backup script did not provide integrity evidence")
        path = Path(match[1]).resolve(strict=True)
        if path.parent != self.backup_dir or not re.fullmatch(
            r"fatty_trader_[0-9]{8}T[0-9]{6}Z.dump", path.name
        ):
            raise OperationRefused("backup output is outside the dedicated catalog")
        os.chmod(path, 0o600)
        after = self._safety_digest()
        backup_id = str(uuid4())
        sha = self._file_digest(path)
        metadata = {
            "job_id": str(job["id"]),
            "safety_consistent": before == after,
            "retention_until": datetime.fromtimestamp(time.time() + 14 * 86400, UTC).isoformat(),
            "held": False,
        }
        self.store.execute(
            "INSERT INTO "
            "console_backups(id,filename,sha256,size_bytes,state,verified_at,safety_diges"
            "t,metadata,account_id,environment) VALUES "
            "(%s,%s,%s,%s,'verified',now(),%s,%s::jsonb,%s,'LIVE')",
            (
                backup_id,
                path.name,
                sha,
                path.stat().st_size,
                after if before == after else None,
                json.dumps(metadata),
                self.scope["account_id"],
            ),
        )
        return {
            "backup_id": backup_id,
            "sha256": sha,
            "size_bytes": path.stat().st_size,
            "integrity": "pg_restore --list verified",
            "restore_safety_consistent": before == after,
        }

    def _file_digest(self, path: Path) -> str:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def _backup_path(self, resource: dict[str, Any]) -> Path:
        filename = resource["filename"]
        if not isinstance(filename, str) or not re.fullmatch(
            r"fatty_trader_[0-9]{8}T[0-9]{6}Z.dump", filename
        ):
            raise OperationRefused("invalid backup catalog filename")
        path = self.backup_dir / filename
        if path.is_symlink() or path.resolve(strict=True).parent != self.backup_dir:
            raise OperationRefused("invalid backup catalog target")
        return path

    def _verify_backup(self, resource: dict[str, Any]) -> None:
        path = self._backup_path(resource)
        if (
            path.stat().st_size != resource["size_bytes"]
            or self._file_digest(path) != resource["sha256"]
        ):
            raise OperationRefused("backup content integrity mismatch")
        self._compose("exec", "-T", "postgres", "pg_restore", "--list", input_file=path)

    def _recovery(self, job: dict[str, Any]) -> dict[str, Any]:
        args = job["arguments"]
        self._quiescent(job)
        self._effect_guard(job)
        argv = [
            sys.executable,
            "scripts/recover_bitget_live_latches.py",
            "--confirm",
            "--approval-reference",
            args["approval_reference"],
            "--account-id",
            self.scope["account_id"],
            "--baseline-id",
            args["baseline_id"],
            "--clock-latch-reason",
            args.get("clock_latch_reason", "clock-skew-exceeded"),
        ]
        if job["action"] == "latch.release_approved":
            argv.extend(["--apply", "--expected-digest", args["expected_digest"]])
        else:
            argv.append("--dry-run")
        result = json.loads(self._run(argv, timeout=120))
        self._quiescent(job)
        if not result.get("recovery_digest") or (
            job["action"] == "latch.release_approved" and not result.get("applied")
        ):
            raise OperationRefused("recovery script did not prove the approved outcome")
        return dict(result)

    def reconcile(self, job: dict[str, Any]) -> dict[str, Any]:
        # GET-only: never repeats start/stop/restart, restore, release, or recovery apply.
        journal = self.state_dir / (str(UUID(job["id"])) + ".receipt.json")
        evidence = json.loads(journal.read_text()) if journal.exists() else {}
        if evidence.get("arguments_digest") and evidence["arguments_digest"] != digest(
            job["arguments"]
        ):
            raise OperationRefused("receipt binding mismatch")
        if evidence.get("state") in ("succeeded", "failed"):
            return {"state": evidence["state"], "result": evidence.get("result", {})}
        observations: dict[str, Any] = {
            "host_services": self.observe(),
            "effect_replayed": False,
        }
        action, args = job["action"], job["arguments"]
        if action.startswith("service."):
            states = {r["service_id"]: r["state"] for r in observations["host_services"]["items"]}
            if action == "service.stop" and all(
                states.get(s) == "exited" for s in args["services"]
            ):
                return self._finish(
                    job,
                    "succeeded",
                    {
                        **observations,
                        "requested_state_observed": True,
                        "causal_attribution": "ambiguous claim, current state only",
                    },
                )
            # Running is not proof that a restart happened: do not invent success.
        elif action == "backup.create":
            row = self.store.one(
                "SELECT id,sha256,size_bytes FROM console_backups WHERE metadata->>'job_id'=%s",
                (str(job["id"]),),
            )
            if row:
                return self._finish(job, "succeeded", {**observations, **row})
        elif action == "backup.delete":
            row = self.store.one(
                "SELECT state FROM console_backups WHERE id=%s", (args["backup_id"],)
            )
            observations["backup_catalog_state"] = row["state"] if row else "unknown"
        elif action == "config.apply":
            row = self.store.one(
                "SELECT state,version FROM console_config_versions WHERE id=%s", (args["draft_id"],)
            )
            observations["config"] = row
        elif action.startswith("latch."):
            observations["latches"] = self.store.query(
                "SELECT scope,active,reason,updated_at FROM venue_kill_switches ORDER BY scope"
            )
        return {
            "state": "reconciliation_pending",
            "result": {
                **observations,
                "reason": "host effect was already claimed; no dangerous operation replay",
            },
        }
