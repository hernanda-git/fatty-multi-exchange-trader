"""Same-origin authenticated control-plane API; provider execution stays in workers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal, TypeAlias
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from fatty_trader.console.auth import (
    POLICY_VERSION,
    ROLES,
    AuthConfig,
    AuthError,
    AuthService,
    utcnow,
)
from fatty_trader.web_push import read_public_key_file

JsonObject: TypeAlias = dict[str, Any]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConsentBody(Body):
    preview_digest: StrictStr | None = Field(default=None, max_length=128)
    job_id: StrictStr | None = Field(default=None, max_length=128)


class LoginBody(Body):
    password: StrictStr = Field(min_length=1, max_length=4096, repr=False)


class EmptyBody(Body):
    pass


class PreviewBody(Body):
    action: StrictStr = Field(max_length=100)
    arguments: JsonObject
    target_refs: list[JsonObject] = Field(default_factory=list, max_length=200)
    reason: StrictStr = Field(min_length=1, max_length=1000)
    scope: dict[str, str] | None = None
    policy_version: StrictStr | None = None


class JobBody(Body):
    preview_id: StrictStr
    preview_digest: StrictStr
    confirmation_phrase: StrictStr | None = None
    reason: StrictStr = Field(min_length=1, max_length=1000)
    policy_version: StrictStr
    resource_version: StrictStr | None = None
    approval_reference: StrictStr | None = None


class DecisionBody(Body):
    preview_digest: StrictStr
    resource_version: StrictStr | None = None
    reason: StrictStr = Field(min_length=1, max_length=1000)


class SecretBody(Body):
    kind: Literal["BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE", "TG_BOT_TOKEN"]
    value: StrictStr = Field(min_length=1, max_length=4096, repr=False)


class PushSubscriptionBody(Body):
    endpoint: StrictStr = Field(min_length=12, max_length=2048)
    p256dh: StrictStr = Field(min_length=16, max_length=256)
    auth: StrictStr = Field(min_length=8, max_length=128)
    user_agent: StrictStr | None = Field(default=None, max_length=200)


class PushRemoveBody(Body):
    id: StrictStr = Field(min_length=8, max_length=64)


def create_router(
    *, store: Any, commands: Any, reads: Any, auth: AuthService, environ: Mapping[str, str]
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    scope = auth.config.scope

    def current(request: Request) -> JsonObject:
        principal = auth.principal(request.cookies.get(auth.config.cookie_name))
        request.state.console_principal = principal
        return principal

    def mutation(request: Request, principal: JsonObject = Depends(current)) -> JsonObject:
        auth.origin_check(request.headers.get("origin"), request.headers.get("content-type", ""))
        auth.csrf_check(principal, request.headers.get("x-csrf-token"))
        auth.rate_limit(
            request.client.host if request.client else "unknown", "mutation:" + principal["id"]
        )
        return principal

    def scoped_read(principal: JsonObject, section: str) -> None:
        if section.split("/")[0] in {"audit", "approvals"}:
            if not set(principal["roles"]).intersection(
                {"owner", "risk_owner", "ops_admin", "access_admin"}
            ):
                raise AuthError("role_denied", "Permission is not granted")
        elif section.split("/")[0] in {"backups", "releases", "services", "config"}:
            auth.require_role(principal, "ops_admin")
        elif section.split("/")[0] == "access":
            auth.require_role(principal, "access_admin")

    def envelope(data: JsonObject, request: Request) -> JsonObject:
        return {**data, "api_version": "v1", "request_id": request.state.console_request_id}

    def audit(
        request: Request, action: str, principal: JsonObject, resource_id: str | None = None
    ) -> None:
        store.audit(
            action,
            "succeeded",
            actor_id=principal["id"],
            scope=scope,
            resource_id=resource_id,
            request_id=request.state.console_request_id,
        )

    def session_response(token: str, request: Request) -> JSONResponse:
        principal = auth.principal(token)
        response = JSONResponse(jsonable_encoder(envelope(auth.session_dto(principal), request)))
        response.set_cookie(
            auth.config.cookie_name,
            token,
            max_age=auth.config.absolute_seconds,
            secure=auth.config.secure,
            httponly=True,
            samesite="strict",
            path="/",
        )
        audit(request, "auth.session_created", principal)
        return response

    @router.get("/session")
    @router.get("/auth/session")
    def session(request: Request, principal: JsonObject = Depends(current)) -> JsonObject:
        return envelope(auth.session_dto(principal), request)

    @router.post("/auth/login")
    def login(body: LoginBody, request: Request) -> JSONResponse:
        auth.origin_check(request.headers.get("origin"), request.headers.get("content-type", ""))
        auth.rate_limit(request.client.host if request.client else "unknown", "password-login", 10)
        # A global durable bucket also bounds attempts across distributed clients.
        auth.rate_limit("console-password", "password-login-global", 50)
        return session_response(
            auth.login(body.password, request.cookies.get(auth.config.cookie_name)), request
        )

    @router.post("/auth/consent")
    def consent(
        body: ConsentBody, request: Request, principal: JsonObject = Depends(mutation)
    ) -> JSONResponse:
        if bool(body.preview_digest) == bool(body.job_id):
            raise AuthError("invalid_request", "Choose exactly one consent target", 400)
        if body.job_id:
            job = commands.get_job(body.job_id, principal)
            row = store.one(
                "SELECT actor_id,preview_digest,action,scope FROM console_previews WHERE id=%s",
                (job["preview_id"],),
            )
        else:
            row = store.one(
                "SELECT actor_id,preview_digest,action,scope FROM console_previews "
                "WHERE preview_digest=%s AND expires_at>now()",
                (body.preview_digest,),
            )
        if not row or row["scope"] != scope or row["actor_id"] != principal["id"]:
            raise AuthError("not_found", "Consent target not found", 404)
        binding = {k: row[k] for k in ("preview_digest", "action", "scope")}
        return session_response(auth.consent(principal, binding), request)

    @router.post("/auth/logout")
    def logout(
        body: EmptyBody, request: Request, principal: JsonObject = Depends(mutation)
    ) -> JSONResponse:
        auth.revoke(principal, principal["session_id"])
        audit(request, "auth.logout", principal)
        response = JSONResponse({"logged_out": True})
        response.delete_cookie(
            auth.config.cookie_name,
            path="/",
            secure=auth.config.secure,
            httponly=True,
            samesite="strict",
        )
        return response

    @router.get("/push/config")
    def push_config(request: Request, principal: JsonObject = Depends(current)) -> JsonObject:
        public_key = read_public_key_file(environ)
        return envelope(
            {
                "data": {"enabled": public_key is not None, "public_key": public_key},
                "scope": scope,
            },
            request,
        )

    @router.get("/push/subscriptions")
    def push_subscriptions(
        request: Request, principal: JsonObject = Depends(current)
    ) -> JsonObject:
        items = store.query(
            "SELECT id, user_agent, created_at, last_seen_at, disabled_at "
            "FROM web_push_subscriptions ORDER BY created_at DESC LIMIT 20"
        )
        enabled = store.one(
            "SELECT count(*) AS n FROM web_push_subscriptions WHERE disabled_at IS NULL"
        )
        return envelope(
            {
                "data": {
                    "items": items,
                    "enabled_count": int(enabled["n"]) if enabled else 0,
                },
                "scope": scope,
            },
            request,
        )

    @router.post("/push/subscriptions")
    def push_subscribe(
        body: PushSubscriptionBody, request: Request, principal: JsonObject = Depends(mutation)
    ) -> JsonObject:
        if not body.endpoint.startswith("https://"):
            raise AuthError("invalid_request", "Push endpoint must be HTTPS", 422)
        row = store.one(
            """INSERT INTO web_push_subscriptions (id, endpoint, p256dh, auth, user_agent)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (endpoint) DO UPDATE SET
                 p256dh = EXCLUDED.p256dh,
                 auth = EXCLUDED.auth,
                 user_agent = EXCLUDED.user_agent,
                 last_seen_at = now(),
                 disabled_at = NULL,
                 last_error = NULL
               RETURNING id""",
            (str(uuid4()), body.endpoint, body.p256dh, body.auth, body.user_agent),
        )
        if row is None:
            raise AuthError("unavailable", "Subscription could not be stored", 503)
        audit(request, "push.subscribe", principal, str(row["id"]))
        return envelope({"data": {"id": str(row["id"])}, "scope": scope}, request)

    @router.post("/push/subscriptions/remove")
    def push_remove(
        body: PushRemoveBody, request: Request, principal: JsonObject = Depends(mutation)
    ) -> JsonObject:
        row = store.one(
            "UPDATE web_push_subscriptions SET disabled_at = now() "
            "WHERE id = %s AND disabled_at IS NULL RETURNING id",
            (body.id,),
        )
        if row is None:
            raise AuthError("not_found", "Subscription not found", 404)
        audit(request, "push.unsubscribe", principal, body.id)
        return envelope({"data": {"id": body.id, "disabled": True}, "scope": scope}, request)

    @router.post("/push/test")
    def push_test(request: Request, principal: JsonObject = Depends(mutation)) -> JsonObject:
        dedup = f"push-test:{uuid4().hex[:16]}"
        store.execute(
            "INSERT INTO notifications_outbox (id, dedup_key, payload) VALUES (%s, %s, %s::jsonb) "
            "ON CONFLICT (dedup_key) DO NOTHING",
            (
                str(uuid4()),
                dedup,
                json.dumps(
                    {
                        "kind": "push-test",
                        "requested_by": principal["id"],
                        "requested_at": utcnow().isoformat(),
                    }
                ),
            ),
        )
        audit(request, "push.test", principal, dedup)
        return envelope({"data": {"queued": True, "dedup_key": dedup}, "scope": scope}, request)

    @router.get("/actions")
    def actions(request: Request, principal: JsonObject = Depends(current)) -> JsonObject:
        return envelope(
            {"data": {"items": commands.capabilities(principal, scope)}, "scope": scope}, request
        )

    @router.post("/previews")
    async def preview(
        body: PreviewBody, request: Request, principal: JsonObject = Depends(mutation)
    ) -> JsonObject:
        if body.scope is not None and body.scope != scope:
            raise AuthError("scope_denied", "Account scope is not permitted")
        result = await commands.preview(body.model_dump(exclude_none=True), principal, scope)
        return envelope(result, request)

    @router.get("/previews/{preview_id}")
    def get_preview(
        preview_id: str, request: Request, principal: JsonObject = Depends(current)
    ) -> JsonObject:
        row = store.one(
            """SELECT * FROM console_previews WHERE id=%s AND actor_id=%s
            AND account_id=%s AND environment=%s""",
            (preview_id, principal["id"], scope["account_id"], scope["environment"]),
        )
        if not row:
            raise AuthError("not_found", "Preview not found", 404)
        safe = {
            k: v
            for k, v in row.items()
            if k not in {"actor_id", "session_id", "account_id", "environment"}
        }
        safe["approval_requirements"] = {"distinct_approver": row["danger_tier"] == 3}
        return envelope(safe, request)

    @router.post("/jobs", status_code=202)
    def enqueue(
        body: JobBody,
        request: Request,
        response: Response,
        principal: JsonObject = Depends(mutation),
    ) -> JsonObject:
        key = request.headers.get("idempotency-key", "")
        if not key or len(key) > 200:
            raise AuthError("precondition_required", "An Idempotency-Key is required", 428)
        payload = body.model_dump(exclude_none=True)
        if request.headers.get("if-match"):
            payload["resource_version"] = request.headers["if-match"].strip('"')
        result = commands.enqueue(payload, principal, key)
        response.headers["Location"] = "/api/v1/jobs/" + str(result["id"])
        if result.get("idempotency_replayed"):
            response.headers["Idempotency-Replayed"] = "true"
        return envelope(result, request)

    @router.get("/jobs/by-idempotency/{key}")
    def receipt(
        key: str,
        request: Request,
        action: str = Query(min_length=1, max_length=100),
        principal: JsonObject = Depends(current),
    ) -> JsonObject:
        if not key or len(key) > 200:
            raise AuthError("invalid_request", "Invalid idempotency key", 400)
        return envelope(
            {"data": commands.by_idempotency_key(key, principal, action, scope), "scope": scope},
            request,
        )

    @router.get("/jobs/{job_id}")
    def get_job(
        job_id: str, request: Request, principal: JsonObject = Depends(current)
    ) -> JsonObject:
        return envelope({"data": commands.get_job(job_id, principal), "scope": scope}, request)

    @router.get("/exports")
    def exports(
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        cursor: str | None = None,
        principal: JsonObject = Depends(current),
    ) -> JsonObject:
        return envelope(
            reads.read(
                "jobs",
                filters={"action": "report.export", "actor_id": principal["id"]},
                limit=limit,
                cursor=cursor,
            ),
            request,
        )

    @router.post("/exports", status_code=202)
    def create_export(
        body: JobBody,
        request: Request,
        response: Response,
        principal: JsonObject = Depends(mutation),
    ) -> JsonObject:
        row = store.one(
            "SELECT action FROM console_previews WHERE id=%s AND actor_id=%s",
            (body.preview_id, principal["id"]),
        )
        if not row or row["action"] != "report.export":
            raise AuthError("not_found", "Report preview not found", 404)
        return enqueue(body, request, response, principal)

    @router.get("/exports/{export_id}")
    def export_detail(
        export_id: str, request: Request, principal: JsonObject = Depends(current)
    ) -> JsonObject:
        job = commands.get_job(export_id, principal)
        if job["action"] != "report.export" or job["actor_id"] != principal["id"]:
            raise AuthError("not_found", "Export not found", 404)
        return envelope({"data": job, "scope": scope}, request)

    @router.post("/jobs/{job_id}/{decision}")
    def decision(
        job_id: str,
        decision: Literal["approve", "reject", "cancel", "reconcile"],
        body: DecisionBody,
        request: Request,
        principal: JsonObject = Depends(mutation),
    ) -> JsonObject:
        payload = body.model_dump(exclude_none=True)
        if request.headers.get("if-match"):
            payload["resource_version"] = request.headers["if-match"].strip('"')
        return envelope(getattr(commands, decision)(job_id, payload, principal), request)

    @router.post("/secret-stages", status_code=201)
    def stage_secret(
        body: SecretBody, request: Request, principal: JsonObject = Depends(mutation)
    ) -> JsonObject:
        auth.require_role(principal, "owner")
        auth.rate_limit(principal["id"], "secret-stage", maximum=4)
        from fatty_trader.console.operations import OperationRefused, SecretStages

        try:
            result = SecretStages(environ).stage(
                body.kind, body.value, principal["id"], scope["account_id"]
            )
        except (OperationRefused, KeyError, OSError, ValueError):
            raise AuthError(
                "secret_stage_rejected", "Secret staging is unavailable or input was rejected", 400
            ) from None
        audit(request, "access.secret_stage", principal)
        return result

    @router.get("/access/principals")
    def principals(
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        cursor: str | None = None,
        principal: JsonObject = Depends(current),
    ) -> JsonObject:
        auth.require_role(principal, "access_admin")
        rows = store.query(
            """SELECT p.id,p.display_name,p.roles,p.scopes,p.enabled,p.created_at
            FROM console_principals p WHERE p.id=%s AND p.scopes @> %s::jsonb
            AND (%s::text IS NULL OR p.id>%s) ORDER BY p.id LIMIT %s""",
            (principal["id"], json.dumps([scope]), cursor, cursor, limit + 1),
        )
        page = rows[:limit]
        return envelope(
            {
                "data": {
                    "items": page,
                    "has_more": len(rows) > limit,
                    "next_cursor": page[-1]["id"] if len(rows) > limit else None,
                },
                "scope": scope,
            },
            request,
        )

    @router.get("/access/sessions")
    def sessions(
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        cursor: str | None = None,
        principal: JsonObject = Depends(current),
    ) -> JsonObject:
        # Shared-password access exposes only the current owner's sessions.
        rows = store.query(
            """SELECT s.id,s.principal_id,s.authenticated_at,s.step_up_at,
            s.last_seen_at,s.absolute_expires_at,s.revoked_at FROM console_sessions s
            JOIN console_principals p ON p.id=s.principal_id WHERE p.scopes @> %s::jsonb
            AND s.principal_id=%s AND (%s::text IS NULL OR s.id>%s)
            ORDER BY s.id LIMIT %s""",
            (json.dumps([scope]), principal["id"], cursor, cursor, limit + 1),
        )
        page = rows[:limit]
        return envelope(
            {
                "data": {
                    "items": page,
                    "has_more": len(rows) > limit,
                    "next_cursor": page[-1]["id"] if len(rows) > limit else None,
                },
                "scope": scope,
            },
            request,
        )

    @router.get("/access/policy")
    def policy(request: Request, principal: JsonObject = Depends(current)) -> JsonObject:
        return envelope(
            {
                "data": {
                    "policy_version": POLICY_VERSION,
                    "roles": sorted(ROLES),
                    "idle_seconds": auth.config.idle_seconds,
                    "absolute_seconds": auth.config.absolute_seconds,
                    "consent_seconds": auth.config.consent_seconds,
                    "authentication": "shared_password",
                },
                "scope": scope,
            },
            request,
        )

    @router.post("/access/sessions/{session_id}/revoke")
    def revoke(
        session_id: str,
        body: EmptyBody,
        request: Request,
        principal: JsonObject = Depends(mutation),
    ) -> JsonObject:
        target = store.one("SELECT principal_id FROM console_sessions WHERE id=%s", (session_id,))
        if not target or target["principal_id"] != principal["id"]:
            raise AuthError("not_found", "Session not found", 404)
        auth.revoke(principal, session_id)
        audit(request, "access.session_revoked", principal, session_id)
        return {"revoked": True}

    @router.get("/events")
    async def events(
        request: Request, principal: JsonObject = Depends(current)
    ) -> StreamingResponse:
        raw = request.headers.get("last-event-id") or request.query_params.get("cursor", "")
        try:
            after = (
                int(raw)
                if raw
                else (await run_in_threadpool(store.event_bounds, scope))["last"] or 0
            )
            if after < 0:
                raise ValueError
        except ValueError:
            raise AuthError("invalid_cursor", "Invalid event cursor", 400) from None
        token = request.cookies.get(auth.config.cookie_name)
        original_session = principal["session_id"]

        async def stream() -> AsyncIterator[str]:
            cursor = after
            while not await request.is_disconnected():
                try:
                    fresh = await run_in_threadpool(lambda: auth.principal(token, touch=False))
                    if fresh["session_id"] != original_session:
                        raise AuthError("session_revoked", "Session was replaced", 401)
                    bounds = await run_in_threadpool(store.event_bounds, scope)
                    if (bounds["first"] is not None and cursor < bounds["first"] - 1) or (
                        bounds["last"] is not None and cursor > bounds["last"]
                    ):
                        yield 'event: resync\ndata: {"reason":"cursor_gap"}\n\n'
                        return
                    rows = await run_in_threadpool(store.events, cursor, scope, 100)
                    for row in rows:
                        # Recheck before every delivered event, not just connection setup.
                        fresh = await run_in_threadpool(lambda: auth.principal(token, touch=False))
                        if fresh["session_id"] != original_session:
                            raise AuthError("session_revoked", "Session was replaced", 401)
                        kind = row["resource_type"]
                        if kind in {"job", "preview", "artifact"}:
                            from fatty_trader.console.commands import CommandError

                            try:
                                if kind == "job":
                                    await run_in_threadpool(
                                        commands.get_job, row["resource_id"], fresh
                                    )
                                elif kind == "preview":
                                    owned = await run_in_threadpool(
                                        store.one,
                                        "SELECT id FROM console_previews WHERE id=%s "
                                        "AND actor_id=%s AND account_id=%s AND environment=%s",
                                        (
                                            row["resource_id"],
                                            fresh["id"],
                                            scope["account_id"],
                                            scope["environment"],
                                        ),
                                    )
                                    if not owned:
                                        raise AuthError("not_found", "Resource not found", 404)
                                else:
                                    report = await run_in_threadpool(
                                        commands.get_job, row["resource_id"], fresh
                                    )
                                    if (
                                        report["actor_id"] != fresh["id"]
                                        or report["action"] != "report.export"
                                    ):
                                        raise AuthError("not_found", "Resource not found", 404)
                            except (AuthError, CommandError):
                                cursor = int(row["id"])
                                continue
                        if kind in {
                            "access",
                            "principal",
                            "session",
                            "audit",
                            "approval",
                            "backup",
                            "release",
                            "service",
                            "config",
                            "services",
                            "backups",
                            "releases",
                            "approvals",
                        }:
                            try:
                                scoped_read(
                                    fresh,
                                    {
                                        "backup": "backups",
                                        "release": "releases",
                                        "service": "services",
                                        "approval": "approvals",
                                    }.get(kind, kind),
                                )
                                if kind in {"access", "principal", "session"}:
                                    auth.require_role(fresh, "access_admin")
                            except AuthError:
                                cursor = int(row["id"])
                                continue
                        observation_section = {
                            "service_logs": "services",
                            "host": "services",
                            "host_effective_config": "config",
                            "effective_config": "config",
                            "notification_schedule": "notifications",
                        }.get(kind)
                        if observation_section:
                            try:
                                scoped_read(fresh, observation_section)
                            except AuthError:
                                cursor = int(row["id"])
                                continue
                        cursor = int(row["id"])
                        safe = {
                            k: row[k]
                            for k in (
                                "id",
                                "event",
                                "resource_type",
                                "resource_id",
                                "resource_version",
                                "schema_version",
                            )
                        }
                        yield (
                            f"id: {cursor}\nevent: change\ndata: "
                            f"{json.dumps(jsonable_encoder(safe), separators=(',', ':'))}\n\n"
                        )
                    yield f'event: heartbeat\ndata: {{"cursor":{cursor}}}\n\n'
                    await asyncio.sleep(5)
                except AuthError:
                    yield "event: session_revoked\ndata: {}\n\n"
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    yield 'event: resync\ndata: {"reason":"read_unavailable"}\n\n'
                    return

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @router.get("/signals/{message_id}/media/{media_id}")
    def media(
        message_id: str, media_id: str, request: Request, principal: JsonObject = Depends(current)
    ) -> Response:
        result = reads.read("media", media_id, {"message_id": message_id})
        data = result.get("data", result)
        content, mime = data.get("content", data.get("bytes")), data.get("mime_type")
        if (
            not isinstance(content, bytes)
            or mime not in {"image/png", "image/jpeg", "image/webp"}
            or len(content) > 20 * 1024 * 1024
        ):
            raise AuthError("not_found", "Media not found", 404)
        return Response(content, media_type=mime, headers={"Cache-Control": "no-store"})

    @router.get("/artifacts/{artifact_id}")
    @router.get("/jobs/{artifact_id}/download")
    @router.get("/exports/{artifact_id}/download")
    def artifact(
        artifact_id: str, request: Request, principal: JsonObject = Depends(current)
    ) -> Response:
        job = commands.get_job(artifact_id, principal)
        if job["action"] != "report.export" or job["actor_id"] != principal["id"]:
            raise AuthError("not_found", "Export artifact not found", 404)
        result = reads.read("artifacts", artifact_id)
        data = result.get("data", result)
        content = data.get("content", data.get("bytes"))
        if not isinstance(content, bytes):
            raise AuthError("not_found", "Artifact content unavailable", 404)
        audit(request, "artifact.download", principal, artifact_id)
        return Response(
            content,
            media_type=data.get("mime_type", "application/octet-stream"),
            headers={
                "Content-Disposition": 'attachment; filename="console-artifact"',
                "Cache-Control": "no-store",
            },
        )

    def read_endpoint(section: str, detail: bool = False) -> Callable[..., JsonObject]:
        def endpoint(
            request: Request,
            resource_id: str | None = None,
            limit: int = Query(50, ge=1, le=200),
            cursor: str | None = None,
            principal: JsonObject = Depends(current),
        ) -> JsonObject:
            scoped_read(principal, section)
            filters = dict(request.query_params)
            if section == "jobs" and principal["roles"] == ["viewer"]:
                filters["actor_id"] = principal["id"]
            for key in ("limit", "cursor"):
                filters.pop(key, None)
            for key in ("account_id", "venue", "environment"):
                if key in filters and filters[key] != scope[key]:
                    raise AuthError("scope_denied", "Account scope is not permitted")
            result = reads.read(section, resource_id if detail else None, filters, limit, cursor)
            if section == "jobs" and isinstance(result.get("data"), dict):
                from fatty_trader.console.commands import CommandError

                visible = []
                for item in result["data"].get("items") or []:
                    try:
                        commands.get_job(str(item["id"]), principal)
                    except (AuthError, CommandError):
                        continue
                    visible.append(item)
                if result["data"].get("items") is not None:
                    result["data"]["items"] = visible
                result["data"]["total"] = None
            return envelope(result, request)

        endpoint.__name__ = "read_" + section.replace("/", "_") + ("_detail" if detail else "")
        return endpoint

    # Register named nested resources before generic section details.
    for section in (
        "recovery/baselines",
        "account/admission-snapshots",
        "account/reservations",
        "account/pnl",
        "config/history",
        "config/drafts",
        "access",
        "access/principals",
        "access/sessions",
        "access/policy",
    ):
        router.add_api_route("/" + section, read_endpoint(section), methods=["GET"])
        if section in {"recovery/baselines", "config/drafts"}:
            router.add_api_route(
                "/" + section + "/{resource_id}", read_endpoint(section, True), methods=["GET"]
            )

    @router.get("/services/{resource_id}/logs")
    def logs(
        resource_id: str,
        request: Request,
        principal: JsonObject = Depends(current),
        limit: int = Query(50, ge=1, le=200),
        cursor: str | None = None,
    ) -> JsonObject:
        scoped_read(principal, "services")
        filters = {k: v for k, v in request.query_params.items() if k not in {"limit", "cursor"}}
        return envelope(reads.read("services/logs", resource_id, filters, limit, cursor), request)

    @router.get("/jobs/{resource_id}/steps")
    def steps(
        resource_id: str, request: Request, principal: JsonObject = Depends(current)
    ) -> JsonObject:
        commands.get_job(resource_id, principal)
        return envelope(reads.read("jobs/steps", resource_id), request)

    for section in (
        "overview",
        "signals",
        "dispatches",
        "positions",
        "orders",
        "fills",
        "protection",
        "management",
        "recovery",
        "account",
        "services",
        "config",
        "notifications",
        "jobs",
        "approvals",
        "audit",
        "backups",
        "releases",
    ):
        router.add_api_route("/" + section, read_endpoint(section), methods=["GET"])
        if section not in {"overview", "recovery", "account", "jobs"}:
            router.add_api_route(
                "/" + section + "/{resource_id}", read_endpoint(section, True), methods=["GET"]
            )
    return router


def mount_console(
    app: FastAPI,
    environ: Mapping[str, str],
    *,
    store: Any = None,
    commands: Any = None,
    reads: Any = None,
    auth: AuthService | None = None,
) -> None:
    if environ.get("CONSOLE_ENABLED", "0") != "1":
        return
    from fatty_trader.console.commands import CommandError, CommandService
    from fatty_trader.console.reads import ReadError, ReadService
    from fatty_trader.console.store import ConsoleStore

    store = store if store is not None else ConsoleStore()
    auth = auth if auth is not None else AuthService(store, AuthConfig.from_environ(environ))
    if commands is None:
        from fatty_trader.console.worker import build_preview_handlers

        commands = CommandService(store, build_preview_handlers(environ, store))
    reads = (
        reads
        if reads is not None
        else ReadService(
            store,
            scope=auth.config.scope,
            media_root=environ.get(
                "CONSOLE_MEDIA_ROOT", environ.get("TELEGRAM_MEDIA_ROOT", "/data/media")
            ),
        )
    )
    app.state.console_store, app.state.console_auth = store, auth
    app.state.console_commands, app.state.console_reads = commands, reads

    @app.middleware("http")
    async def console_security(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not request.url.path.startswith("/api/v1"):
            return await call_next(request)
        request.state.console_request_id = str(uuid4())
        response: Response
        too_large = (
            request.headers.get("content-length", "").isdigit()
            and int(request.headers["content-length"]) > 65536
        )
        if not too_large and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            body_buffer = bytearray()
            async for chunk in request.stream():
                if len(body_buffer) + len(chunk) > 65536:
                    too_large = True
                    break
                body_buffer.extend(chunk)
            if not too_large:
                # Starlette's cached middleware Request replays this bounded body downstream.
                request._body = bytes(body_buffer)
        if too_large:
            response = JSONResponse(
                {
                    "code": "request_too_large",
                    "message": "Request body is too large",
                    "request_id": request.state.console_request_id,
                },
                status_code=413,
            )
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Request-ID"] = request.state.console_request_id
        return response

    async def safe_error(request: Request, exc: Any) -> JSONResponse:
        actor = getattr(request.state, "console_principal", None)
        with suppress(Exception):
            await run_in_threadpool(
                lambda: store.audit(
                    "api.request",
                    "denied",
                    actor_id=actor["id"] if actor else None,
                    scope=auth.config.scope,
                    request_id=getattr(request.state, "console_request_id", None),
                    evidence={"code": exc.code},
                )
            )
        return JSONResponse(
            {
                "code": exc.code,
                "message": exc.message,
                "request_id": getattr(request.state, "console_request_id", str(uuid4())),
            },
            status_code=exc.status,
        )

    app.add_exception_handler(AuthError, safe_error)
    app.add_exception_handler(CommandError, safe_error)
    app.add_exception_handler(ReadError, safe_error)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if not request.url.path.startswith("/api/v1"):
            from fastapi.exception_handlers import http_exception_handler

            return await http_exception_handler(request, exc)
        return await safe_error(
            request,
            AuthError(
                "not_found" if exc.status_code == 404 else "request_rejected",
                "Resource not found" if exc.status_code == 404 else "Request rejected",
                exc.status_code,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        return await safe_error(
            request, AuthError("invalid_request", "Request fields are invalid", 422)
        )

    @app.exception_handler(Exception)
    async def unavailable(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            {
                "code": "service_unavailable",
                "message": "Requested data or service is unavailable",
                "request_id": getattr(request.state, "console_request_id", str(uuid4())),
            },
            status_code=503,
        )

    app.include_router(
        create_router(store=store, commands=commands, reads=reads, auth=auth, environ=environ)
    )
    static = Path(environ.get("CONSOLE_STATIC_DIR", "/app/console-static"))
    if not static.is_dir():
        static = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if static.is_dir():
        import mimetypes

        from fastapi.staticfiles import StaticFiles

        mimetypes.add_type("application/manifest+json", ".webmanifest")
        assets = static / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="console-assets")

        static_root = static.resolve()
        static_headers = {
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' blob: data:; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            ),
            "Referrer-Policy": "no-referrer",
        }

        @app.get("/{console_path:path}", include_in_schema=False)
        def spa(console_path: str) -> FileResponse:
            if console_path.startswith(("api/", "health")):
                raise AuthError("not_found", "Resource not found", 404)
            if console_path:
                candidate = (static_root / console_path).resolve()
                if candidate.is_file() and candidate.is_relative_to(static_root):
                    return FileResponse(candidate, headers=static_headers)
            return FileResponse(static_root / "index.html", headers=static_headers)
