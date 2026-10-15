"""Shared-password authentication and scoped opaque console sessions."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeAlias, cast
from urllib.parse import urlsplit
from uuid import uuid4

JsonObject: TypeAlias = dict[str, Any]
ROLES = frozenset({"viewer", "operator", "risk_owner", "ops_admin", "access_admin", "owner"})
POLICY_VERSION = "console-password-v2"
OWNER_ID = "console-password-owner"


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
    secure: bool
    scope: JsonObject
    password_hash: str = field(default="", repr=False)
    idle_seconds: int = 1800
    absolute_seconds: int = 43200
    consent_seconds: int = 300

    @classmethod
    def from_environ(cls, values: Mapping[str, str]) -> AuthConfig:
        origin = values.get("CONSOLE_ORIGIN", "").strip()
        parsed = urlsplit(origin)
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
        account = values.get(
            "CONSOLE_ACCOUNT_ID", values.get("BITGET_ACCOUNT_ID", "configured-bitget")
        ).strip()
        if not account:
            raise ValueError("Console account scope is required")
        environment = values.get("BITGET_MODE", "LIVE").upper()
        if environment != "LIVE":
            raise ValueError("Console production scope is LIVE only")
        encoded = values.get("CONSOLE_PASSWORD_HASH", "").strip()
        if not encoded and values.get("CONSOLE_PASSWORD_HASH_FILE"):
            try:
                encoded = (
                    Path(values["CONSOLE_PASSWORD_HASH_FILE"]).read_text(encoding="utf-8").strip()
                )
            except (OSError, ValueError):
                # No fallback password; workers do not need access to this file.
                encoded = ""
        return cls(
            origin,
            not local,
            {"account_id": account, "venue": "bitget", "environment": environment},
            encoded,
        )

    @property
    def cookie_name(self) -> str:
        return "__Host-console_session" if self.secure else "console_session_local"

    @property
    def password_revision(self) -> int:
        # A non-secret, stable epoch binds sessions to this hash and exact account scope.
        value = self.password_hash + json.dumps(self.scope, sort_keys=True)
        return int(digest(value)[:15], 16) + 1


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
            raise AuthError("authentication_required", "Password authentication required", 401)
        row: JsonObject | None = self.store.one(
            """SELECT p.*,s.id AS session_id,s.csrf_token,
            s.authenticated_at,s.step_up_at,s.last_seen_at,s.expires_at,s.absolute_expires_at
            FROM console_sessions s JOIN console_principals p ON p.id=s.principal_id
            WHERE s.token_hash=%s AND s.revoked_at IS NULL AND p.enabled AND p.id=%s
            AND s.principal_revision=p.revision AND p.revision=%s
            AND s.absolute_expires_at>now() AND s.expires_at>now()
            AND s.last_seen_at>now()-(%s * interval '1 second')""",
            (digest(token), OWNER_ID, self.config.password_revision, self.config.idle_seconds),
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
        self,
        conn: Any,
        principal_id: str,
        *,
        consented: bool = False,
        old_session: str | None = None,
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
                now if consented else None,
                now,
                absolute,
                absolute,
                owner["revision"],
            ),
        )
        return token

    def login(self, password: str, old_token: str | None = None) -> str:
        try:
            algorithm, n, r, p, salt, expected = self.config.password_hash.split("$")
            if (algorithm, n, r, p) != ("scrypt", "16384", "8", "1"):
                raise ValueError("unsupported password hash")
            salt_bytes, expected_bytes = bytes.fromhex(salt), bytes.fromhex(expected)
            if len(salt_bytes) != 16 or len(expected_bytes) != 32:
                raise ValueError("invalid password hash")
        except (ValueError, TypeError):
            raise AuthError(
                "authentication_unavailable", "Password login is not configured", 503
            ) from None
        if not isinstance(password, str) or not password or len(password) > 4096:
            raise AuthError("authentication_rejected", "Password verification failed", 401)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt_bytes, n=16384, r=8, p=1, dklen=32
        )
        if not secrets.compare_digest(actual, expected_bytes):
            raise AuthError("authentication_rejected", "Password verification failed", 401)
        with self.store.connection() as conn:
            execute_one(conn, "SELECT pg_advisory_xact_lock(hashtext('console-password-owner'))")
            execute_one(
                conn,
                """UPDATE console_sessions s SET revoked_at=now()
                FROM console_principals p WHERE p.id=%s AND s.principal_id=p.id
                AND s.revoked_at IS NULL
                AND (s.principal_revision<>%s OR s.principal_revision<>p.revision)""",
                (OWNER_ID, self.config.password_revision),
            )
            execute_one(
                conn,
                """INSERT INTO console_principals(id,display_name,roles,scopes,revision)
                VALUES (%s,'Console owner','["owner"]'::jsonb,%s::jsonb,%s)
                ON CONFLICT(id) DO UPDATE SET enabled=true,roles=EXCLUDED.roles,
                scopes=EXCLUDED.scopes,updated_at=now(),revision=EXCLUDED.revision""",
                (OWNER_ID, json.dumps([self.config.scope]), self.config.password_revision),
            )
            if old_token and len(old_token) <= 256:
                execute_one(
                    conn,
                    "UPDATE console_sessions SET revoked_at=now() WHERE token_hash=%s",
                    (digest(old_token),),
                )
            return self._session(conn, OWNER_ID)

    def consent(self, principal: JsonObject, binding: JsonObject) -> str:
        if principal["id"] != OWNER_ID:
            raise AuthError("authentication_required", "Password authentication required", 401)
        if binding.get("scope") != self.config.scope:
            raise AuthError("scope_denied", "Account scope is not permitted")
        with self.store.connection() as conn:
            # Recheck the immutable target while rotating and granting in one transaction.
            preview = execute_one(
                conn,
                """SELECT preview_digest,action,scope FROM console_previews
                WHERE preview_digest=%s AND actor_id=%s AND expires_at>now() FOR SHARE""",
                (binding.get("preview_digest"), principal["id"]),
            )
            if not preview or any(
                preview[k] != binding.get(k) for k in ("preview_digest", "action", "scope")
            ):
                raise AuthError("not_found", "Consent target not found or expired", 404)
            token = self._session(
                conn, OWNER_ID, consented=True, old_session=principal["session_id"]
            )
            session = execute_one(
                conn, "SELECT id FROM console_sessions WHERE token_hash=%s", (digest(token),)
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
                    OWNER_ID,
                    session["id"],
                    binding["preview_digest"],
                    binding["action"],
                    json.dumps(binding["scope"]),
                    self.config.consent_seconds,
                ),
            )
            return token

    def revoke(self, principal: JsonObject, session_id: str) -> None:
        row = self.store.one("SELECT principal_id FROM console_sessions WHERE id=%s", (session_id,))
        if not row or row["principal_id"] != principal["id"]:
            raise AuthError("not_found", "Session not found", 404)
        self.store.execute(
            "UPDATE console_sessions SET revoked_at=now() WHERE id=%s", (session_id,)
        )
