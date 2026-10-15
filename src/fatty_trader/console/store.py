"""Owned PostgreSQL transactions, durable jobs, fencing and replayable events."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

TERMINAL_STATES = frozenset(
    {"succeeded", "failed", "partially_succeeded", "rejected", "expired", "cancelled"}
)
ALLOWED_TRANSITIONS = {
    "awaiting_approval": {"queued", "rejected", "expired", "cancelled"},
    "queued": {"running", "awaiting_approval", "rejected", "expired", "cancelled"},
    "running": {
        "succeeded",
        "failed",
        "partially_succeeded",
        "reconciliation_pending",
        "cancelled",
    },
    "reconciliation_pending": {"running"},
}
SECRET_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "api_key",
        "api_secret",
        "passphrase",
        "private_key",
        "authorization",
        "cookie",
        "dsn",
        "credential",
        "assertion",
    }
)
SECRET_TEXT = re.compile(
    r"(?i)(bearer\s+\S+|(?:api[_-]?(?:key|secret|token)|password|passphrase|token|"
    r"authorization|cookie)\s*[:=]\s*[^\s,;]+|postgres(?:ql)?://[^\s]+)"
)


def safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): safe_json(v) for k, v in value.items() if str(k).lower() not in SECRET_KEYS}
    if isinstance(value, (list, tuple)):
        return [safe_json(v) for v in value]
    if isinstance(value, (Decimal, datetime)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, str):
        return SECRET_TEXT.sub("[redacted]", value)
    return value


def canonical(value: Any) -> str:
    return json.dumps(safe_json(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def configured_scope(environ: Mapping[str, str]) -> dict[str, str]:
    return {
        "account_id": environ.get("CONSOLE_ACCOUNT_ID")
        or environ.get("BITGET_ACCOUNT_ID")
        or "configured-bitget",
        "venue": "bitget",
        "environment": environ.get("BITGET_MODE", "LIVE"),
    }


class LostLease(RuntimeError):
    """The caller no longer owns this job; it must perform no further effect."""


class ResourceBusy(RuntimeError):
    pass


class EffectCancelled(RuntimeError):
    """Authority was canceled while this worker still owned an unclaimed step."""


class ConsoleStore:
    def __init__(self, connection_factory: Callable[[], Any] | None = None) -> None:
        self._connection_factory = connection_factory or (
            lambda: psycopg.connect(row_factory=dict_row)
        )

    @contextmanager
    def connection(self) -> Iterator[Any]:
        connection = self._connection_factory()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def cursor(connection: Any) -> Any:
        return connection.cursor(row_factory=dict_row)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connection() as connection:
            cursor = self.cursor(connection)
            cursor.execute(sql, params)
            return list(cursor.fetchall())

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connection() as connection:
            cursor = self.cursor(connection)
            cursor.execute(sql, params)
            return cast(dict[str, Any] | None, cursor.fetchone())

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.connection() as connection:
            cursor = self.cursor(connection)
            cursor.execute(sql, params)
            return int(cursor.rowcount)

    def audit(
        self,
        action: str,
        outcome: str,
        actor_id: str | None = None,
        scope: dict[str, Any] | None = None,
        job_id: str | None = None,
        evidence: Any = None,
        channel: str = "web",
        request_id: str | None = None,
        resource_id: str | None = None,
        cursor: Any = None,
    ) -> int:
        params = (
            actor_id,
            channel,
            action,
            outcome,
            Jsonb(safe_json(scope or {})),
            job_id,
            request_id,
            resource_id,
            Jsonb(safe_json(evidence or {})),
        )
        sql = """INSERT INTO console_audit_events
                 (actor_id,channel,action,outcome,scope,job_id,request_id,resource_id,evidence)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id"""
        if cursor is None:
            with self.connection() as connection:
                return self.audit(
                    action,
                    outcome,
                    actor_id,
                    scope,
                    job_id,
                    evidence,
                    channel,
                    request_id,
                    resource_id,
                    self.cursor(connection),
                )
        cursor.execute(sql, params)
        audit_id = int(cursor.fetchone()["id"])
        self.emit(cursor, "audit_changed", scope or {}, "audit", audit_id, audit_id, job_id)
        return audit_id

    def emit(
        self,
        cursor: Any,
        event: str,
        scope: dict[str, Any],
        resource_type: str,
        resource_id: str | int,
        resource_version: str | int,
        job_id: str | None = None,
        redacted_change: Any = None,
    ) -> int:
        cursor.execute(
            """INSERT INTO console_event_outbox
                       (event,scope,resource_type,resource_id,resource_version,job_id,
                        redacted_change)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (
                event,
                Jsonb(scope),
                resource_type,
                str(resource_id),
                str(resource_version),
                job_id,
                Jsonb(safe_json(redacted_change)) if redacted_change is not None else None,
            ),
        )
        return int(cursor.fetchone()["id"])

    def event_cursor(self) -> int:
        row = self.one("SELECT coalesce(max(id),0) AS id FROM console_event_outbox")
        assert row is not None
        return int(row["id"])

    def events(self, after: int, scope: dict[str, str], limit: int = 100) -> list[dict[str, Any]]:
        rows = self.query(
            """SELECT * FROM console_event_outbox WHERE id>%s
                          AND created_at>now()-interval '24 hours'
                          AND scope->>'account_id'=%s AND scope->>'environment'=%s
                          ORDER BY id LIMIT %s""",
            (int(after), scope["account_id"], scope["environment"], min(limit, 200)),
        )
        return [
            safe_json(
                {
                    **row,
                    "sequence": row["id"],
                    "observed_at": row["created_at"],
                    "event_kind": row["event"],
                }
            )
            for row in rows
        ]

    def event_bounds(self, scope: dict[str, str]) -> dict[str, Any] | None:
        return self.one(
            """SELECT coalesce(min(id) FILTER(WHERE created_at>now()-interval '24 hours'),
                        max(id)+1) AS first,max(id) AS last FROM console_event_outbox
                        WHERE scope->>'account_id'=%s AND scope->>'environment'=%s""",
            (scope["account_id"], scope["environment"]),
        )

    def put_snapshot(
        self,
        kind: str,
        scope: dict[str, str],
        data: Any,
        error: Any = None,
        observed_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        snapshot_id = str(uuid4())
        with self.connection() as connection:
            cursor = self.cursor(connection)
            cursor.execute(
                """INSERT INTO console_snapshots
                           (id,kind,account_id,environment,scope,data,error,observed_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    snapshot_id,
                    kind,
                    scope["account_id"],
                    scope["environment"],
                    Jsonb(scope),
                    Jsonb(safe_json(data)) if data is not None else None,
                    Jsonb(safe_json(error)) if error is not None else None,
                    observed_at,
                ),
            )
            snapshot = cursor.fetchone()
            self.emit(cursor, "read_model_changed", scope, kind, snapshot_id, snapshot["revision"])
            return cast(dict[str, Any], safe_json(snapshot))

    def latest_snapshot(self, kind: str, scope: dict[str, str]) -> dict[str, Any] | None:
        row = self.one(
            """SELECT * FROM console_snapshots WHERE kind=%s
                       AND (account_id=%s OR account_id IN (
                           SELECT (data->'values'->>'provider_account_id')
                           FROM console_snapshots
                           WHERE kind='account_identity' AND environment=%s
                           ORDER BY id DESC LIMIT 1
                       ))
                       AND environment=%s ORDER BY collected_at DESC,id DESC LIMIT 1""",
            (kind, scope["account_id"], scope["environment"], scope["environment"]),
        )
        return cast(dict[str, Any], safe_json(row)) if row is not None else None

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.one("SELECT * FROM console_jobs WHERE id=%s", (str(job_id),))
        return self.job_dto(row) if row else None

    @staticmethod
    def job_dto(row: dict[str, Any]) -> dict[str, Any]:
        result = cast(dict[str, Any], safe_json(dict(row)))
        result["id"] = str(result["id"])
        result["resource_version"] = str(result["revision"])
        result["target_resource_version"] = row["resource_version"]
        result["normalized_arguments_redacted"] = result["arguments"]
        result["initiator"] = {"id": result["actor_id"]}
        result["cancellation"] = {
            "requested_at": result.get("cancellation_requested_at"),
            "accepted": result.get("cancellation_requested_at") is not None,
        }
        result.pop("lease_token", None)
        result.pop("session_id", None)
        return result

    def _record_job(self, cursor: Any, job: dict[str, Any], outcome: str) -> None:
        self.audit(
            job["action"],
            outcome,
            job["actor_id"],
            job["scope"],
            job["id"],
            {"phase": job["phase"], "state": job["state"], "revision": job["revision"]},
            cursor=cursor,
        )
        self.emit(cursor, "job_changed", job["scope"], "job", job["id"], job["revision"], job["id"])

    def claim_job(
        self, worker_id: str, actions: Collection[str], lease_seconds: int = 120
    ) -> dict[str, Any] | None:
        if not actions:
            return None
        token = str(uuid4())
        with self.connection() as connection:
            cursor = self.cursor(connection)
            cursor.execute(
                """SELECT * FROM console_jobs WHERE action=ANY(%s)
                           AND state IN ('queued','running','reconciliation_pending')
                           AND (lease_until IS NULL OR lease_until<now())
                           ORDER BY updated_at,id FOR UPDATE SKIP LOCKED LIMIT 1""",
                (sorted(actions),),
            )
            job = cursor.fetchone()
            if job is None:
                return None
            recovering = (
                job["state"] != "queued" or job["lease_token"] is not None or job["effect_claimed"]
            )
            cursor.execute(
                """UPDATE console_jobs SET state=%s, phase=%s,claimed_by=%s,
                           lease_token=%s,lease_until=now()+(%s * interval '1 second'),
                           claim_generation=claim_generation+1,revision=revision+1,updated_at=now()
                           WHERE id=%s RETURNING *""",
                (
                    "running" if recovering else "queued",
                    "readback" if recovering else "prechecks",
                    worker_id,
                    token,
                    lease_seconds,
                    job["id"],
                ),
            )
            job = cursor.fetchone()
            # Lost leases never allow a replacement POST, even if the prior worker had
            # only reached prechecks. Recovery may prove a safe terminal outcome.
            job["readback_only"] = recovering or job["effect_claimed"]
            cursor.execute("SAVEPOINT console_resource_claim")
            try:
                self._lock_resources(cursor, job, lease_seconds)
            except ResourceBusy:
                cursor.execute("ROLLBACK TO SAVEPOINT console_resource_claim")
                cursor.execute(
                    """UPDATE console_jobs SET state=%s,lease_token=NULL,lease_until=NULL,
                               claimed_by=NULL,updated_at=now() WHERE id=%s""",
                    ("reconciliation_pending" if recovering else "queued", job["id"]),
                )
                return None
            cursor.execute("RELEASE SAVEPOINT console_resource_claim")
            self._record_job(cursor, job, "claimed_readback" if job["readback_only"] else "claimed")
            return cast(dict[str, Any], job)

    def _lock_resources(self, cursor: Any, job: dict[str, Any], lease_seconds: int) -> None:
        scope = job["scope"]
        prefix = f"{scope['account_id']}:{scope['environment']}"
        # Readback must remain available while the original ambiguous mutation
        # retains its financial locks. Identity recovery has a separate domain.
        if job["action"].endswith((".reconcile", ".observe")) or job["danger_tier"] == 0:
            return
        identity = job["action"].startswith(("access.", "session."))
        financial = job["action"].startswith(
            (
                "trade.",
                "entry.",
                "order.",
                "position.",
                "protection.",
                "management.",
                "service.",
                "release.",
                "latch.",
                "baseline.",
            )
        )
        financial = financial or job["action"] in {"backup.restore", "config.apply"}
        resources = (
            {prefix + ":access"} if identity else {prefix + ":account"} if financial else set()
        )
        for target in job["target_refs"]:
            resources.add(prefix + ":" + str(target["kind"]) + ":" + str(target["id"]))
        for field in (
            "client_oid",
            "symbol",
            "principal_id",
            "session_id",
            "draft_id",
            "backup_id",
            "release_id",
            "baseline_id",
        ):
            identity = job["arguments"].get(field)
            if identity is not None:
                resources.add(prefix + ":" + field + ":" + str(identity))
        for key in sorted(resources):
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (key,))
            cursor.execute(
                """SELECT l.*,j.state FROM console_resource_locks l
                           JOIN console_jobs j ON j.id=l.job_id
                           WHERE resource_key=%s FOR UPDATE OF l""",
                (key,),
            )
            lock = cursor.fetchone()
            if lock and lock["job_id"] != job["id"] and lock["state"] not in TERMINAL_STATES:
                raise ResourceBusy(key)
            cursor.execute(
                """INSERT INTO console_resource_locks
                           (resource_key,job_id,lease_token,claim_generation,lease_until)
                           VALUES (%s,%s,%s,%s,now()+(%s * interval '1 second'))
                           ON CONFLICT(resource_key) DO UPDATE SET job_id=excluded.job_id,
                           lease_token=excluded.lease_token,
                           claim_generation=excluded.claim_generation,
                           lease_until=excluded.lease_until""",
                (key, job["id"], job["lease_token"], job["claim_generation"], lease_seconds),
            )

    def owned_job(self, cursor: Any, job: dict[str, Any]) -> dict[str, Any]:
        cursor.execute(
            """SELECT * FROM console_jobs WHERE id=%s AND lease_token=%s
                       AND claim_generation=%s AND state IN ('queued','running')
                       AND lease_until>now() FOR UPDATE""",
            (job["id"], job["lease_token"], job["claim_generation"]),
        )
        current = cursor.fetchone()
        if current is None:
            raise LostLease(job["id"])
        return cast(dict[str, Any], current)

    def effect_authority(self, cursor: Any, job: dict[str, Any]) -> dict[str, Any]:
        """Validate fresh authority only for a new effect, never for its readback."""
        from fatty_trader.console.commands import CommandService

        current = self.owned_job(cursor, job)
        if current["cancellation_requested_at"] is not None:
            raise EffectCancelled("cancelled before effect")
        CommandService(self, {current["action"]: None})._authority(cursor, current)
        return current

    def begin_running(self, job: dict[str, Any]) -> None:
        with self.connection() as connection:
            cursor = self.cursor(connection)
            current = self.effect_authority(cursor, job)
            if current["state"] != "queued":
                raise LostLease("authorization no longer eligible")
            cursor.execute(
                """UPDATE console_jobs SET state='running',revision=revision+1,updated_at=now()
                           WHERE id=%s RETURNING *""",
                (job["id"],),
            )
            updated = cursor.fetchone()
            self._record_job(cursor, updated, "started")
            job["state"] = "running"

    def renew_lease(self, job: dict[str, Any], lease_seconds: int = 120) -> None:
        with self.connection() as connection:
            cursor = self.cursor(connection)
            self.owned_job(cursor, job)
            cursor.execute(
                """UPDATE console_jobs SET lease_until=now()+(%s * interval '1 second')
                           WHERE id=%s AND lease_token=%s""",
                (lease_seconds, job["id"], job["lease_token"]),
            )
            cursor.execute(
                """UPDATE console_resource_locks SET lease_until=now()+(%s * interval '1 second')
                           WHERE job_id=%s AND lease_token=%s""",
                (lease_seconds, job["id"], job["lease_token"]),
            )

    def checkpoint(self, job: dict[str, Any], phase: str, evidence: Any = None) -> None:
        with self.connection() as connection:
            cursor = self.cursor(connection)
            current = self.owned_job(cursor, job)
            cursor.execute(
                """INSERT INTO console_job_steps
                           (job_id,sequence,phase,state,lease_token,claim_generation,evidence)
                           SELECT %s,coalesce(max(sequence),0)+1,%s,'recorded',%s,%s,%s
                           FROM console_job_steps WHERE job_id=%s""",
                (
                    job["id"],
                    phase,
                    job["lease_token"],
                    job["claim_generation"],
                    Jsonb(safe_json(evidence or {})),
                    job["id"],
                ),
            )
            cursor.execute(
                """UPDATE console_jobs SET phase=%s,revision=revision+1,updated_at=now()
                           WHERE id=%s RETURNING *""",
                (phase, current["id"]),
            )
            self._record_job(cursor, cursor.fetchone(), "checkpoint")

    def claim_effect(
        self, job: dict[str, Any], effect_key: str, endpoint: str, payload: Any, stable_id: str
    ) -> bool:
        """Commit the at-most-once boundary before adapter/domain POST claims."""
        with self.connection() as connection:
            cursor = self.cursor(connection)
            self.effect_authority(cursor, job)
            cursor.execute(
                """INSERT INTO console_job_effects
                           (id,job_id,effect_key,endpoint,payload_digest,stable_id,
                            lease_token,claim_generation)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT(job_id,effect_key) DO NOTHING RETURNING id""",
                (
                    str(uuid4()),
                    job["id"],
                    effect_key,
                    endpoint,
                    digest(payload),
                    stable_id,
                    job["lease_token"],
                    job["claim_generation"],
                ),
            )
            new = cursor.fetchone() is not None
            if new:
                cursor.execute(
                    """UPDATE console_jobs SET effect_claimed=TRUE,phase='intent_claim',
                               revision=revision+1,updated_at=now() WHERE id=%s RETURNING *""",
                    (job["id"],),
                )
                self._record_job(cursor, cursor.fetchone(), "effect_claimed")
            return new

    def finish_job(
        self, job: dict[str, Any], state: str, result: Any = None, error: Any = None
    ) -> dict[str, Any]:
        if state not in {
            "succeeded",
            "failed",
            "partially_succeeded",
            "reconciliation_pending",
            "cancelled",
            "awaiting_approval",
            "rejected",
            "expired",
        }:
            raise ValueError("invalid outcome state")
        with self.connection() as connection:
            cursor = self.cursor(connection)
            current = self.owned_job(cursor, job)
            if state not in ALLOWED_TRANSITIONS[current["state"]]:
                raise ValueError("invalid job state transition")
            if state in {"awaiting_approval", "rejected", "expired"} and current["effect_claimed"]:
                raise ValueError("claimed effects require readback")
            cursor.execute(
                """UPDATE console_jobs SET state=%s,phase=%s,result=%s,error=%s,
                           reconciliation_required=%s,
                           lease_until=CASE WHEN %s='reconciliation_pending'
                           THEN now()+interval '10 seconds' ELSE NULL END,
                           lease_token=NULL,claimed_by=NULL,
                           revision=revision+1,updated_at=now() WHERE id=%s RETURNING *""",
                (
                    state,
                    "readback" if state == "reconciliation_pending" else "complete",
                    Jsonb(safe_json(result)) if result is not None else None,
                    Jsonb(safe_json(error)) if error is not None else None,
                    state == "reconciliation_pending",
                    state,
                    job["id"],
                ),
            )
            updated = cursor.fetchone()
            if current["effect_claimed"]:
                cursor.execute(
                    """UPDATE console_job_effects SET state=%s,result=%s,
                               resolved_at=CASE WHEN %s THEN now() ELSE NULL END
                               WHERE job_id=%s AND state IN ('claimed','reconciliation_pending')""",
                    (
                        state,
                        Jsonb(safe_json(result)) if result is not None else None,
                        state in TERMINAL_STATES,
                        job["id"],
                    ),
                )
            if state in TERMINAL_STATES or (
                state == "awaiting_approval" and not current["effect_claimed"]
            ):
                cursor.execute(
                    "DELETE FROM console_resource_locks WHERE job_id=%s AND lease_token=%s",
                    (job["id"], job["lease_token"]),
                )
            self._record_job(cursor, updated, state)
            return self.job_dto(updated)
