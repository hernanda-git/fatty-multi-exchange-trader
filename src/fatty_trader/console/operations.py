"""Typed administrative operations; secrets never enter the command database."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from fatty_trader.console.store import ConsoleStore

if TYPE_CHECKING:
    from fatty_trader.console.runner import HostOperationsRunner

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def object_schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


TEXT = {"type": "string", "minLength": 1, "maxLength": 200}
ID = {"type": "string", "format": "uuid"}
DIGEST = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
SERVICES = (
    "web",
    "intake",
    "analyzer",
    "dispatcher-bitget",
    "monitor-bitget",
    "operator-bot",
    "source-management",
    "notification-sender",
    "console-worker",
)
SERVICE_SET = {
    "type": "array",
    "items": {"type": "string", "enum": list(SERVICES)},
    "minItems": 1,
    "uniqueItems": True,
}
CONFIG_FIELDS: dict[str, Any] = {
    "BITGET_DISPATCH_POLL_SECONDS": {"type": "integer", "minimum": 1, "maximum": 300},
    "BITGET_MONITOR_POLL_SECONDS": {"type": "integer", "minimum": 1, "maximum": 30},
    "NOTIFICATION_RETRY_SECONDS": {"type": "integer", "minimum": 10, "maximum": 300},
    "NOTIFICATION_MAX_ATTEMPTS": {"type": "integer", "minimum": 1, "maximum": 20},
    "ANALYZER_CHANNEL_IDS": {
        "type": "array",
        "items": {"type": "integer"},
        "minItems": 1,
        "maxItems": 100,
        "uniqueItems": True,
    },
    "TELEGRAM_SOURCE_CHANNELS": {
        "type": "array",
        "items": {
            "oneOf": [
                {"type": "integer", "maximum": -1},
                {"type": "string", "pattern": "^@[A-Za-z][A-Za-z0-9_]{4,31}$"},
            ]
        },
        "minItems": 1,
        "maxItems": 100,
        "uniqueItems": True,
    },
    "BITGET_MARGIN_MODE_TYPE": {
        "type": "string",
        "enum": ["PERCENTAGE", "FIXED_USDT"],
    },
    "BITGET_ALLOCATION_PCT": {
        "type": "number",
        "minimum": 0.01,
        "maximum": 1.0,
    },
    "BITGET_FIXED_MARGIN_USDT": {
        "type": "number",
        "exclusiveMinimum": 0,
        "maximum": 1.0,
    },
    "BITGET_DEFAULT_LEVERAGE": {
        "type": "integer",
        "minimum": 5,
        "maximum": 20,
    },
}
SECRET_KINDS = ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE", "TG_BOT_TOKEN")
ACTION_SCHEMAS = {
    "config.save": object_schema(
        {
            "base_version": {"type": "integer", "minimum": 0},
            "settings": object_schema(CONFIG_FIELDS),
        },
        ("base_version", "settings"),
    ),
    "config.save_draft": object_schema(
        {
            "base_version": {"type": "integer", "minimum": 0},
            "settings": object_schema(CONFIG_FIELDS),
            "secret_stage_ids": {"type": "array", "items": ID, "maxItems": 4, "uniqueItems": True},
        },
        ("base_version", "settings"),
    ),
    "config.apply": object_schema({"draft_id": ID}, ("draft_id",)),
    "source.configure": object_schema(
        {
            "base_version": {"type": "integer", "minimum": 0},
            "live_channel_ids": CONFIG_FIELDS["ANALYZER_CHANNEL_IDS"],
            "intake_channels": CONFIG_FIELDS["TELEGRAM_SOURCE_CHANNELS"],
        },
        ("base_version", "live_channel_ids"),
    ),
    "notification.test": object_schema(
        {"content_kind": {"type": "string", "enum": ["console_test"]}}, ("content_kind",)
    ),
    "notification.send_health": object_schema({}),
    "notification.configure_schedule": object_schema(
        {"interval_hours": {"type": "integer", "enum": [1, 2, 3, 6, 12, 24]}}, ("interval_hours",)
    ),
    "notification.requeue": object_schema(
        {"notification_id": ID, "duplicate_risk_accepted": {"type": "boolean", "const": True}},
        ("notification_id", "duplicate_risk_accepted"),
    ),
    "signal.reanalyze": object_schema(
        {"message_id": ID, "revision": DIGEST, "analysis_only": {"type": "boolean", "const": True}},
        ("message_id", "revision", "analysis_only"),
    ),
    "signal.annotate": object_schema(
        {"message_id": ID, "revision": DIGEST, "note": TEXT},
        ("message_id", "revision", "note"),
    ),
    "incident.acknowledge": object_schema(
        {
            "latch_scope": {
                "type": "string",
                "enum": ["global", "bitget", "bitget-protection-stream"],
            },
            "expected_reason": TEXT,
        },
        ("latch_scope", "expected_reason"),
    ),
    "report.export": object_schema(
        {
            "report_kind": {
                "type": "string",
                "enum": ["signals", "jobs", "audit", "notifications"],
            },
            "format": {"type": "string", "enum": ["json", "csv"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
        ("report_kind", "format"),
    ),
    "admission.pause": object_schema({}, ()),
    "admission.resume": object_schema({}, ()),
    "safety.contain": object_schema(
        {"latch_scope": {"type": "string", "enum": ["bitget"]}}, ("latch_scope",)
    ),
    "baseline.preview_retire": object_schema(
        {
            "historical_before": {"type": "string", "format": "date-time"},
            "approval_reference": TEXT,
        },
        ("historical_before", "approval_reference"),
    ),
    "baseline.apply_retire": object_schema(
        {
            "historical_before": {"type": "string", "format": "date-time"},
            "approval_reference": TEXT,
            "expected_digest": DIGEST,
        },
        ("historical_before", "approval_reference", "expected_digest"),
    ),
    "latch.prove": object_schema(
        {
            "baseline_id": ID,
            "approval_reference": TEXT,
            "clock_latch_reason": {
                "type": "string",
                "enum": ["clock-skew-exceeded", "clock-skew-unavailable"],
            },
        },
        ("baseline_id", "approval_reference"),
    ),
    "latch.release_approved": object_schema(
        {
            "baseline_id": ID,
            "approval_reference": TEXT,
            "expected_digest": DIGEST,
            "clock_latch_reason": {
                "type": "string",
                "enum": ["clock-skew-exceeded", "clock-skew-unavailable"],
            },
        },
        ("baseline_id", "approval_reference", "expected_digest"),
    ),
    "maintenance.prepare": object_schema(
        {"services": SERVICE_SET, "no_replay": {"type": "boolean", "const": True}},
        ("services", "no_replay"),
    ),
    "backup.create": object_schema({}),
}
for name in ("service.start", "service.stop", "service.restart"):
    ACTION_SCHEMAS[name] = object_schema(
        {"services": SERVICE_SET, "maintenance_plan_id": ID}, ("services", "maintenance_plan_id")
    )
for name in ("release.deploy", "release.rollback"):
    ACTION_SCHEMAS[name] = object_schema(
        {"release_id": TEXT, "services": SERVICE_SET, "maintenance_plan_id": ID},
        ("release_id", "services", "maintenance_plan_id"),
    )
for name in ("backup.verify", "backup.restore", "backup.delete"):
    ACTION_SCHEMAS[name] = object_schema(
        {"backup_id": ID, "sha256": DIGEST, "maintenance_plan_id": ID},
        ("backup_id", "sha256", "maintenance_plan_id"),
    )
OPERATIONS_ACTIONS = frozenset(ACTION_SCHEMAS)
HOST_ACTIONS = frozenset(
    name
    for name in OPERATIONS_ACTIONS
    if name.startswith(("service.", "release.", "backup.", "latch.", "baseline."))
    or name in ("config.save", "config.apply", "notification.configure_schedule")
)
ACTION_POLICIES = {
    name: {
        "role": "owner"
        if name in HOST_ACTIONS
        or name in ("source.configure", "admission.resume", "maintenance.prepare")
        else "operator",
        "danger_tier": 3
        if name
        in (
            "backup.restore",
            "release.deploy",
            "release.rollback",
            "latch.release_approved",
            "baseline.apply_retire",
        )
        else 0
        if name in ("signal.reanalyze", "report.export")
        else 1
        if name
        in ("config.save_draft", "maintenance.prepare", "signal.annotate", "incident.acknowledge")
        else 2,
    }
    for name in OPERATIONS_ACTIONS
}


class OperationRefused(ValueError):
    pass


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def validate_arguments(action: str, arguments: dict[str, Any]) -> None:
    from jsonschema import Draft202012Validator

    if action not in ACTION_SCHEMAS:
        raise OperationRefused("action is not allowlisted")
    if list(
        Draft202012Validator(
            ACTION_SCHEMAS[action], format_checker=Draft202012Validator.FORMAT_CHECKER
        ).iter_errors(arguments)
    ):
        raise OperationRefused("operation arguments do not match the fixed schema")
    for key in (
        "draft_id",
        "message_id",
        "notification_id",
        "baseline_id",
        "backup_id",
        "maintenance_plan_id",
    ):
        if key in arguments:
            UUID(arguments[key])
    if (
        "settings" in arguments
        and not arguments["settings"]
        and not arguments.get("secret_stage_ids")
    ):
        raise OperationRefused("configuration has no changes")


def create_config_revision(cursor: Any, job: dict[str, Any]) -> dict[str, Any]:
    """Create one immutable revision while holding the shared configuration lock."""
    args = job["arguments"]
    cursor.execute("SELECT pg_advisory_xact_lock(626026)")
    cursor.execute(
        "SELECT coalesce(max(version),0) FROM console_config_versions WHERE kind='runtime'"
    )
    row = cursor.fetchone()
    current = int(next(iter(row.values())) if isinstance(row, dict) else row[0])
    if current != args["base_version"]:
        raise OperationRefused("configuration version changed; reload settings before saving")
    payload = {
        "settings": dict(
            args.get("settings", {"ANALYZER_CHANNEL_IDS": args.get("live_channel_ids")})
        ),
        "secret_stage_ids": args.get("secret_stage_ids", []),
    }
    if job["action"] == "source.configure" and args.get("intake_channels"):
        payload["settings"]["TELEGRAM_SOURCE_CHANNELS"] = args["intake_channels"]
    revision = {
        "id": str(uuid4()),
        "version": current + 1,
        "base_version": current,
        "payload": payload,
        "state": "draft",
        "actor_id": job["actor_id"],
        "account_id": job["scope"]["account_id"],
        "environment": "LIVE",
    }
    cursor.execute(
        "INSERT INTO console_config_versions"
        "(id,kind,version,base_version,payload,state,actor_id,account_id,environment) "
        "VALUES (%s,'runtime',%s,%s,%s::jsonb,'draft',%s,%s,'LIVE')",
        (
            revision["id"],
            current + 1,
            current,
            json.dumps(payload),
            job["actor_id"],
            job["scope"]["account_id"],
        ),
    )
    return revision


class SecretStages:
    """RSA-OAEP/Fernet envelope; web has public key only, runner has private key."""

    def __init__(self, environ: Mapping[str, str]) -> None:
        self.directory = Path(environ["CONSOLE_SECRET_STAGE_DIR"])
        self.shared_gid = (
            int(environ["CONSOLE_SECRET_STAGE_GID"])
            if environ.get("CONSOLE_SECRET_STAGE_GID")
            else None
        )
        protected = self.directory.stat()
        if self.directory.is_symlink() or (
            os.name != "nt"
            and (
                stat.S_IMODE(protected.st_mode) & 0o007
                or (self.shared_gid is None and stat.S_IMODE(protected.st_mode) & 0o077)
                or (
                    self.shared_gid is not None
                    and (
                        protected.st_gid != self.shared_gid or not protected.st_mode & stat.S_ISGID
                    )
                )
            )
        ):
            raise OperationRefused("secret spool must be dedicated 0700 or approved setgid 2770")
        public_file = Path(environ["CONSOLE_SECRET_PUBLIC_KEY_FILE"])
        if public_file.is_symlink():
            raise OperationRefused("secret public key must not be a symlink")
        public_key = serialization.load_pem_public_key(public_file.read_bytes())
        if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 3072:
            raise OperationRefused("secret public key must be RSA 3072 or stronger")
        self.public_key = public_key
        self.private_file = environ.get("CONSOLE_SECRET_PRIVATE_KEY_FILE")

    @contextmanager
    def _lock(self) -> Iterator[None]:
        if os.name == "nt":
            import msvcrt

            fd = os.open(self.directory / ".stages.lock", os.O_RDWR | os.O_CREAT, 0o600)
        else:
            import fcntl

            fd = os.open(self.directory, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            if os.name == "nt":
                if not os.fstat(fd).st_size:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                cast(Any, msvcrt).locking(fd, cast(Any, msvcrt).LK_LOCK, 1)
            else:
                cast(Any, fcntl).flock(fd, cast(Any, fcntl).LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    cast(Any, msvcrt).locking(fd, cast(Any, msvcrt).LK_UNLCK, 1)
                else:
                    cast(Any, fcntl).flock(fd, cast(Any, fcntl).LOCK_UN)
        finally:
            os.close(fd)

    def cleanup(self) -> None:
        with self._lock():
            self._inventory()

    def _inventory(self) -> list[dict[str, Any]]:
        entries = []
        now = time.time()
        for existing in self.directory.glob("*.stage"):
            info = existing.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
                raise OperationRefused("invalid secret spool entry")
            if now - info.st_mtime > 900:
                existing.unlink()
                continue
            try:
                metadata = json.loads(existing.read_bytes())
                if not isinstance(metadata, dict) or not isinstance(metadata.get("binding"), str):
                    raise ValueError
            except ValueError:
                raise OperationRefused("invalid secret spool entry") from None
            entries.append(metadata)
            if len(entries) > 256:
                raise OperationRefused("secret spool capacity reached")
        return entries

    def stage(self, kind: str, value: str, actor_id: str, account_id: str) -> dict[str, Any]:
        if (
            kind not in SECRET_KINDS
            or not isinstance(value, str)
            or not value.strip()
            or len(value) > 4096
            or any(c in value for c in "\r\n\x00")
        ):
            raise OperationRefused("invalid secret replacement")
        with self._lock():
            return self._stage(kind, value, actor_id, account_id)

    def _stage(self, kind: str, value: str, actor_id: str, account_id: str) -> dict[str, Any]:
        binding = digest({"actor_id": actor_id, "account_id": account_id})
        entries = self._inventory()
        if sum(entry["binding"] == binding for entry in entries) >= 4 or len(entries) >= 256:
            raise OperationRefused("secret spool capacity reached")
        stage_id = str(uuid4())
        expires = int(time.time()) + 900
        payload = {
            "kind": kind,
            "value": value,
            "actor_id": str(actor_id),
            "account_id": account_id,
            "expires": expires,
        }
        key = Fernet.generate_key()
        sealed = self.public_key.encrypt(
            key,
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        envelope = {
            "key": sealed.hex(),
            "payload": Fernet(key).encrypt(json.dumps(payload).encode()).decode(),
            "binding": binding,
        }
        fd = os.open(
            self.directory / (stage_id + ".stage"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        if os.name != "nt":
            cast(Any, os).fchmod(fd, 0o640 if self.shared_gid is not None else 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(envelope).encode())
            stream.flush()
            os.fsync(stream.fileno())
        return {
            "stage_id": stage_id,
            "kind": kind,
            "expires_at": datetime.fromtimestamp(expires, UTC).isoformat(),
        }

    def read(self, stage_id: str, actor_id: str, account_id: str) -> dict[str, Any]:
        if not self.private_file:
            raise OperationRefused("secret decryption is host-runner only")
        private_file = Path(self.private_file)
        path = self.directory / (str(UUID(stage_id)) + ".stage")
        if (
            private_file.is_symlink()
            or path.is_symlink()
            or (os.name != "nt" and stat.S_IMODE(private_file.stat().st_mode) & 0o077)
        ):
            raise OperationRefused("invalid secret storage protection")
        try:
            if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > 16384:
                raise OperationRefused("invalid secret spool entry")
            private_key = serialization.load_pem_private_key(
                private_file.read_bytes(), password=None
            )
            if not isinstance(private_key, rsa.RSAPrivateKey):
                raise OperationRefused("secret private key must be RSA")
            envelope = json.loads(path.read_bytes())
            key = private_key.decrypt(
                bytes.fromhex(envelope["key"]),
                padding.OAEP(
                    mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None
                ),
            )
            value = json.loads(Fernet(key).decrypt(envelope["payload"].encode(), ttl=900))
        except Exception:
            raise OperationRefused("secret stage unavailable or expired") from None
        if (
            value["actor_id"] != str(actor_id)
            or value["account_id"] != account_id
            or value["expires"] < time.time()
            or value["kind"] not in SECRET_KINDS
        ):
            raise OperationRefused("secret stage binding mismatch")
        return dict(value)

    def consume(self, stage_id: str) -> None:
        with self._lock():
            (self.directory / (str(UUID(stage_id)) + ".stage")).unlink()


class OperationsHandlers:
    def __init__(
        self,
        environ: Mapping[str, str],
        store: ConsoleStore,
        runner: HostOperationsRunner | None = None,
    ) -> None:
        self.environ, self.store, self.runner = environ, store, runner

    def capability(self, action: str, scope: dict[str, Any]) -> dict[str, Any]:
        if action not in HOST_ACTIONS:
            return {"reasons": []}
        snapshot = self.store.latest_snapshot("services", scope)
        if not snapshot or snapshot.get("error") or not snapshot.get("observed_at"):
            return {
                "reasons": [
                    {
                        "code": "host_evidence_unavailable",
                        "message": "Dedicated host executor observation is required",
                    }
                ]
            }
        observed = snapshot["observed_at"]
        observed = (
            datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
            if not isinstance(observed, datetime)
            else observed
        )
        return {
            "reasons": []
            if 0 <= (datetime.now(UTC) - observed).total_seconds() <= 30
            else [
                {
                    "code": "host_evidence_stale",
                    "message": "Dedicated host executor observation is stale",
                }
            ]
        }

    async def preview(
        self,
        action: str,
        arguments: dict[str, Any],
        target_refs: list[dict[str, Any]],
        scope: dict[str, Any],
    ) -> dict[str, Any]:
        validate_arguments(action, arguments)
        account = (
            self.environ.get("CONSOLE_ACCOUNT_ID")
            or self.environ.get("BITGET_ACCOUNT_ID")
            or "configured-bitget"
        )
        if scope.get("environment") != "LIVE" or scope.get("account_id") != account:
            raise OperationRefused("operations require the configured LIVE account")
        resource = self._resource(action, arguments, scope)
        gates = []
        if action in HOST_ACTIONS:
            if self.runner is not None:
                import asyncio

                await asyncio.to_thread(self.runner.observe)
            gates.append(
                {
                    "name": "host_executor_observation",
                    "verdict": "pass"
                    if not self.capability(action, scope)["reasons"]
                    else "unknown",
                }
            )
        return {
            "normalized_arguments": arguments,
            "resource_version": digest(resource),
            "effects": [
                {
                    "action": action,
                    "targets": target_refs,
                    "summary": "Apply edited settings, verify consumer environments, and restore "
                    "the prior running posture; requires authenticated flat inventory"
                    if action == "config.save"
                    else "Analysis-only; no admission or receipt rewrite"
                    if action == "signal.reanalyze"
                    else "Scoped operation; no global safety latch clearing",
                    **({"settings": arguments["settings"]} if action == "config.save" else {}),
                }
            ],
            "gates": gates,
            "warnings": [
                "Desired config is not effective until consumer restart and observed readback"
            ]
            if action.startswith(("config.", "source."))
            else [],
        }

    def current_resource_version(
        self,
        action: str,
        arguments: dict[str, Any],
        target_refs: list[dict[str, Any]],
        scope: dict[str, Any],
    ) -> str:
        validate_arguments(action, arguments)
        return digest(self._resource(action, arguments, scope))

    def _resource(
        self, action: str, arguments: dict[str, Any], scope: dict[str, Any]
    ) -> dict[str, Any]:
        if action == "notification.configure_schedule":
            snapshot = self.store.latest_snapshot("notification_schedule", scope)
            if not snapshot or snapshot.get("error") or not snapshot.get("data"):
                raise OperationRefused("actual fixed health report timer observation required")
            timer = snapshot["data"]
            return {
                "scope": scope,
                "arguments": arguments,
                "timer": {
                    "unit": timer["unit"],
                    "desired_interval_hours": timer.get("desired_interval_hours"),
                    "monotonic": timer.get("fields", {}).get("TimersMonotonic"),
                    "active": timer.get("fields", {}).get("ActiveState"),
                },
            }
        if arguments.get("maintenance_plan_id"):
            plan = self.store.one(
                "SELECT * FROM console_maintenance_plans WHERE id=%s AND expires_at>now()",
                (arguments["maintenance_plan_id"],),
            )
            if (
                not plan
                or plan["scope"] != scope
                or not plan["no_replay"]
                or not set(arguments.get("services", [])).issubset(plan["services"])
            ):
                raise OperationRefused("reviewed maintenance plan scope changed or expired")
        if action in ("config.save", "config.save_draft", "source.configure"):
            row = self.store.one(
                "SELECT coalesce(max(version),0) AS version FROM console_config_versions "
                "WHERE kind='runtime'"
            )
            if row is None or int(arguments["base_version"]) != int(row["version"]):
                raise OperationRefused("configuration version changed")
            if action == "source.configure" or "ANALYZER_CHANNEL_IDS" in arguments.get(
                "settings", {}
            ):
                ids = arguments.get(
                    "live_channel_ids", arguments.get("settings", {}).get("ANALYZER_CHANNEL_IDS")
                )
                known = {
                    int(r["channel_id"])
                    for r in self.store.query(
                        "SELECT DISTINCT channel_id FROM telegram_messages WHERE "
                        "channel_id = ANY(%s)",
                        (ids,),
                    )
                }
                if set(ids) != known:
                    raise OperationRefused("source must have authenticated intake evidence")
            return row
        if action == "config.apply":
            row = self.store.one(
                "SELECT * FROM console_config_versions WHERE id=%s", (arguments["draft_id"],)
            )
            if (
                not row
                or row["state"] != "draft"
                or row.get("account_id") != scope["account_id"]
                or row.get("environment") != scope["environment"]
            ):
                raise OperationRefused("config draft is unavailable in this scope")
            return row
        if action.startswith(("latch.", "baseline.")):
            return {
                "scope": scope,
                "arguments": arguments,
                "latches": self.store.query(
                    "SELECT scope,active,reason,updated_at FROM venue_kill_switches ORDER BY scope"
                ),
                "baseline": self.store.one(
                    "SELECT * FROM bitget_audited_baselines WHERE id=%s",
                    (arguments["baseline_id"],),
                )
                if arguments.get("baseline_id")
                else None,
            }
        if action == "notification.requeue":
            row = self.store.one(
                "SELECT * FROM notifications_outbox WHERE id=%s", (arguments["notification_id"],)
            )
            if not row or not row.get("failed_at") or row.get("sent_at") or row.get("claimed_by"):
                raise OperationRefused(
                    "only terminal-failed unclaimed notifications may be requeued"
                )
            return {k: row.get(k) for k in ("id", "attempts", "failed_at", "sent_at", "claimed_by")}
        if action == "incident.acknowledge":
            row = self.store.one(
                "SELECT scope,active,reason,updated_at FROM venue_kill_switches WHERE scope=%s",
                (arguments["latch_scope"],),
            )
            if not row or not row["active"] or row["reason"] != arguments["expected_reason"]:
                raise OperationRefused("active incident reason changed or incident is unavailable")
            return row
        if action in ("signal.reanalyze", "signal.annotate"):
            row = self.store.one(
                "SELECT id,revision_hash,raw_text,message_id FROM telegram_messages WHERE id=%s",
                (arguments["message_id"],),
            )
            if not row or row["revision_hash"].strip() != arguments["revision"]:
                raise OperationRefused("immutable source revision mismatch")
            return row
        if action.startswith("release."):
            row = self.store.one(
                "SELECT * FROM console_artifacts WHERE id=%s AND approved=true",
                (arguments["release_id"],),
            )
            if (
                not row
                or row.get("account_id") != scope["account_id"]
                or row.get("environment") != scope["environment"]
            ):
                raise OperationRefused("approved immutable artifact not found in this scope")
            return row
        if action.startswith("backup.") and action != "backup.create":
            row = self.store.one(
                "SELECT * FROM console_backups WHERE id=%s AND state <> 'deleted'",
                (arguments["backup_id"],),
            )
            if (
                not row
                or row["sha256"] != arguments["sha256"]
                or row.get("account_id") != scope["account_id"]
                or row.get("environment") != scope["environment"]
            ):
                raise OperationRefused("backup catalog scope or digest mismatch")
            return row
        if action.startswith("admission."):
            return (
                self.store.one(
                    "SELECT * FROM console_admission_pauses WHERE scope=%s",
                    (self._pause_scope(scope),),
                )
                or {}
            )
        return {"scope": scope, "arguments": arguments}

    def _pause_scope(self, scope: dict[str, Any]) -> str:
        return f"bitget:{scope['account_id']}:LIVE"

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        action, args = job["action"], job["arguments"]
        validate_arguments(action, args)
        old = self.store.one(
            "SELECT * FROM console_operation_receipts WHERE job_id=%s", (job["id"],)
        )
        if old:
            return await self.reconcile(job)
        resource = self._resource(action, args, job["scope"])
        # Command service checks resource_version immediately before this boundary.
        if action in HOST_ACTIONS:
            if self.runner is None:
                raise OperationRefused("host jobs require the dedicated executor")
            import asyncio

            return await asyncio.to_thread(self.runner.execute, job, resource)
        import asyncio

        health: dict[str, Any] | None = None
        analysis: dict[str, Any] | None = None
        if action == "notification.send_health":
            health = await asyncio.to_thread(self._health_snapshot)
        elif action == "signal.reanalyze":
            analysis = await asyncio.to_thread(self._analyze, resource, args)
        exported = self._export(args, job) if action == "report.export" else None
        # Metadata effects and their terminal receipts share one owned transaction.
        with self.store.connection() as conn:
            cursor = self.store.cursor(conn)
            current_job = self.store.effect_authority(cursor, job)
            if current_job.get("cancellation_requested_at"):
                raise OperationRefused("operation was cancelled before effect")
            if action in ("config.save_draft", "source.configure"):
                revision = create_config_revision(cursor, job)
                result = {
                    "draft_id": revision["id"],
                    "version": revision["version"],
                    "state": "draft",
                    "effective": False,
                }
            elif action == "maintenance.prepare":
                plan_id = str(uuid4())
                cursor.execute(
                    "INSERT INTO "
                    "console_maintenance_plans(id,actor_id,scope,services,no_replay,reaso"
                    "n,expires_at) VALUES "
                    "(%s,%s,%s::jsonb,%s::jsonb,true,%s,now()+interval '1 hour')",
                    (
                        plan_id,
                        job["actor_id"],
                        json.dumps(job["scope"]),
                        json.dumps(args["services"]),
                        job["reason"],
                    ),
                )
                result = {
                    "maintenance_plan_id": plan_id,
                    "services": args["services"],
                    "no_replay": True,
                    "scope": job["scope"],
                    "expires_in_seconds": 3600,
                }
            elif action == "safety.contain":
                cursor.execute(
                    "INSERT INTO venue_kill_switches(scope,active,reason,updated_at) "
                    "VALUES (%s,true,%s,now()) ON CONFLICT(scope) DO UPDATE SET "
                    "active=true,reason=CASE WHEN venue_kill_switches.active THEN "
                    "venue_kill_switches.reason ELSE EXCLUDED.reason END,updated_at=now()",
                    (args["latch_scope"], "console-containment:" + str(job["id"])),
                )
                result = {
                    "latch_scope": args["latch_scope"],
                    "active": True,
                    "existing_incident_reason_preserved": True,
                    "execution_config_unchanged": True,
                }
            elif action.startswith("notification."):
                notification_id = str(uuid4())
                if action == "notification.test":
                    payload: dict[str, Any] = {
                        "event": "console_test",
                        "message": "Owner-approved console notification delivery test",
                        "job_id": str(job["id"]),
                    }
                elif action == "notification.send_health":
                    if health is None:
                        raise OperationRefused("health snapshot unavailable")
                    payload = {
                        "kind": "console-health-report",
                        "snapshot": health,
                        "job_id": str(job["id"]),
                    }
                else:
                    cursor.execute(
                        "SELECT payload,attempts FROM notifications_outbox WHERE id=%s "
                        "AND failed_at IS NOT NULL AND sent_at IS NULL AND claimed_by IS "
                        "NULL FOR UPDATE",
                        (args["notification_id"],),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise OperationRefused("notification eligibility changed")
                    payload = dict(row["payload"] if isinstance(row, dict) else row[0])
                    payload.update(
                        {
                            "original_notification_id": args["notification_id"],
                            "requeue_job_id": str(job["id"]),
                            "prior_attempts": row["attempts"] if isinstance(row, dict) else row[1],
                        }
                    )
                cursor.execute(
                    "INSERT INTO notifications_outbox(id,dedup_key,payload) VALUES "
                    "(%s,%s,%s::jsonb)",
                    (notification_id, "console:" + str(job["id"]), json.dumps(payload)),
                )
                result = {
                    "notification_id": notification_id,
                    "delivery_state": "queued",
                    "original_notification_id": args.get("notification_id"),
                }
            elif action.startswith("admission."):
                active = action == "admission.pause"
                if not active:
                    # Readiness checks incident gates, pending ownership and protection,
                    # independently of the dedicated console pause that we are releasing.
                    import psycopg

                    from fatty_trader.execution.bitget_dispatch_repository import (
                        PostgresBitgetDispatchRepository,
                    )
                    from fatty_trader.execution.bitget_runtime_readiness import (
                        ledger_readiness_issues,
                    )

                    repository = PostgresBitgetDispatchRepository(lambda: psycopg.connect())
                    incident = self.store.one(
                        "SELECT EXISTS(SELECT 1 FROM venue_kill_switches WHERE active) AS active"
                    )
                    if (
                        incident is None
                        or incident["active"]
                        or ledger_readiness_issues(repository, "LIVE")
                    ):
                        raise OperationRefused("incident readiness gates prohibit resume")
                    for kind in ("account", "positions", "orders", "plan_orders", "clock"):
                        snap = self.store.latest_snapshot(kind, job["scope"])
                        if not snap or snap.get("error") or not snap.get("observed_at"):
                            raise OperationRefused(
                                "fresh independent readiness observations required"
                            )
                        observed = snap["observed_at"]
                        observed = (
                            datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
                            if not isinstance(observed, datetime)
                            else observed
                        )
                        if not 0 <= (datetime.now(UTC) - observed).total_seconds() <= 30:
                            raise OperationRefused("resume proof is stale")
                cursor.execute(
                    "INSERT INTO "
                    "console_admission_pauses(scope,active,reason,actor_id,job_id,updated"
                    "_at) VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT(scope) DO UPDATE SET "
                    "active=EXCLUDED.active,reason=EXCLUDED.reason,actor_id=EXCLUDED.acto"
                    "r_id,job_id=EXCLUDED.job_id,updated_at=now()",
                    (
                        self._pause_scope(job["scope"]),
                        active,
                        job["reason"],
                        job["actor_id"],
                        job["id"],
                    ),
                )
                result = {
                    "admission_paused": active,
                    "incident_latches_cleared": False,
                    "protection_unchanged": True,
                }
            elif action in ("signal.annotate", "incident.acknowledge"):
                # The immutable job and receipt are the annotation/acknowledgment.
                # Neither changes source eligibility nor clears an incident.
                result = {
                    **args,
                    "source_eligibility_unchanged": True,
                    "incident_latches_cleared": False,
                }
            elif action == "signal.reanalyze":
                if analysis is None:
                    raise OperationRefused("analysis run unavailable")
                cursor.execute(
                    "INSERT INTO "
                    "console_analysis_runs(id,source_message_id,analysis,actor_id) "
                    "VALUES (%s,%s,%s::jsonb,%s)",
                    (job["id"], args["message_id"], json.dumps(analysis), job["actor_id"]),
                )
                result = {"analysis_run_id": str(job["id"]), **analysis}
            elif action == "report.export":
                if exported is None:
                    raise OperationRefused("export unavailable")
                result = exported
            else:
                raise OperationRefused("operation is not executable in this worker")
            cursor.execute(
                "INSERT INTO console_operation_receipts(job_id,action,state,evidence) "
                "VALUES (%s,%s,'succeeded',%s::jsonb)",
                (job["id"], action, json.dumps(result)),
            )
        return {"state": "succeeded", "result": result}

    def _analyze(self, resource: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        from fatty_trader.analyzer.integration import analyze_with_fallback
        from fatty_trader.service import build_codex_runner

        analyzed = analyze_with_fallback(
            text=resource["raw_text"],
            message_id=resource["message_id"],
            codex_runner=build_codex_runner(self.environ).run,
        )
        return {
            "status": str(analyzed.status),
            "signal": analyzed.signal.model_dump(mode="json") if analyzed.signal else None,
            "failure_class": analyzed.failure_class,
            "analysis_only": True,
            "revision": args["revision"],
        }

    def _health_snapshot(self) -> dict[str, Any]:
        import psycopg

        from fatty_trader.exchanges.bitget.client import BitgetRestClient
        from fatty_trader.operator.bitget_gateway import BitgetOperatorGateway
        from fatty_trader.operator.health import load_operator_health_snapshot
        from fatty_trader.storage.live_intents import PostgresLiveIntentStore

        client = BitgetRestClient(
            self.environ["BITGET_API_KEY"],
            self.environ["BITGET_API_SECRET"],
            self.environ["BITGET_API_PASSPHRASE"],
            mode="LIVE",
            max_get_retries=0,
        )
        gateway = BitgetOperatorGateway(client, PostgresLiveIntentStore(lambda: psycopg.connect()))
        try:
            identity = gateway._run(client._get("/api/v2/spot/account/info"))
            expected = (
                self.environ.get("CONSOLE_ACCOUNT_ID")
                or self.environ.get("BITGET_ACCOUNT_ID")
                or "configured-bitget"
            )
            if not isinstance(identity, dict) or str(identity.get("userId")) != expected:
                raise OperationRefused("health provider account identity mismatch")
            return load_operator_health_snapshot(
                gateway,
                lambda: psycopg.connect(),
                mode="LIVE",
                venue_mode="LIVE",
                execution_enabled=self.environ.get("BITGET_EXECUTION_ENABLED") == "1",
                fallback_mutations_enabled=self.environ.get(
                    "BITGET_FALLBACK_MUTATIONS_ENABLED", "0"
                ),
            )
        finally:
            gateway.close()

    def _export(self, args: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        # Payload is an authenticated job result, never an arbitrary public filesystem URL.
        queries = {
            "signals": (
                "SELECT s.id,s.message_id AS source_message_id,tm.channel_id,tm.message_id "
                "AS "
                "telegram_message_id,s.revision,s.pair_token,s.direction,s.entry_price,s.stop"
                "_loss,s.created_at,tm.received_at FROM canonical_signals s JOIN "
                "telegram_messages tm ON tm.id=s.message_id ORDER BY s.created_at DESC LIMIT "
                "%s"
            ),
            "jobs": (
                "SELECT id,action,actor_id,state,created_at FROM console_jobs WHERE "
                "scope->>'account_id'=%s AND scope->>'environment'=%s ORDER BY created_at "
                "DESC LIMIT %s"
            ),
            "audit": (
                "SELECT id,action,outcome,actor_id,created_at FROM console_audit_events "
                "WHERE scope->>'account_id'=%s AND scope->>'environment'=%s ORDER BY "
                "created_at DESC LIMIT %s"
            ),
            "notifications": (
                "SELECT id,attempts,sent_at,failed_at,created_at FROM notifications_outbox "
                "ORDER BY created_at DESC LIMIT %s"
            ),
        }
        params = (
            (job["scope"]["account_id"], job["scope"]["environment"], args.get("limit", 100))
            if args["report_kind"] in ("jobs", "audit")
            else (args.get("limit", 100),)
        )
        from fatty_trader.console.reads import sanitize

        rows = sanitize(self.store.query(queries[args["report_kind"]], params))
        if args["format"] == "json":
            content = json.dumps(rows, default=str)
        else:
            import csv
            import io

            out = io.StringIO()
            writer = csv.writer(out)
            if rows:
                writer.writerow(rows[0].keys())
                for row in rows:
                    writer.writerow(
                        [
                            ("'" + str(value))
                            if str(value).startswith(("=", "+", "-", "@", "\t", "\r"))
                            else str(value)
                            for value in row.values()
                        ]
                    )
            content = out.getvalue()
        return {
            "format": args["format"],
            "content": content,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "count": len(rows),
            "coverage": "bounded durable rows; no implied provider completeness",
            "expires_at": datetime.fromtimestamp(time.time() + 900, UTC).isoformat(),
        }

    async def reconcile(self, job: dict[str, Any]) -> dict[str, Any]:
        row = self.store.one(
            "SELECT state,evidence FROM console_operation_receipts WHERE job_id=%s", (job["id"],)
        )
        if row and row["state"] in ("succeeded", "failed"):
            return {"state": row["state"], "result": row["evidence"]}
        if job["action"] in HOST_ACTIONS and self.runner:
            import asyncio

            return await asyncio.to_thread(self.runner.reconcile, job)
        return {
            "state": "reconciliation_pending",
            "result": {
                "reason": "claimed effect lacks terminal receipt; no dangerous operation replay"
            },
        }
