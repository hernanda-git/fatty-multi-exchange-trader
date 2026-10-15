"""Server-side, UV-required passkeys and scoped opaque console sessions."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import secrets
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from os import environ as os_environ
from typing import Any, TypeAlias, cast
from urllib.parse import urlsplit
from uuid import uuid4

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialCreationOptions,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialRequestOptions,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

JsonObject: TypeAlias = dict[str, Any]

ROLES = frozenset({"viewer", "operator", "risk_owner", "ops_admin", "access_admin", "owner"})
POLICY_VERSION = "console-auth-v1"


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 403) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def utcnow() -> datetime:
    return datetime.now(UTC)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def execute_one(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> JsonObject | None:
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(sql, params)
        return cast(JsonObject | None, cursor.fetchone()) if cursor.description else None


@dataclass(frozen=True)
class AuthConfig:
    origin: str
    rp_id: str
    secure: bool
    scope: JsonObject
    idle_seconds: int = 1800
    absolute_seconds: int = 43200
    stepup_seconds: int = 300
    challenge_seconds: int = 180
    invitation_seconds: int = 600

    @classmethod
    def from_environ(cls, values: Mapping[str, str]) -> AuthConfig:
        origin = values.get("CONSOLE_ORIGIN", "").strip()
        parsed = urlsplit(origin)
        # Accessing .port validates malformed/non-numeric origin ports as well.
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("CONSOLE_ORIGIN port is invalid")
        private = values.get("CONSOLE_ALLOW_LOCALHOST_HTTP", "0") == "1"
        local = parsed.hostname == "localhost" and parsed.scheme == "http" and private
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or (parsed.scheme != "https" and not local)
        ):
            raise ValueError(
                "CONSOLE_ORIGIN requires HTTPS or explicitly configured localhost HTTP"
            )
        rp_id = values.get("CONSOLE_RP_ID", parsed.hostname)
        if not rp_id or rp_id != parsed.hostname:
            raise ValueError("CONSOLE_RP_ID must match the configured origin hostname")
        account = values.get(
            "CONSOLE_ACCOUNT_ID", values.get("BITGET_ACCOUNT_ID", "configured-bitget")
        ).strip()
        if not account:
            raise ValueError("Console account scope is required")
        environment = values.get("BITGET_MODE", "LIVE").upper()
        if environment != "LIVE":
            raise ValueError("Console production scope is LIVE only")
        return cls(
            origin,
            rp_id,
            not local,
            {"account_id": account, "venue": "bitget", "environment": environment},
        )

    @property
    def cookie_name(self) -> str:
        # __Host- requires Secure. HTTP is only the explicitly trusted localhost tunnel.
        return "__Host-console_session" if self.secure else "console_session_local"


class AuthService:
    def __init__(self, store: Any, config: AuthConfig) -> None:
        self.store, self.config = store, config

    def rate_limit(self, client: str, operation: str, maximum: int = 20) -> None:
        row = self.store.one(
            """INSERT INTO console_auth_rates(bucket,window_start,attempts)
            VALUES (%s,now(),1) ON CONFLICT(bucket) DO UPDATE SET
            attempts=CASE WHEN console_auth_rates.window_start < now()-interval '5 minutes'
                THEN 1 ELSE console_auth_rates.attempts+1 END,
            window_start=CASE WHEN console_auth_rates.window_start < now()-interval '5 minutes'
                THEN now() ELSE console_auth_rates.window_start END RETURNING attempts""",
            (digest(client + ":" + operation),),
        )
        if row["attempts"] > maximum:
            raise AuthError("rate_limited", "Please wait before trying again", 429)

    def origin_check(self, origin: str | None, content_type: str) -> None:
        if origin != self.config.origin:
            raise AuthError("origin_rejected", "Request origin is not permitted")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise AuthError("json_required", "JSON content type is required", 415)

    def principal(self, token: str | None, *, touch: bool = True) -> JsonObject:
        if not token or len(token) > 256:
            raise AuthError("authentication_required", "Passkey authentication required", 401)
        row: JsonObject | None = self.store.one(
            """SELECT p.*,s.id AS session_id,s.csrf_token,
            s.authenticated_at,s.step_up_at,s.last_seen_at,s.expires_at,s.absolute_expires_at
            FROM console_sessions s JOIN console_principals p ON p.id=s.principal_id
            WHERE s.token_hash=%s AND s.revoked_at IS NULL AND p.enabled
            AND s.principal_revision=p.revision AND s.absolute_expires_at>now()
            AND s.expires_at>now() AND s.last_seen_at>now()-(%s * interval '1 second')""",
            (digest(token), self.config.idle_seconds),
        )
        if not row:
            raise AuthError("authentication_required", "Session expired or revoked", 401)
        if self.config.scope not in row["scopes"]:
            raise AuthError("scope_denied", "Account scope is not permitted")
        if touch:
            self.store.execute(
                "UPDATE console_sessions SET last_seen_at=now() WHERE token_hash=%s AND "
                "revoked_at IS NULL",
                (digest(token),),
            )
            row["last_seen_at"] = utcnow()
        return row

    def csrf_check(self, principal: JsonObject, token: str | None) -> None:
        if not token or not secrets.compare_digest(token, principal["csrf_token"]):
            raise AuthError("csrf_rejected", "Request verification failed")

    def require_role(self, principal: JsonObject, role: str) -> None:
        if role not in principal["roles"] and "owner" not in principal["roles"]:
            raise AuthError("role_denied", "Permission is not granted")

    def require_stepup(self, principal: JsonObject) -> None:
        when = principal.get("step_up_at")
        if isinstance(when, str):
            when = datetime.fromisoformat(when.replace("Z", "+00:00"))
        if not when or when < utcnow() - timedelta(seconds=self.config.stepup_seconds):
            raise AuthError("step_up_required", "Fresh passkey verification required", 401)

    def session_dto(self, principal: JsonObject) -> JsonObject:
        fields = (
            "id",
            "display_name",
            "roles",
            "enabled",
            "session_id",
            "csrf_token",
            "authenticated_at",
            "step_up_at",
        )
        from fatty_trader.console.commands import POLICY_VERSION as command_policy

        return {
            "principal": {k: principal[k] for k in fields},
            "csrf_token": principal["csrf_token"],
            "scope": self.config.scope,
            "scopes": principal["scopes"],
            "idle_expires_at": principal["last_seen_at"]
            + timedelta(seconds=self.config.idle_seconds),
            "absolute_expires_at": principal["absolute_expires_at"],
            "policy_version": command_policy,
            "auth_policy_version": POLICY_VERSION,
        }

    def _session(
        self, conn: Any, principal_id: str, *, stepped: bool = False, old_session: str | None = None
    ) -> str:
        now = utcnow()
        authenticated_at, absolute = now, now + timedelta(seconds=self.config.absolute_seconds)
        if old_session:
            old = execute_one(
                conn,
                """SELECT s.* FROM console_sessions s
                JOIN console_principals p ON p.id=s.principal_id
                WHERE s.id=%s AND s.principal_id=%s AND s.revoked_at IS NULL
                AND p.enabled AND s.principal_revision=p.revision
                AND s.expires_at>now() AND s.absolute_expires_at>now()
                AND s.last_seen_at>now()-(%s * interval '1 second')
                FOR UPDATE OF s,p""",
                (old_session, principal_id, self.config.idle_seconds),
            )
            if not old:
                raise AuthError("authentication_required", "Session expired or revoked", 401)
            authenticated_at, absolute = old["authenticated_at"], old["absolute_expires_at"]
            execute_one(
                conn, "UPDATE console_sessions SET revoked_at=now() WHERE id=%s", (old_session,)
            )
        owner = execute_one(
            conn,
            "SELECT revision,enabled FROM console_principals WHERE id=%s FOR UPDATE",
            (principal_id,),
        )
        if not owner or not owner["enabled"]:
            raise AuthError("authentication_required", "Account is disabled", 401)
        token = secrets.token_urlsafe(32)
        execute_one(
            conn,
            """INSERT INTO console_sessions
            (token_hash,id,principal_id,csrf_token,authenticated_at,step_up_at,last_seen_at,
             expires_at,absolute_expires_at,principal_revision)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                digest(token),
                str(uuid4()),
                principal_id,
                secrets.token_urlsafe(32),
                authenticated_at,
                now if stepped else None,
                now,
                absolute,
                absolute,
                owner["revision"],
            ),
        )
        return token

    def start(
        self,
        purpose: str,
        token: str | None = None,
        principal: JsonObject | None = None,
        binding: JsonObject | None = None,
    ) -> JsonObject:
        pid: str | None = None
        invitation_hash: str | None = None
        options: PublicKeyCredentialCreationOptions | PublicKeyCredentialRequestOptions
        if purpose == "bootstrap":
            invitation_hash = digest(token or "")
            invitation = self.store.one(
                """SELECT * FROM console_invitations WHERE token_hash=%s
                AND consumed_at IS NULL AND expires_at>now()""",
                (invitation_hash,),
            )
            if not invitation:
                raise AuthError(
                    "enrollment_rejected", "Enrollment token is invalid or expired", 401
                )
            pid = str(uuid4())
            options = generate_registration_options(
                rp_id=self.config.rp_id,
                rp_name="Fatty Console",
                user_id=pid.encode(),
                user_name=pid,
                user_display_name=invitation["display_name"],
                authenticator_selection=AuthenticatorSelectionCriteria(
                    resident_key=ResidentKeyRequirement.REQUIRED,
                    user_verification=UserVerificationRequirement.REQUIRED,
                ),
            )
        elif purpose in {"authenticate", "stepup"}:
            descriptors = []
            if purpose == "stepup":
                if not principal:
                    raise AuthError("authentication_required", "Authentication required", 401)
                pid = principal["id"]
                descriptors = [
                    PublicKeyCredentialDescriptor(id=base64url_to_bytes(row["id"]))
                    for row in self.store.query(
                        "SELECT id FROM console_credentials WHERE principal_id=%s", (pid,)
                    )
                ]
            options = generate_authentication_options(
                rp_id=self.config.rp_id,
                allow_credentials=descriptors,
                user_verification=UserVerificationRequirement.REQUIRED,
            )
        else:
            raise AuthError("invalid_request", "Invalid authentication ceremony", 400)
        challenge_id = str(uuid4())
        self.store.execute(
            """INSERT INTO console_auth_challenges
            (id,challenge,purpose,principal_id,session_id,invitation_hash,binding,expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,now()+(%s * interval '1 second'))""",
            (
                challenge_id,
                options.challenge,
                purpose,
                pid,
                principal["session_id"] if principal else None,
                invitation_hash,
                json.dumps(binding or {}),
                self.config.challenge_seconds,
            ),
        )
        return {"challenge_id": challenge_id, "options": json.loads(options_to_json(options))}

    def finish(
        self,
        purpose: str,
        challenge_id: str,
        credential: JsonObject,
        principal: JsonObject | None = None,
    ) -> str:
        # Lock challenge, invitation and credential through verification and session creation.
        # Failed verification rolls back; rate limiting separately bounds repeated attempts.
        try:
            client_data = json.loads(base64url_to_bytes(credential["response"]["clientDataJSON"]))
            if client_data.get("crossOrigin", False) is not False:
                raise ValueError("cross-origin passkey ceremony")
        except Exception:
            raise AuthError("authentication_rejected", "Passkey verification failed", 401) from None
        with self.store.connection() as conn:
            challenge = execute_one(
                conn,
                """SELECT * FROM console_auth_challenges WHERE id=%s
                AND purpose=%s AND consumed_at IS NULL AND expires_at>now() FOR UPDATE""",
                (challenge_id, purpose),
            )
            if not challenge or (
                purpose == "stepup"
                and (
                    not principal
                    or challenge["session_id"] != principal["session_id"]
                    or challenge["principal_id"] != principal["id"]
                )
            ):
                raise AuthError("authentication_rejected", "Passkey verification failed", 401)
            if purpose == "bootstrap":
                execute_one(
                    conn, "SELECT pg_advisory_xact_lock(hashtext('console-owner-enrollment'))"
                )
                invite = execute_one(
                    conn,
                    """SELECT * FROM console_invitations WHERE token_hash=%s
                    AND consumed_at IS NULL AND expires_at>now() FOR UPDATE""",
                    (challenge["invitation_hash"],),
                )
                if not invite:
                    raise AuthError(
                        "enrollment_rejected", "Enrollment token is invalid or expired", 401
                    )
                if invite["bootstrap"] and execute_one(
                    conn, "SELECT id FROM console_principals LIMIT 1"
                ):
                    raise AuthError("enrollment_rejected", "Initial owner is already enrolled", 409)
                try:
                    registration = verify_registration_response(
                        credential=credential,
                        expected_challenge=bytes(challenge["challenge"]),
                        expected_rp_id=self.config.rp_id,
                        expected_origin=self.config.origin,
                        require_user_verification=True,
                    )
                except Exception:
                    raise AuthError(
                        "authentication_rejected", "Passkey verification failed", 401
                    ) from None
                pid = challenge["principal_id"]
                execute_one(
                    conn,
                    "INSERT INTO console_principals(id,display_name,roles,scopes) VALUES "
                    "(%s,%s,%s::jsonb,%s::jsonb)",
                    (
                        pid,
                        invite["display_name"],
                        json.dumps(invite["roles"]),
                        json.dumps(invite["scopes"]),
                    ),
                )
                execute_one(
                    conn,
                    "INSERT INTO "
                    "console_credentials(id,principal_id,public_key,sign_count) VALUES "
                    "(%s,%s,%s,%s)",
                    (
                        bytes_to_base64url(registration.credential_id),
                        pid,
                        registration.credential_public_key,
                        registration.sign_count,
                    ),
                )
                subject = invite.get("telegram_subject")
                if subject:
                    existing = execute_one(
                        conn,
                        "SELECT principal_id FROM console_identity_links WHERE "
                        "issuer='telegram' AND subject=%s",
                        (subject,),
                    )
                    if existing:
                        raise AuthError(
                            "identity_already_enrolled",
                            "Recipient already has an enrolled identity",
                            409,
                        )
                    execute_one(
                        conn,
                        "INSERT INTO "
                        "console_identity_links(issuer,subject,principal_id,verified_at) "
                        "VALUES ('telegram',%s,%s,now())",
                        (subject, pid),
                    )
                execute_one(
                    conn,
                    "UPDATE console_invitations SET consumed_at=now() WHERE token_hash=%s",
                    (invite["token_hash"],),
                )
            else:
                record = execute_one(
                    conn,
                    (
                        "SELECT c.*,p.enabled FROM console_credentials c\n"
                        "                    JOIN console_principals p ON p.id=c.principal_id "
                        "WHERE c.id=%s FOR UPDATE OF c,p"
                    ),
                    (credential.get("id", ""),),
                )
                if (
                    not record
                    or not record["enabled"]
                    or (
                        purpose == "stepup"
                        and (principal is None or record["principal_id"] != principal["id"])
                    )
                ):
                    raise AuthError("authentication_rejected", "Passkey verification failed", 401)
                try:
                    authentication = verify_authentication_response(
                        credential=credential,
                        expected_challenge=bytes(challenge["challenge"]),
                        expected_rp_id=self.config.rp_id,
                        expected_origin=self.config.origin,
                        credential_public_key=bytes(record["public_key"]),
                        credential_current_sign_count=record["sign_count"],
                        require_user_verification=True,
                    )
                    user_handle = credential.get("response", {}).get("userHandle")
                    if (
                        user_handle
                        and base64url_to_bytes(user_handle) != record["principal_id"].encode()
                    ):
                        raise ValueError("user handle mismatch")
                except Exception:
                    raise AuthError(
                        "authentication_rejected", "Passkey verification failed", 401
                    ) from None
                pid = record["principal_id"]
                execute_one(
                    conn,
                    "UPDATE console_credentials SET sign_count=%s WHERE id=%s",
                    (authentication.new_sign_count, record["id"]),
                )
            execute_one(
                conn,
                "UPDATE console_auth_challenges SET consumed_at=now() WHERE id=%s",
                (challenge_id,),
            )
            session_token = self._session(
                conn,
                pid,
                stepped=purpose == "stepup",
                old_session=principal["session_id"] if principal else None,
            )
            binding = challenge.get("binding") or {}
            if purpose == "stepup" and binding:
                session = execute_one(
                    conn,
                    "SELECT id FROM console_sessions WHERE token_hash=%s",
                    (digest(session_token),),
                )
                if not session:
                    raise AuthError("authentication_rejected", "Session creation failed", 401)
                execute_one(
                    conn,
                    """INSERT INTO console_step_up_grants
                    (id,principal_id,session_id,preview_digest,action,scope,verified_at,expires_at)
                    VALUES (%s,%s,%s,%s,%s,%s::jsonb,now(),now()+(%s * interval '1 second'))""",
                    (
                        str(uuid4()),
                        pid,
                        session["id"],
                        binding["preview_digest"],
                        binding["action"],
                        json.dumps(binding["scope"]),
                        self.config.stepup_seconds,
                    ),
                )
            return session_token

    def revoke(self, principal: JsonObject, session_id: str) -> None:
        row = self.store.one("SELECT principal_id FROM console_sessions WHERE id=%s", (session_id,))
        if not row or (
            row["principal_id"] != principal["id"]
            and not {"access_admin", "owner"}.intersection(principal["roles"])
        ):
            raise AuthError("not_found", "Session not found", 404)
        if row["principal_id"] != principal["id"]:
            self.require_stepup(principal)
        self.store.execute(
            "UPDATE console_sessions SET revoked_at=now() WHERE id=%s", (session_id,)
        )

    async def issue_invitation(
        self,
        values: Mapping[str, str],
        display_name: str,
        roles: list[str],
        scopes: list[JsonObject],
        creator: JsonObject | None = None,
        invitation_id: str | None = None,
        approval_reference: str | None = None,
        recipient: str | None = None,
        job: JsonObject | None = None,
    ) -> JsonObject:
        if not display_name.strip() or len(display_name) > 100 or not roles or set(roles) - ROLES:
            raise AuthError("invalid_request", "Invalid account name or roles", 400)
        if scopes != [self.config.scope]:
            raise AuthError("scope_denied", "Only the configured account scope is permitted")
        if creator:
            self.require_role(creator, "access_admin")
            self.require_stepup(creator)
        if not creator and roles != ["owner"]:
            raise AuthError("role_denied", "Initial enrollment must create an owner")
        external = creator is None and approval_reference is not None
        if (
            external
            and approval_reference is not None
            and (not approval_reference.strip() or len(approval_reference) > 200)
        ):
            raise AuthError(
                "invalid_request",
                "A recorded SSH administrator approval reference is required",
                400,
            )
        owner_id = (recipient or "") if external else values.get("TG_OPERATOR_ID", "")
        bot_token = values.get("TG_BOT_TOKEN", "")
        if not owner_id.isdigit() or int(owner_id) <= 0 or not bot_token:
            raise AuthError(
                "owner_delivery_unavailable",
                "Private Telegram owner delivery is not configured",
                503,
            )
        token, invitation_id = secrets.token_urlsafe(32), invitation_id or str(uuid4())
        with self.store.connection() as conn:
            if job:
                self.effect_authority(conn, job)
            execute_one(conn, "SELECT pg_advisory_xact_lock(hashtext('console-owner-enrollment'))")
            if (
                not creator
                and not external
                and execute_one(conn, "SELECT id FROM console_principals LIMIT 1")
            ):
                raise AuthError("enrollment_rejected", "Initial owner is already enrolled", 409)
            if external:
                if not execute_one(
                    conn,
                    "SELECT id FROM console_principals WHERE enabled AND roles ? 'owner' LIMIT 1",
                ):
                    raise AuthError("enrollment_rejected", "Enroll the initial owner first", 409)
                if owner_id == values.get("TG_OPERATOR_ID") or execute_one(
                    conn,
                    "SELECT principal_id FROM console_identity_links WHERE "
                    "issuer='telegram' AND subject=%s",
                    (owner_id,),
                ):
                    raise AuthError(
                        "distinct_identity_required",
                        "Approver must be a distinct person and recipient",
                        409,
                    )
                if execute_one(
                    conn,
                    """SELECT id FROM console_invitations WHERE approval_reference IS NOT NULL
                    AND consumed_at IS NULL AND expires_at>now() LIMIT 1""",
                ):
                    raise AuthError(
                        "invitation_pending",
                        "An approver enrollment invitation is already pending",
                        409,
                    )
            elif not creator:
                execute_one(
                    conn,
                    "UPDATE console_invitations SET consumed_at=now() WHERE bootstrap "
                    "AND consumed_at IS NULL",
                )
            execute_one(
                conn,
                (
                    "INSERT INTO console_invitations\n"
                    "                "
                    "(token_hash,id,display_name,roles,scopes,created_by,expires_at,bootst"
                    "rap,telegram_subject,approval_reference)\n"
                    "                VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s,now()+(%s * "
                    "interval '1 second'),%s,%s,%s)"
                ),
                (
                    digest(token),
                    invitation_id,
                    display_name.strip(),
                    json.dumps(roles),
                    json.dumps(scopes),
                    creator["id"] if creator else None,
                    self.config.invitation_seconds,
                    creator is None and not external,
                    owner_id if creator is None else None,
                    approval_reference,
                ),
            )
        from fatty_trader.config.notifications import TelegramNotificationSettings
        from fatty_trader.notifications import TelegramBotSender

        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        try:
            with self.store.connection() if job else nullcontext(None) as delivery_conn:
                if job:
                    self.effect_authority(delivery_conn, job)
                await TelegramBotSender(
                    TelegramNotificationSettings(bot_token, int(owner_id))
                ).send(
                    "Console passkey enrollment for " + escape(display_name) + "\n"
                    "Origin: " + escape(self.config.origin) + "\n"
                    "Expires in 10 minutes. Enter this token in the enrollment form; never a URL.\n"
                    "<code>" + token + "</code>"
                )
        except Exception:
            self.store.execute(
                "UPDATE console_invitations SET consumed_at=now() WHERE id=%s", (invitation_id,)
            )
            raise AuthError(
                "owner_delivery_failed", "Owner enrollment delivery failed", 503
            ) from None
        self.store.audit(
            "access.ssh_approver_invitation"
            if external
            else "access.initial_owner_invitation"
            if creator is None
            else "access.invitation_delivered",
            "succeeded",
            actor_id=creator["id"] if creator else None,
            scope=self.config.scope,
            resource_id=invitation_id,
            evidence={"approval_reference": approval_reference} if external else {},
        )
        return {
            "id": invitation_id,
            "delivery": "private_telegram_owner",
            "expires_in_seconds": self.config.invitation_seconds,
        }

    def effect_authority(self, conn: Any, job: JsonObject) -> None:
        self.store.effect_authority(self.store.cursor(conn), job)

    def update_account(
        self,
        actor: JsonObject,
        principal_id: str,
        roles: list[str],
        enabled: bool,
        connection: Any = None,
    ) -> None:
        self.require_role(actor, "access_admin")
        self.require_stepup(actor)
        if not roles or set(roles) - ROLES:
            raise AuthError("invalid_request", "Invalid account roles", 400)
        with nullcontext(connection) if connection is not None else self.store.connection() as conn:
            execute_one(conn, "SELECT pg_advisory_xact_lock(hashtext('console-owner-enrollment'))")
            target = execute_one(
                conn, "SELECT * FROM console_principals WHERE id=%s FOR UPDATE", (principal_id,)
            )
            if not target or self.config.scope not in target["scopes"]:
                raise AuthError("not_found", "Account not found", 404)
            if (
                "owner" in target["roles"]
                and target["enabled"]
                and ("owner" not in roles or not enabled)
            ):
                others = execute_one(
                    conn,
                    "SELECT count(*) AS n FROM console_principals WHERE enabled AND "
                    "roles ? 'owner' AND id<>%s",
                    (principal_id,),
                )
                if not others or others["n"] == 0:
                    raise AuthError(
                        "last_owner", "The last enrolled owner cannot be disabled or demoted", 409
                    )
            if principal_id == actor["id"] and set(roles) - set(target["roles"]):
                raise AuthError("self_grant_denied", "Self-granting privilege is prohibited")
            execute_one(
                conn,
                "UPDATE console_principals SET "
                "roles=%s::jsonb,enabled=%s,revision=revision+1,updated_at=now() WHERE "
                "id=%s",
                (json.dumps(roles), enabled, principal_id),
            )
            execute_one(
                conn,
                "UPDATE console_sessions SET revoked_at=now() WHERE principal_id=%s AND "
                "revoked_at IS NULL",
                (principal_id,),
            )


