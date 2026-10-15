"""Digest-bound previews, authorization, approval and durable command submission."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, FormatChecker
from psycopg.types.json import Jsonb

from fatty_trader.console.store import TERMINAL_STATES, ConsoleStore, digest, safe_json

POLICY_VERSION = "console-policy-26-v1"
ADAPTER_VERSION = "console-adapters-26-v1"


class CommandError(ValueError):
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


def catalog() -> tuple[dict[str, Any], dict[str, Any]]:
    from fatty_trader.console.operations import ACTION_POLICIES, ACTION_SCHEMAS
    from fatty_trader.console.trading import ACTION_SCHEMAS as TRADING_SCHEMAS

    schemas: dict[str, Any] = {**ACTION_SCHEMAS, **TRADING_SCHEMAS}
    policies: dict[str, Any] = {
        **{
            name: {
                "role": "operator",
                "danger_tier": 0 if name.endswith((".reconcile", ".observe")) else 2,
            }
            for name in TRADING_SCHEMAS
        },
        **ACTION_POLICIES,
    }
    policies["protection.disable_fallback"] = {"role": "risk_owner", "danger_tier": 3}
    # Owner/admin grants remain two-person; access enrollment does not bypass it.
    text = {"type": "string", "minLength": 1, "maxLength": 200}
    schemas.update(
        {
            "access.invite": {
                "type": "object",
                "properties": {
                    "display_name": text,
                    "roles": {
                        "type": "array",
                        "items": {
                            "enum": [
                                "viewer",
                                "operator",
                                "risk_owner",
                                "ops_admin",
                                "access_admin",
                                "owner",
                            ]
                        },
                        "minItems": 1,
                        "uniqueItems": True,
                    },
                },
                "required": ["display_name", "roles"],
                "additionalProperties": False,
            },
            "access.change_roles": {
                "type": "object",
                "properties": {
                    "principal_id": text,
                    "roles": {
                        "type": "array",
                        "items": {
                            "enum": [
                                "viewer",
                                "operator",
                                "risk_owner",
                                "ops_admin",
                                "access_admin",
                                "owner",
                            ]
                        },
                        "minItems": 1,
                        "uniqueItems": True,
                    },
                },
                "required": ["principal_id", "roles"],
                "additionalProperties": False,
            },
            "access.disable": {
                "type": "object",
                "properties": {"principal_id": text},
                "required": ["principal_id"],
                "additionalProperties": False,
            },
            "session.revoke": {
                "type": "object",
                "properties": {"session_id": text},
                "required": ["session_id"],
                "additionalProperties": False,
            },
        }
    )
    for action in ("access.invite", "access.change_roles", "access.disable"):
        policies[action] = {"role": "access_admin", "danger_tier": 3}
    policies["session.revoke"] = {"role": "access_admin", "danger_tier": 2}
    return schemas, policies


class CommandService:
    def __init__(self, store: ConsoleStore, handlers: dict[str, Any]) -> None:
        self.store = store
        self.handlers = handlers

    def _principal(
        self, cursor: Any, principal: dict[str, Any], scope: dict[str, Any], role: str = "viewer"
    ) -> dict[str, Any]:
        cursor.execute(
            """SELECT p.*,s.revoked_at,s.expires_at,s.absolute_expires_at,
                       s.principal_revision,s.last_seen_at,now() AS authority_now
                       FROM console_principals p JOIN console_sessions s ON s.principal_id=p.id
                       WHERE p.id=%s AND s.id=%s FOR SHARE""",
            (principal["id"], principal["session_id"]),
        )
        row = cursor.fetchone()
        now = row["authority_now"] if row and "authority_now" in row else datetime.now(UTC)
        if (
            not row
            or not row["enabled"]
            or row["revoked_at"]
            or row["expires_at"] <= now
            or row["absolute_expires_at"] <= now
            or row["principal_revision"] != row["revision"]
            or now - row["last_seen_at"] >= timedelta(minutes=30)
        ):
            raise CommandError(
                "session_expired", "A current authenticated session is required", 401
            )
        roles = row["roles"]
        if role != "viewer" and role not in roles and "owner" not in roles:
            raise CommandError("permission_denied", "Required action privilege is missing", 403)
        if role == "viewer" and not roles:
            raise CommandError("permission_denied", "Read privilege is missing", 403)
        scopes = row["scopes"]
        if not any(
            all(
                binding.get(key) == scope.get(key) for key in ("account_id", "venue", "environment")
            )
            for binding in scopes
        ):
            raise CommandError("scope_denied", "This account scope is not authorized", 403)
        return cast(dict[str, Any], row)

    def capabilities(
        self, principal: dict[str, Any], scope: dict[str, Any]
    ) -> list[dict[str, Any]]:
        schemas, policies = catalog()
        owners = self.store.query(
            """SELECT p.id,p.scopes FROM console_principals p
            WHERE p.enabled AND p.roles @> '["owner"]'::jsonb
            AND EXISTS(SELECT 1 FROM console_credentials c WHERE c.principal_id=p.id)"""
        )
        distinct_owner = any(
            row["id"] != principal["id"] and scope in row["scopes"] for row in owners
        )
        result = []
        for action, schema in sorted(schemas.items()):
            policy = policies[action]
            reasons = []
            if policy["danger_tier"] == 3 and not distinct_owner:
                reasons.append(
                    {
                        "code": "second_approver_required",
                        "message": (
                            "Enroll a distinct eligible owner for this scoped two-person action"
                        ),
                    }
                )
            if (
                policy["role"] not in principal["roles"]
                and "owner" not in principal["roles"]
                and policy["role"] != "viewer"
            ):
                reasons.append(
                    {"code": "permission_denied", "message": "Required action privilege is missing"}
                )
            if action not in self.handlers:
                reasons.append(
                    {
                        "code": "policy_prohibited",
                        "message": "This action has no approved execution authority",
                    }
                )
            # Provider gate and read evidence are independently advertised by adapters.
            handler = self.handlers.get(action)
            gates = []
            if handler and hasattr(handler, "capability"):
                extra = handler.capability(action, scope)
                reasons.extend(extra.get("reasons", []))
                gates = extra.get("gates", [])
            result.append(
                {
                    "action": action,
                    "support": "gated"
                    if reasons
                    else "read_only"
                    if policy["danger_tier"] == 0
                    else "available",
                    "reasons": reasons,
                    "required_role": policy["role"],
                    "required_scopes": [scope],
                    "danger_tier": policy["danger_tier"],
                    "gates": gates,
                    "policy_version": POLICY_VERSION,
                    "adapter_version": ADAPTER_VERSION,
                    "preview_allowed": action in self.handlers
                    and not any(r["code"] == "permission_denied" for r in reasons),
                    "enqueue_allowed": not reasons,
                    "arguments_schema": schema,
                }
            )
        return result

    def _policy(self, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        schemas, policies = catalog()
        if action not in schemas or action not in self.handlers:
            raise CommandError(
                "action_unsupported", "Action is not authorized by the current policy"
            )
        errors = list(
            Draft202012Validator(schemas[action], format_checker=FormatChecker()).iter_errors(
                arguments
            )
        )
        if errors:
            raise CommandError(
                "invalid_arguments", "Arguments do not match the fixed action schema", 422
            )
        # Secret-bearing inputs must only reach the isolated secret-stage endpoint.
        from fatty_trader.console.store import SECRET_KEYS

        def check(value: Any) -> None:
            if isinstance(value, dict):
                if any(str(key).lower() in SECRET_KEYS for key in value):
                    raise CommandError(
                        "secret_prohibited", "Secrets cannot enter command jobs", 422
                    )
                for item in value.values():
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)

        check(arguments)
        if action == "access.invite" and arguments.get("roles") == ["viewer"]:
            return {"role": "access_admin", "danger_tier": 2}
        return cast(dict[str, Any], policies[action])

    def _resource_current(self, authority: dict[str, Any]) -> None:
        handler = self.handlers[authority["action"]]
        try:
            current = handler.current_resource_version(
                authority["action"],
                authority["arguments"],
                authority["target_refs"],
                authority["scope"],
            )
        except (ValueError, RuntimeError):
            raise CommandError(
                "resource_changed", "The reviewed resource or evidence is no longer available", 412
            ) from None
        if str(current) != authority["resource_version"]:
            raise CommandError("resource_changed", "Authoritative resources changed", 412)

    async def preview(
        self, request: dict[str, Any], principal: dict[str, Any], scope: dict[str, Any]
    ) -> dict[str, Any]:
        if set(request) - {
            "action",
            "arguments",
            "target_refs",
            "reason",
            "scope",
            "policy_version",
            "approval_reference",
        }:
            raise CommandError("invalid_request", "Unknown preview field", 422)
        action, arguments = request.get("action", ""), request.get("arguments", {})
        policy = self._policy(action, arguments)
        reason = request.get("reason", "")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise CommandError(
                "reason_required", "An explicit bounded preview reason is required", 422
            )
        reason = cast(str, safe_json(reason.strip()))
        if request.get("scope", scope) != scope:
            raise CommandError(
                "scope_denied", "Scope does not match the authenticated account", 403
            )
        if request.get("policy_version", POLICY_VERSION) != POLICY_VERSION:
            raise CommandError("policy_changed", "Refresh the action policy")
        targets = request.get("target_refs", [])
        if not isinstance(targets, list) or len(targets) > 100:
            raise CommandError("invalid_targets", "Select bounded exact targets", 422)
        for target in targets:
            if (
                not isinstance(target, dict)
                or set(target) - {"kind", "id", "resource_version"}
                or not all(
                    isinstance(target.get(k), str) and target[k]
                    for k in ("kind", "id", "resource_version")
                )
            ):
                raise CommandError(
                    "invalid_targets", "Targets require kind, id and resource_version", 422
                )
        with self.store.connection() as connection:
            self._principal(self.store.cursor(connection), principal, scope, policy["role"])
        try:
            planned = await self.handlers[action].preview(action, arguments, targets, scope)
        except CommandError:
            raise
        except (ValueError, RuntimeError):
            self.store.audit(
                action,
                "preview_refused",
                principal["id"],
                scope,
                evidence={"code": "adapter_refused"},
            )
            raise CommandError(
                "gate_closed", "The action adapter refused the requested scope or evidence"
            ) from None
        normalized = cast(dict[str, Any], safe_json(planned.get("normalized_arguments", arguments)))
        self._policy(action, normalized)
        now = datetime.now(UTC)
        preview_id = str(uuid4())
        tier = max(policy["danger_tier"], int(planned.get("danger_tier", 0)))
        if len(targets) > 1 and tier >= 2:
            tier = 3
        if tier == 3:
            owners = self.store.query(
                """SELECT p.id,p.scopes FROM console_principals p
                WHERE p.enabled AND p.roles @> '["owner"]'::jsonb
                AND EXISTS(SELECT 1 FROM console_credentials c WHERE c.principal_id=p.id)"""
            )
            eligible = any(
                row["id"] != principal["id"] and scope in row["scopes"] for row in owners
            )
            planned.setdefault("gates", []).append(
                {"name": "distinct_approver_enrolled", "verdict": "pass" if eligible else "fail"}
            )
        phrase = f"{action.upper()} {scope['environment']} {preview_id[:8]}" if tier >= 2 else ""
        preview: dict[str, Any] = {
            "id": preview_id,
            "actor_id": principal["id"],
            "action": action,
            "arguments": normalized,
            "target_refs": targets,
            "scope": scope,
            "resource_version": str(planned["resource_version"]),
            "danger_tier": tier,
            "policy_version": POLICY_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "reason": reason.strip(),
            "confirmation_phrase": phrase,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=60)).isoformat(),
            "effects": planned.get("effects", []),
            "gates": planned.get("gates", []),
            "warnings": planned.get("warnings", []),
            "approval_requirements": {
                "distinct_approver": tier == 3,
                "required_role": "owner" if tier == 3 else policy["role"],
            },
        }
        preview["preview_digest"] = digest(preview)
        with self.store.connection() as connection:
            cursor = self.store.cursor(connection)
            self._principal(cursor, principal, scope, policy["role"])
            cursor.execute(
                """INSERT INTO console_previews
                           (id,actor_id,session_id,action,scope,account_id,environment,
                           arguments,target_refs,resource_version,preview_digest,
                           confirmation_phrase,danger_tier,policy_version,
                           adapter_version,effects,gates,warnings,expires_at,reason)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                   %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    preview_id,
                    principal["id"],
                    principal["session_id"],
                    action,
                    Jsonb(scope),
                    scope["account_id"],
                    scope["environment"],
                    Jsonb(normalized),
                    Jsonb(targets),
                    preview["resource_version"],
                    preview["preview_digest"],
                    phrase,
                    tier,
                    POLICY_VERSION,
                    ADAPTER_VERSION,
                    Jsonb(preview["effects"]),
                    Jsonb(preview["gates"]),
                    Jsonb(preview["warnings"]),
                    timestamp(preview["expires_at"]),
                    reason.strip(),
                ),
            )
            self.store.audit(
                action,
                "previewed",
                principal["id"],
                scope,
                evidence={"preview_id": preview_id, "preview_digest": preview["preview_digest"]},
                cursor=cursor,
            )
        return preview

    @staticmethod
    def _version(request: dict[str, Any], expected: str | int) -> None:
        version = request.get("resource_version")
        if version is None:
            raise CommandError(
                "precondition_required", "If-Match resource version is required", 428
            )
        if str(version).strip('"') != str(expected):
            raise CommandError("resource_changed", "The resource version changed", 412)

    @staticmethod
    def _control(request: dict[str, Any]) -> None:
        if not isinstance(request, dict) or set(request) - {
            "preview_digest",
            "resource_version",
            "reason",
        }:
            raise CommandError("invalid_request", "Unknown command control field", 422)
        reason = request.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise CommandError("reason_required", "An explicit bounded reason is required", 422)

    def _grant(
        self, cursor: Any, principal: dict[str, Any], preview: dict[str, Any], job_id: str
    ) -> dict[str, Any]:
        cursor.execute(
            """SELECT * FROM console_step_up_grants WHERE principal_id=%s AND session_id=%s
                       AND preview_digest=%s AND action=%s AND scope=%s AND expires_at>now()
                       AND consumed_at IS NULL ORDER BY verified_at DESC FOR UPDATE LIMIT 1""",
            (
                principal["id"],
                principal["session_id"],
                preview["preview_digest"],
                preview["action"],
                Jsonb(preview["scope"]),
            ),
        )
        grant = cursor.fetchone()
        if not grant:
            raise CommandError(
                "step_up_required", "Verify a fresh passkey bound to this preview", 403
            )
        cursor.execute(
            "UPDATE console_step_up_grants SET consumed_at=now(),job_id=%s WHERE id=%s",
            (job_id, grant["id"]),
        )
        return cast(dict[str, Any], grant)

    def enqueue(
        self, request: dict[str, Any], principal: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        if set(request) - {
            "preview_id",
            "preview_digest",
            "confirmation_phrase",
            "reason",
            "approval_reference",
            "policy_version",
            "resource_version",
            "channel",
        }:
            raise CommandError("invalid_request", "Unknown enqueue field", 422)
        try:
            UUID(idempotency_key)
        except (ValueError, TypeError, AttributeError):
            raise CommandError(
                "precondition_required", "A UUID Idempotency-Key is required", 428
            ) from None
        payload_digest = digest(request)
        job_id = str(uuid4())
        with self.store.connection() as connection:
            cursor = self.store.cursor(connection)
            cursor.execute(
                "SELECT * FROM console_previews WHERE id=%s FOR UPDATE",
                (request.get("preview_id"),),
            )
            preview = cursor.fetchone()
            if not preview or preview["actor_id"] != principal["id"]:
                raise CommandError("not_found", "Preview not found", 404)
            policy = self._policy(preview["action"], preview["arguments"])
            self._principal(cursor, principal, preview["scope"], policy["role"])
            # Distinct previews can race on the same scoped key; serialize before
            # the receipt lookup so the loser returns conflict/replay, not SQL 500.
            key_scope = [
                principal["id"],
                preview["action"],
                preview["account_id"],
                preview["environment"],
                idempotency_key,
            ]
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (digest(key_scope),)
            )
            cursor.execute(
                """SELECT * FROM console_idempotency_receipts WHERE principal_id=%s AND action=%s
                           AND account_id=%s AND environment=%s AND idempotency_key=%s""",
                (
                    principal["id"],
                    preview["action"],
                    preview["account_id"],
                    preview["environment"],
                    idempotency_key,
                ),
            )
            receipt = cursor.fetchone()
            if receipt:
                if receipt["payload_digest"] != payload_digest:
                    raise CommandError(
                        "idempotency_conflict", "Idempotency key belongs to a different request"
                    )
                cursor.execute("SELECT * FROM console_jobs WHERE id=%s", (receipt["job_id"],))
                result = self.store.job_dto(cursor.fetchone())
                result["idempotency_replayed"] = True
                return result
            if preview["danger_tier"] < 2 and preview["session_id"] != principal["session_id"]:
                raise CommandError("session_expired", "Preview belongs to a different session", 401)
            self._version(request, preview["resource_version"])
            if request.get("preview_digest") != preview["preview_digest"]:
                raise CommandError("preview_changed", "Preview digest does not match", 412)
            if (
                request.get("policy_version") != POLICY_VERSION
                or preview["policy_version"] != POLICY_VERSION
            ):
                raise CommandError("policy_changed", "Refresh the action policy")
            if preview["expires_at"] <= datetime.now(UTC):
                raise CommandError("preview_expired", "Create a new preview")
            if preview["consumed_by"]:
                raise CommandError("preview_consumed", "This preview already authorized a job")
            self._resource_current(preview)
            if any(gate.get("verdict") != "pass" for gate in preview["gates"]):
                raise CommandError("gate_closed", "Required evidence or action gate does not pass")
            tier = preview["danger_tier"]
            reason = request.get("reason")
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
                raise CommandError("reason_required", "A bounded explicit reason is required", 422)
            reason = cast(str, safe_json(reason.strip()))
            if tier >= 2 and request.get("confirmation_phrase") != preview["confirmation_phrase"]:
                raise CommandError(
                    "confirmation_mismatch", "Type the exact preview confirmation phrase", 422
                )
            reference = request.get("approval_reference")
            if tier >= 2 and (
                not isinstance(reference, str) or not reference.strip() or len(reference) > 200
            ):
                raise CommandError(
                    "approval_reference_required",
                    "An explicit bounded approval reference is required",
                    422,
                )
            state = "awaiting_approval" if tier == 3 else "queued"
            cursor.execute(
                """INSERT INTO console_jobs
                           (id,preview_id,actor_id,session_id,channel,action,arguments,
                           target_refs,scope,account_id,environment,preview_digest,
                           policy_version,adapter_version,resource_version,
                           danger_tier,reason,approval_reference,state,authorization_expires_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                   %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           RETURNING *""",
                (
                    job_id,
                    preview["id"],
                    principal["id"],
                    principal["session_id"],
                    request.get("channel", "web"),
                    preview["action"],
                    Jsonb(preview["arguments"]),
                    Jsonb(preview["target_refs"]),
                    Jsonb(preview["scope"]),
                    preview["account_id"],
                    preview["environment"],
                    preview["preview_digest"],
                    POLICY_VERSION,
                    ADAPTER_VERSION,
                    preview["resource_version"],
                    tier,
                    reason,
                    safe_json(reference),
                    state,
                    min(preview["expires_at"], datetime.now(UTC) + timedelta(minutes=5)),
                ),
            )
            job = cursor.fetchone()
            if tier >= 2:
                self._grant(cursor, principal, preview, job_id)
            cursor.execute(
                "UPDATE console_previews SET consumed_by=%s WHERE id=%s", (job_id, preview["id"])
            )
            cursor.execute(
                """INSERT INTO console_idempotency_receipts
                           (principal_id,action,account_id,environment,idempotency_key,
                           payload_digest,job_id)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (
                    principal["id"],
                    job["action"],
                    job["account_id"],
                    job["environment"],
                    idempotency_key,
                    payload_digest,
                    job_id,
                ),
            )
            self.store._record_job(cursor, job, "enqueued")
            return self.store.job_dto(job)

    def _authorized_job(
        self,
        cursor: Any,
        job_id: str,
        principal: dict[str, Any],
        request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cursor.execute("SELECT * FROM console_jobs WHERE id=%s FOR UPDATE", (str(job_id),))
        job = cursor.fetchone()
        if not job:
            raise CommandError("not_found", "Job not found", 404)
        self._principal(cursor, principal, job["scope"])
        if job["actor_id"] != principal["id"]:
            self._principal(
                cursor,
                principal,
                job["scope"],
                self._policy(job["action"], job["arguments"])["role"],
            )
        if request is not None:
            self._version(request, job["revision"])
            if request.get("preview_digest") != job["preview_digest"]:
                raise CommandError("preview_changed", "Job digest does not match", 412)
        return cast(dict[str, Any], job)

    def get_job(self, job_id: str, principal: dict[str, Any]) -> dict[str, Any]:
        with self.store.connection() as connection:
            cursor = self.store.cursor(connection)
            job = self._authorized_job(cursor, job_id, principal)
            dto = self.store.job_dto(job)
            cursor.execute(
                "SELECT phase,state,sequence,evidence,created_at FROM console_job_steps "
                "WHERE job_id=%s ORDER BY sequence",
                (job["id"],),
            )
            dto["steps"] = safe_json(cursor.fetchall())
            cursor.execute(
                "SELECT effect_key,endpoint,stable_id,state,domain_refs,result,claimed_at,"
                "resolved_at FROM console_job_effects WHERE job_id=%s ORDER BY claimed_at,id",
                (job["id"],),
            )
            dto["effects"] = safe_json(cursor.fetchall())
            return dto

    def by_idempotency_key(
        self, key: str, principal: dict[str, Any], action: str, scope: dict[str, Any]
    ) -> dict[str, Any]:
        with self.store.connection() as connection:
            cursor = self.store.cursor(connection)
            self._principal(cursor, principal, scope)
            cursor.execute(
                """SELECT j.* FROM console_idempotency_receipts r
                           JOIN console_jobs j ON j.id=r.job_id
                           WHERE r.principal_id=%s AND r.action=%s
                           AND r.account_id=%s AND r.environment=%s
                           AND r.idempotency_key=%s""",
                (principal["id"], action, scope["account_id"], scope["environment"], key),
            )
            row = cursor.fetchone()
            if not row:
                raise CommandError("not_found", "Receipt not found", 404)
            return self.store.job_dto(row)

    def approve(
        self, job_id: str, request: dict[str, Any], principal: dict[str, Any]
    ) -> dict[str, Any]:
        return self._decision(job_id, request, principal, True)

    def reject(
        self, job_id: str, request: dict[str, Any], principal: dict[str, Any]
    ) -> dict[str, Any]:
        return self._decision(job_id, request, principal, False)

    def _decision(
        self, job_id: str, request: dict[str, Any], principal: dict[str, Any], approved: bool
    ) -> dict[str, Any]:
        self._control(request)
        with self.store.connection() as connection:
            cursor = self.store.cursor(connection)
            job = self._authorized_job(cursor, job_id, principal, request)
            self._principal(
                cursor,
                principal,
                job["scope"],
                "owner"
                if job["danger_tier"] == 3
                else self._policy(job["action"], job["arguments"])["role"],
            )
            if job["state"] not in {"awaiting_approval", "queued"} or job["effect_claimed"]:
                raise CommandError("not_approvable", "Only unclaimed queued approval can change")
            if job["danger_tier"] == 3 and job["actor_id"] == principal["id"]:
                raise CommandError(
                    "distinct_approver_required", "Tier 3 requires a different eligible owner", 403
                )
            reason = cast(str, safe_json(request.get("reason", "").strip()))
            if not reason or len(reason) > 1000:
                raise CommandError("reason_required", "An explicit bounded reason is required", 422)
            if job["policy_version"] != POLICY_VERSION:
                raise CommandError("policy_changed", "Refresh the policy and preview")
            if job["authorization_expires_at"] <= datetime.now(UTC):
                raise CommandError(
                    "preview_expired", "Create a new preview; expired consent cannot be refreshed"
                )
            self._resource_current(job)
            grant = self._grant(cursor, principal, job, job["id"])
            cursor.execute(
                """INSERT INTO console_approvals
                           (id,job_id,principal_id,session_id,preview_digest,policy_version,
                           resource_version,
                           decision,reason,step_up_at,expires_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    str(uuid4()),
                    job["id"],
                    principal["id"],
                    principal["session_id"],
                    job["preview_digest"],
                    job["policy_version"],
                    job["resource_version"],
                    "approved" if approved else "rejected",
                    reason,
                    grant["verified_at"],
                    min(grant["expires_at"], job["authorization_expires_at"]),
                ),
            )
            cursor.execute(
                """UPDATE console_jobs SET state=%s,revision=revision+1,updated_at=now()
                           WHERE id=%s RETURNING *""",
                ("queued" if approved else "rejected", job["id"]),
            )
            job = cursor.fetchone()
            self.store._record_job(cursor, job, "approved" if approved else "rejected")
            self.store.emit(
                cursor,
                "approval_changed",
                job["scope"],
                "job",
                job["id"],
                job["revision"],
                job["id"],
            )
            return self.store.job_dto(job)

    def cancel(
        self, job_id: str, request: dict[str, Any], principal: dict[str, Any]
    ) -> dict[str, Any]:
        self._control(request)
        with self.store.connection() as connection:
            cursor = self.store.cursor(connection)
            job = self._authorized_job(cursor, job_id, principal)
            if job["state"] == "cancelled":
                return self.store.job_dto(job)
            self._version(request, job["revision"])
            if request.get("preview_digest") != job["preview_digest"]:
                raise CommandError("preview_changed", "Job digest does not match", 412)
            if job["actor_id"] != principal["id"]:
                self._principal(cursor, principal, job["scope"], "owner")
            if job["state"] in TERMINAL_STATES:
                raise CommandError("not_cancellable", "Terminal work cannot be undone")
            before_claim = (
                job["state"] in {"queued", "awaiting_approval"} and not job["effect_claimed"]
            )
            cursor.execute(
                """UPDATE console_jobs
                           SET cancellation_requested_at=
                               coalesce(cancellation_requested_at,now()),
                           state=%s,revision=revision+1,updated_at=now() WHERE id=%s RETURNING *""",
                ("cancelled" if before_claim else job["state"], job["id"]),
            )
            updated = cursor.fetchone()
            self.store._record_job(
                cursor,
                updated,
                "cancelled_before_claim" if before_claim else "cancellation_requested",
            )
            return self.store.job_dto(updated)

    def reconcile(
        self, job_id: str, request: dict[str, Any], principal: dict[str, Any]
    ) -> dict[str, Any]:
        """Wake existing ambiguous readback, never clear a durable effect claim."""
        self._control(request)
        with self.store.connection() as connection:
            cursor = self.store.cursor(connection)
            job = self._authorized_job(cursor, job_id, principal, request)
            if job["state"] != "reconciliation_pending":
                raise CommandError("not_reconcilable", "Only pending readback may be resumed")
            if job["lease_until"] and job["lease_until"] > datetime.now(UTC) and job["lease_token"]:
                raise CommandError("resource_busy", "A readback worker already owns this job")
            cursor.execute(
                """UPDATE console_jobs SET lease_until=NULL,revision=revision+1,updated_at=now()
                           WHERE id=%s RETURNING *""",
                (job["id"],),
            )
            updated = cursor.fetchone()
            self.store._record_job(cursor, updated, "readback_requested")
            return self.store.job_dto(updated)

    def _authority(
        self, cursor: Any, job: dict[str, Any], *, consume_approval: bool = False
    ) -> None:
        """Exact durable consent plus current session/scope; no provider calls."""
        policy = self._policy(job["action"], job["arguments"])
        self._principal(
            cursor,
            {"id": job["actor_id"], "session_id": job["session_id"]},
            job["scope"],
            policy["role"],
        )
        if job["policy_version"] != POLICY_VERSION or job["adapter_version"] != ADAPTER_VERSION:
            raise CommandError("policy_changed", "Policy or adapter changed")
        cursor.execute("SELECT now() AS authority_now")
        now = cursor.fetchone()["authority_now"]
        starting = not job["effect_claimed"]
        if starting and job["authorization_expires_at"] <= now:
            raise CommandError("authorization_expired", "Approval and preview window expired")
        if job["danger_tier"] >= 2:
            cursor.execute(
                """SELECT * FROM console_step_up_grants WHERE principal_id=%s AND session_id=%s
                           AND job_id=%s AND preview_digest=%s AND action=%s AND scope=%s
                           AND consumed_at IS NOT NULL FOR SHARE""",
                (
                    job["actor_id"],
                    job["session_id"],
                    job["id"],
                    job["preview_digest"],
                    job["action"],
                    Jsonb(job["scope"]),
                ),
            )
            grant = cursor.fetchone()
            if not grant or (
                starting
                and (
                    grant["expires_at"] <= now or grant["verified_at"] < now - timedelta(minutes=5)
                )
            ):
                raise CommandError(
                    "step_up_required", "Initiator bound step-up is no longer current", 403
                )
        if job["danger_tier"] == 3:
            cursor.execute(
                """SELECT * FROM console_approvals WHERE job_id=%s AND decision='approved'
                           AND principal_id<>%s AND preview_digest=%s
                           AND policy_version=%s AND resource_version=%s
                           ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
                (
                    job["id"],
                    job["actor_id"],
                    job["preview_digest"],
                    job["policy_version"],
                    job["resource_version"],
                ),
            )
            approval = cursor.fetchone()
            if not approval or (
                starting
                and (
                    approval["expires_at"] <= now
                    or approval["step_up_at"] < now - timedelta(minutes=5)
                )
            ):
                raise CommandError(
                    "approval_expired", "A fresh distinct owner approval is required"
                )
            if not consume_approval and approval["consumed_at"] is None:
                raise CommandError(
                    "approval_expired", "Approval has not been consumed for execution"
                )
            self._principal(
                cursor,
                {"id": approval["principal_id"], "session_id": approval["session_id"]},
                job["scope"],
                "owner",
            )
            cursor.execute(
                """SELECT 1 FROM console_step_up_grants WHERE principal_id=%s AND session_id=%s
                           AND job_id=%s AND preview_digest=%s AND action=%s AND scope=%s
                           AND verified_at=%s AND consumed_at IS NOT NULL""",
                (
                    approval["principal_id"],
                    approval["session_id"],
                    job["id"],
                    job["preview_digest"],
                    job["action"],
                    Jsonb(job["scope"]),
                    approval["step_up_at"],
                ),
            )
            if not cursor.fetchone():
                raise CommandError("approval_expired", "Approver bound step-up evidence is missing")
            if consume_approval and approval["consumed_at"] is None:
                cursor.execute(
                    "UPDATE console_approvals SET consumed_at=now() WHERE id=%s", (approval["id"],)
                )

    async def validate_worker_start(self, job: dict[str, Any]) -> None:
        """Read prechecks first, then atomically consume exact fresh authority."""
        if job.get("readback_only") or job["effect_claimed"]:
            return
        fresh = await self.handlers[job["action"]].preview(
            job["action"], job["arguments"], job["target_refs"], job["scope"]
        )
        if str(fresh["resource_version"]) != job["resource_version"]:
            raise CommandError("resource_changed", "Authoritative resources changed", 412)
        if any(gate.get("verdict") != "pass" for gate in fresh.get("gates", [])):
            raise CommandError("gate_closed", "Fresh evidence or gate does not pass")
        with self.store.connection() as connection:
            cursor = self.store.cursor(connection)
            current = self.store.owned_job(cursor, job)
            if current["cancellation_requested_at"]:
                raise CommandError("cancelled", "Cancellation requested")
            self._authority(cursor, current, consume_approval=True)