class AuthHandlers:
    """Identity jobs share the same approval/effect fence as all other commands."""

    def __init__(self, environ: Mapping[str, str], store: Any) -> None:
        self.environ, self.store = environ, store
        self.auth = AuthService(store, AuthConfig.from_environ(environ))

    def current_resource_version(
        self, action: str, arguments: JsonObject, target_refs: list[JsonObject], scope: JsonObject
    ) -> str:
        if scope != self.auth.config.scope:
            raise AuthError("scope_denied", "Account scope is not permitted")
        if action == "access.invite":
            resource = self.store.query(
                "SELECT id,revision,enabled FROM console_principals WHERE scopes @> "
                "%s::jsonb ORDER BY id",
                (json.dumps([scope]),),
            )
        elif action in {"access.change_roles", "access.disable"}:
            resource = self.store.one(
                "SELECT id,roles,scopes,enabled,revision FROM console_principals WHERE id=%s",
                (arguments["principal_id"],),
            )
            if not resource or scope not in resource["scopes"]:
                raise AuthError("not_found", "Account not found", 404)
        elif action == "session.revoke":
            resource = self.store.one(
                """SELECT s.id,s.principal_id,s.revoked_at,p.scopes FROM console_sessions s
                JOIN console_principals p ON p.id=s.principal_id WHERE s.id=%s""",
                (arguments["session_id"],),
            )
            if not resource or scope not in resource["scopes"]:
                raise AuthError("not_found", "Session not found", 404)
        else:
            raise AuthError("invalid_request", "Identity action is not permitted", 400)
        from fatty_trader.console.store import digest as resource_digest

        return resource_digest(resource)

    async def preview(
        self, action: str, arguments: JsonObject, target_refs: list[JsonObject], scope: JsonObject
    ) -> JsonObject:
        return {
            "normalized_arguments": arguments,
            "resource_version": self.current_resource_version(
                action, arguments, target_refs, scope
            ),
            "effects": [
                {
                    "action": action,
                    "targets": target_refs,
                    "delivery": "private_telegram_owner" if action == "access.invite" else None,
                }
            ],
            "gates": [],
            "warnings": ["Identity changes revoke existing sessions and pending authority"]
            if action in {"access.change_roles", "access.disable"}
            else [],
        }

    def _actor(self, job: JsonObject) -> JsonObject:
        row: JsonObject | None = self.store.one(
            """SELECT p.*,s.id AS session_id,s.step_up_at,s.revoked_at,s.absolute_expires_at
            FROM console_principals p JOIN console_sessions s ON s.principal_id=p.id
            WHERE p.id=%s AND s.id=%s AND s.principal_revision=p.revision""",
            (job["actor_id"], job["session_id"]),
        )
        if (
            not row
            or not row["enabled"]
            or row["revoked_at"]
            or row["absolute_expires_at"] <= utcnow()
        ):
            raise AuthError(
                "authentication_required", "Requesting session is no longer active", 401
            )
        if job["scope"] not in row["scopes"]:
            raise AuthError("scope_denied", "Account scope is not permitted")
        self.auth.require_role(row, "access_admin")
        self.auth.require_stepup(row)
        return row

    async def execute(self, job: JsonObject) -> JsonObject:
        actor = self._actor(job)
        action, args = job["action"], job["arguments"]
        receipt = self.store.one(
            "SELECT * FROM console_operation_receipts WHERE job_id=%s", (job["id"],)
        )
        if receipt:
            return {"state": receipt["state"], "result": receipt["evidence"]}
        if action == "access.invite":
            result = await self.auth.issue_invitation(
                self.environ,
                args["display_name"],
                args["roles"],
                [job["scope"]],
                actor,
                invitation_id=job["id"],
                job=job,
            )
            self.store.execute(
                "INSERT INTO console_operation_receipts(job_id,action,state,evidence) "
                "VALUES (%s,%s,%s,%s::jsonb)",
                (job["id"], action, "succeeded", json.dumps(result)),
            )
        else:
            with self.store.connection() as conn:
                self.auth.effect_authority(conn, job)
                execute_one(
                    conn, "SELECT pg_advisory_xact_lock(hashtext('console-owner-enrollment'))"
                )
                if action in {"access.change_roles", "access.disable"}:
                    target = execute_one(
                        conn,
                        "SELECT roles,enabled FROM console_principals WHERE id=%s FOR UPDATE",
                        (args["principal_id"],),
                    )
                    if not target:
                        raise AuthError("not_found", "Account not found", 404)
                    self.auth.update_account(
                        actor,
                        args["principal_id"],
                        args["roles"] if action == "access.change_roles" else target["roles"],
                        target["enabled"] if action == "access.change_roles" else False,
                        connection=conn,
                    )
                    result = {"principal_id": args["principal_id"], "updated": True}
                elif action == "session.revoke":
                    target = execute_one(
                        conn,
                        (
                            "SELECT s.id,p.scopes FROM console_sessions s\n"
                            "                        JOIN console_principals p ON "
                            "p.id=s.principal_id WHERE s.id=%s FOR UPDATE OF s"
                        ),
                        (args["session_id"],),
                    )
                    if not target or job["scope"] not in target["scopes"]:
                        raise AuthError("not_found", "Session not found", 404)
                    execute_one(
                        conn,
                        "UPDATE console_sessions SET revoked_at=now() WHERE id=%s",
                        (args["session_id"],),
                    )
                    result = {"session_id": args["session_id"], "revoked": True}
                else:
                    raise AuthError("invalid_request", "Identity action is not permitted", 400)
                execute_one(
                    conn,
                    "INSERT INTO "
                    "console_operation_receipts(job_id,action,state,evidence) VALUES "
                    "(%s,%s,%s,%s::jsonb)",
                    (job["id"], action, "succeeded", json.dumps(result)),
                )
        return {"state": "succeeded", "result": result}

    async def reconcile(self, job: JsonObject) -> JsonObject:
        receipt = self.store.one(
            "SELECT * FROM console_operation_receipts WHERE job_id=%s", (job["id"],)
        )
        if receipt:
            return {"state": receipt["state"], "result": receipt["evidence"]}
        if job["action"] == "access.invite":
            invitation = self.store.one(
                "SELECT consumed_at,expires_at FROM console_invitations WHERE id=%s", (job["id"],)
            )
            if invitation and invitation["expires_at"] <= utcnow():
                return {
                    "state": "failed",
                    "result": {"code": "enrollment_expired", "delivery_confirmed": False},
                }
        return {
            "state": "reconciliation_pending",
            "result": {"code": "identity_effect_unconfirmed", "replay_allowed": False},
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Private passkey invitation delivery; requires trusted SSH administrator execution"
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bootstrap-owner", action="store_true")
    mode.add_argument("--issue-approver-invitation", action="store_true")
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--approval-reference")
    parser.add_argument("--recipient")
    args = parser.parse_args()
    if args.issue_approver_invitation and (not args.approval_reference or not args.recipient):
        parser.error("approver invitation requires --approval-reference and --recipient")
    if args.bootstrap_owner and (args.approval_reference or args.recipient):
        parser.error("initial owner delivery uses only configured TG_OPERATOR_ID")
    from fatty_trader.console.store import ConsoleStore

    try:
        service = AuthService(ConsoleStore(), AuthConfig.from_environ(os_environ))
        metadata = asyncio.run(
            service.issue_invitation(
                os_environ,
                args.display_name,
                ["owner"],
                [service.config.scope],
                approval_reference=args.approval_reference,
                recipient=args.recipient,
            )
        )
        print(json.dumps(metadata))
        return 0
    except Exception:
        print("Owner enrollment failed; check console configuration and private owner delivery.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
