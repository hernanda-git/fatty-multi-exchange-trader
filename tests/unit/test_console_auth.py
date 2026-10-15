"""Offline cryptographic passkey and console boundary regressions; no live endpoints."""

import hashlib
import json
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock

import cbor2
import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from webauthn.helpers import bytes_to_base64url

from fatty_trader.console.api import mount_console
from fatty_trader.console.auth import AuthConfig, AuthError, AuthService, digest, utcnow

ENV = {
    "CONSOLE_ENABLED": "1",
    "CONSOLE_ORIGIN": "http://localhost:18081",
    "CONSOLE_ALLOW_LOCALHOST_HTTP": "1",
    "CONSOLE_ACCOUNT_ID": "account-one",
    "BITGET_MODE": "LIVE",
}
SCOPE = {"account_id": "account-one", "venue": "bitget", "environment": "LIVE"}


@pytest.mark.parametrize(
    "origin,allow",
    [
        ("http://example.com", "1"),
        ("http://127.0.0.1:18081", "1"),
        ("http://localhost:18081", "0"),
        ("https://example.com/path", "0"),
        ("https://user@example.com", "0"),
        ("https://example.com?key=value", "0"),
    ],
)
def test_public_http_and_noncanonical_origins_fail_closed(origin, allow):
    with pytest.raises(ValueError):
        AuthConfig.from_environ(
            {**ENV, "CONSOLE_ORIGIN": origin, "CONSOLE_ALLOW_LOCALHOST_HTTP": allow}
        )


def test_secure_cookie_and_localhost_cookie_names():
    assert AuthConfig.from_environ(ENV).cookie_name == "console_session_local"
    public = AuthConfig.from_environ({**ENV, "CONSOLE_ORIGIN": "https://console.example.com"})
    assert public.secure and public.cookie_name == "__Host-console_session"


def test_csrf_origin_role_stepup_and_missing_sessions():
    service = AuthService(Mock(), AuthConfig.from_environ(ENV))
    with pytest.raises(AuthError):
        service.origin_check("https://evil.example", "application/json")
    with pytest.raises(AuthError):
        service.origin_check(ENV["CONSOLE_ORIGIN"], "text/plain")
    with pytest.raises(AuthError):
        service.csrf_check({"csrf_token": "server-token"}, "other-token")
    with pytest.raises(AuthError):
        service.require_role({"roles": ["ops_admin"]}, "operator")
    with pytest.raises(AuthError):
        service.require_stepup({"step_up_at": utcnow() - timedelta(seconds=301)})
    with pytest.raises(AuthError):
        service.principal(None)
    assert digest("opaque-secret") != "opaque-secret"


def test_discoverable_auth_options_require_user_verification():
    store = Mock()
    service = AuthService(store, AuthConfig.from_environ(ENV))
    result = service.start("authenticate")
    assert result["options"]["userVerification"] == "required"
    assert result["options"]["rpId"] == "localhost"
    assert "challenge" in result["options"]
    assert store.execute.call_args.args[1][1] != b""


def test_enrollment_options_require_discoverable_uv_passkey():
    store = Mock()
    store.one.return_value = {"display_name": "Invited operator"}
    service = AuthService(store, AuthConfig.from_environ(ENV))
    options = service.start("bootstrap", "one-use-token")["options"]
    assert options["authenticatorSelection"]["residentKey"] == "required"
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["user"]["name"] != "Invited operator"
    assert store.one.call_args.args[1] == (digest("one-use-token"),)


def assertion(private_key, challenge, *, flags=5, origin="http://localhost:18081", count=1):
    client = json.dumps(
        {
            "type": "webauthn.get",
            "challenge": bytes_to_base64url(challenge),
            "origin": origin,
            "crossOrigin": False,
        },
        separators=(",", ":"),
    ).encode()
    authenticator = (
        hashlib.sha256(b"localhost").digest() + bytes([flags]) + count.to_bytes(4, "big")
    )
    signature = private_key.sign(
        authenticator + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256())
    )
    return {
        "id": bytes_to_base64url(b"credential-test"),
        "rawId": bytes_to_base64url(b"credential-test"),
        "type": "public-key",
        "response": {
            "clientDataJSON": bytes_to_base64url(client),
            "authenticatorData": bytes_to_base64url(authenticator),
            "signature": bytes_to_base64url(signature),
            "userHandle": bytes_to_base64url(b"principal-test"),
        },
    }


@pytest.mark.parametrize(
    "flags,origin,challenge_ok,succeeds",
    [
        (5, "http://localhost:18081", True, True),
        (1, "http://localhost:18081", True, False),
        (5, "https://evil.example", True, False),
        (5, "http://localhost:18081", False, False),
    ],
)
def test_real_signed_authentication_verifies_uv_origin_challenge(
    monkeypatch, flags, origin, challenge_ok, succeeds
):
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_numbers()
    cose = cbor2.dumps(
        {1: 2, 3: -7, -1: 1, -2: public.x.to_bytes(32, "big"), -3: public.y.to_bytes(32, "big")}
    )
    challenge = b"a real server-generated challenge"
    store = Mock()

    @contextmanager
    def connection():
        yield object()

    store.connection = connection
    records = iter(
        [
            {"challenge": challenge, "purpose": "authenticate", "binding": {}},
            {
                "id": bytes_to_base64url(b"credential-test"),
                "principal_id": "principal-test",
                "public_key": cose,
                "sign_count": 0,
                "enabled": True,
            },
        ]
    )

    def execute_one(conn, sql, params=()):
        if sql.lstrip().startswith("SELECT"):
            return next(records)
        return None

    monkeypatch.setattr("fatty_trader.console.auth.execute_one", execute_one)
    service = AuthService(store, AuthConfig.from_environ(ENV))
    service._session = Mock(return_value="opaque-server-session")
    credential = assertion(key, challenge if challenge_ok else b"wrong", flags=flags, origin=origin)
    if succeeds:
        assert service.finish("authenticate", "challenge-id", credential) == "opaque-server-session"
    else:
        with pytest.raises(AuthError, match="Passkey verification failed"):
            service.finish("authenticate", "challenge-id", credential)
        service._session.assert_not_called()


@pytest.mark.parametrize("flags,succeeds", [(0x45, True), (0x41, False)])
def test_real_none_attestation_enrollment_requires_uv(monkeypatch, flags, succeeds):
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_numbers()
    cose = cbor2.dumps(
        {1: 2, 3: -7, -1: 1, -2: public.x.to_bytes(32, "big"), -3: public.y.to_bytes(32, "big")}
    )
    challenge, cid = b"registration challenge", b"unique-registration-credential"
    client = json.dumps(
        {
            "type": "webauthn.create",
            "challenge": bytes_to_base64url(challenge),
            "origin": ENV["CONSOLE_ORIGIN"],
            "crossOrigin": False,
        }
    ).encode()
    authenticator = (
        hashlib.sha256(b"localhost").digest()
        + bytes([flags])
        + bytes(4)
        + bytes(16)
        + len(cid).to_bytes(2, "big")
        + cid
        + cose
    )
    attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": authenticator})
    credential = {
        "id": bytes_to_base64url(cid),
        "rawId": bytes_to_base64url(cid),
        "type": "public-key",
        "response": {
            "clientDataJSON": bytes_to_base64url(client),
            "attestationObject": bytes_to_base64url(attestation),
            "transports": ["internal"],
        },
    }
    store = Mock()

    @contextmanager
    def connection():
        yield object()

    store.connection = connection
    writes = []

    def execute_one(conn, sql, params=()):
        if "FROM console_auth_challenges" in sql:
            return {
                "challenge": challenge,
                "principal_id": "invited-principal",
                "invitation_hash": "token-hash",
                "binding": {},
            }
        if "FROM console_invitations" in sql:
            return {
                "token_hash": "token-hash",
                "bootstrap": True,
                "display_name": "Owner",
                "roles": ["owner"],
                "scopes": [SCOPE],
            }
        if sql.startswith("INSERT"):
            writes.append((sql, params))
        return None

    monkeypatch.setattr("fatty_trader.console.auth.execute_one", execute_one)
    service = AuthService(store, AuthConfig.from_environ(ENV))
    service._session = Mock(return_value="session-secret")
    if succeeds:
        assert service.finish("bootstrap", "challenge-id", credential) == "session-secret"
        assert any("console_credentials" in sql for sql, params in writes)
    else:
        with pytest.raises(AuthError, match="Passkey verification failed"):
            service.finish("bootstrap", "challenge-id", credential)
        assert not writes


def test_used_or_expired_challenge_never_creates_session(monkeypatch):
    store = Mock()

    @contextmanager
    def connection():
        yield object()

    store.connection = connection
    monkeypatch.setattr("fatty_trader.console.auth.execute_one", lambda *args: None)
    service = AuthService(store, AuthConfig.from_environ(ENV))
    service._session = Mock()
    credential = {
        "response": {
            "clientDataJSON": bytes_to_base64url(json.dumps({"crossOrigin": False}).encode())
        }
    }
    with pytest.raises(AuthError):
        service.finish("authenticate", "already-used-challenge", credential)
    service._session.assert_not_called()


class BoundaryStore:
    def __init__(self):
        self.active = True
        self.audits = []

    def one(self, sql, params=()):
        if "console_auth_rates" in sql:
            return {"attempts": 1}
        if "console_sessions s JOIN console_principals" in sql:
            if not self.active:
                return None
            now = utcnow()
            return {
                "id": "principal",
                "display_name": "Real invited user",
                "enabled": True,
                "roles": ["viewer"],
                "scopes": [SCOPE],
                "session_id": "session",
                "csrf_token": "csrf",
                "authenticated_at": now,
                "step_up_at": None,
                "last_seen_at": now,
                "expires_at": now + timedelta(hours=12),
                "absolute_expires_at": now + timedelta(hours=12),
            }
        return None

    def execute(self, sql, params=()):
        pass

    def audit(self, *args, **kwargs):
        self.audits.append((args, kwargs))


@pytest.mark.asyncio
async def test_api_denies_csrf_role_and_revoked_session_sanitizes_validation():
    store = BoundaryStore()
    commands = Mock()
    reads = Mock()
    reads.read.return_value = {
        "data": {"items": []},
        "evidence": [],
        "warnings": [],
        "scope": SCOPE,
    }
    app = FastAPI()
    mount_console(app, ENV, store=store, commands=commands, reads=reads)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ENV["CONSOLE_ORIGIN"]
    ) as client:
        client.cookies.set("console_session_local", "opaque-token")
        result = await client.get("/api/v1/auth/session")
        assert result.status_code == 200 and result.json()["csrf_token"] == "csrf"
        denied = await client.post(
            "/api/v1/previews",
            json={"action": "trade.open", "arguments": {}, "reason": "reason"},
            headers={"Origin": ENV["CONSOLE_ORIGIN"]},
        )
        assert denied.status_code == 403 and denied.json()["code"] == "csrf_rejected"
        commands.preview.assert_not_called()
        role = await client.get("/api/v1/access/principals")
        assert role.status_code == 403
        invalid = await client.post(
            "/api/v1/secret-stages",
            json={"kind": "TG_BOT_TOKEN", "value": "SUPERSECRET", "unknown": "SUPERSECRET"},
            headers={"Origin": ENV["CONSOLE_ORIGIN"], "X-CSRF-Token": "csrf"},
        )
        assert "SUPERSECRET" not in invalid.text
        store.active = False
        revoked = await client.get("/api/v1/overview")
        assert revoked.status_code == 401
        assert store.audits


@pytest.mark.asyncio
async def test_bootstrap_failure_never_returns_or_persists_plaintext_token(monkeypatch):
    store = Mock()

    @contextmanager
    def connection():
        yield object()

    store.connection = connection
    captured = []

    def execute_one(conn, sql, params=()):
        captured.append((sql, params))
        return None

    monkeypatch.setattr("fatty_trader.console.auth.execute_one", execute_one)

    async def failed_send(self, text):
        raise RuntimeError("transport includes secret body")

    monkeypatch.setattr("fatty_trader.notifications.TelegramBotSender.send", failed_send)
    monkeypatch.setattr(
        "fatty_trader.console.auth.secrets.token_urlsafe", lambda _: "ONE_TIME_SECRET"
    )
    service = AuthService(store, AuthConfig.from_environ(ENV))
    with pytest.raises(AuthError, match="Owner enrollment delivery failed") as error:
        await service.issue_invitation(
            {**ENV, "TG_OPERATOR_ID": "1234", "TG_BOT_TOKEN": "bot-secret"},
            "Owner",
            ["owner"],
            [SCOPE],
        )
    assert "ONE_TIME_SECRET" not in str(error.value)
    assert all("ONE_TIME_SECRET" not in repr(params) for sql, params in captured)
    assert digest("ONE_TIME_SECRET") in repr(captured)
    assert store.execute.called


@pytest.mark.asyncio
async def test_sse_revalidates_session_before_any_event():
    class RevokedStore(BoundaryStore):
        calls = 0

        def one(self, sql, params=()):
            if "console_sessions s JOIN console_principals" in sql:
                self.calls += 1
                if self.calls > 1:
                    self.active = False
            return super().one(sql, params)

        def event_bounds(self, scope):
            return {"first": None, "last": None}

    store = RevokedStore()
    app = FastAPI()
    mount_console(app, ENV, store=store, commands=Mock(), reads=Mock())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ENV["CONSOLE_ORIGIN"]
    ) as client:
        client.cookies.set("console_session_local", "opaque-token")
        result = await client.get("/api/v1/events")
        assert result.status_code == 200
        assert "event: session_revoked" in result.text
        assert "event: change" not in result.text


def test_session_rotation_preserves_absolute_deadline_and_auth_time(monkeypatch):
    now = utcnow()
    old_auth, deadline = now - timedelta(hours=11), now + timedelta(hours=1)
    captured = []

    def execute_one(conn, sql, params=()):
        if "SELECT s.* FROM console_sessions" in sql:
            return {"authenticated_at": old_auth, "absolute_expires_at": deadline}
        if "SELECT revision,enabled" in sql:
            return {"revision": 3, "enabled": True}
        captured.append((sql, params))
        return None

    monkeypatch.setattr("fatty_trader.console.auth.execute_one", execute_one)
    service = AuthService(Mock(), AuthConfig.from_environ(ENV))
    token = service._session(object(), "principal", stepped=True, old_session="old-session")
    inserted = next(params for sql, params in captured if "INSERT INTO console_sessions" in sql)
    assert inserted[0] == digest(token) and token not in inserted
    assert inserted[4] == old_auth
    assert inserted[7] == deadline and inserted[8] == deadline
    assert inserted[9] == 3
    assert any("revoked_at=now()" in sql for sql, params in captured)
    rotation_sql, rotation_params = next(
        (sql, params) for sql, params in captured if "revoked_at=now()" in sql
    )
    assert rotation_params == ("old-session",)


def test_last_owner_and_self_grant_denied(monkeypatch):
    store = Mock()

    @contextmanager
    def connection():
        yield object()

    store.connection = connection

    def execute_one(conn, sql, params=()):
        if "SELECT * FROM console_principals" in sql:
            return {"id": "owner", "roles": ["owner"], "enabled": True, "scopes": [SCOPE]}
        if "SELECT count(*)" in sql:
            return {"n": 0}
        return None

    monkeypatch.setattr("fatty_trader.console.auth.execute_one", execute_one)
    service = AuthService(store, AuthConfig.from_environ(ENV))
    actor = {"id": "owner", "roles": ["owner"], "step_up_at": utcnow()}
    with pytest.raises(AuthError, match="last enrolled owner"):
        service.update_account(actor, "owner", ["viewer"], False)
    with pytest.raises(AuthError, match="Self-granting"):
        service.update_account(actor, "owner", ["owner", "operator"], True)


def test_rate_limit_fails_closed_and_scope_never_inferred():
    store = Mock()
    store.one.return_value = {"attempts": 21}
    service = AuthService(store, AuthConfig.from_environ(ENV))
    with pytest.raises(AuthError) as limit:
        service.rate_limit("127.0.0.1", "authenticate")
    assert limit.value.status == 429
    store.one.return_value = {"scopes": [{**SCOPE, "account_id": "different-account"}]}
    with pytest.raises(AuthError) as mismatch:
        service.principal("opaque-token")
    assert mismatch.value.code == "scope_denied"


@pytest.mark.asyncio
async def test_chunked_body_limit_and_secret_validation_redaction():
    store = BoundaryStore()
    app = FastAPI()
    mount_console(app, ENV, store=store, commands=Mock(), reads=Mock())

    async def chunks():
        yield b'{"value":"'
        yield b"A" * 65536
        yield b'"}'

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ENV["CONSOLE_ORIGIN"]
    ) as client:
        client.cookies.set("console_session_local", "opaque-token")
        headers = {
            "Origin": ENV["CONSOLE_ORIGIN"],
            "X-CSRF-Token": "csrf",
            "Content-Type": "application/json",
        }
        oversized = await client.post("/api/v1/secret-stages", content=chunks(), headers=headers)
        assert oversized.status_code == 413
        malformed = await client.post(
            "/api/v1/secret-stages", json={"value": "NEVER_RETURN_SECRET"}, headers=headers
        )
        assert "NEVER_RETURN_SECRET" not in malformed.text
        assert set(malformed.json()) == {"code", "message", "request_id"}


def test_rotation_query_rechecks_principal_revision_and_idle_expiry(monkeypatch):
    queries = []

    def execute_one(conn, sql, params=()):
        queries.append((sql, params))
        return None

    monkeypatch.setattr("fatty_trader.console.auth.execute_one", execute_one)
    service = AuthService(Mock(), AuthConfig.from_environ(ENV))
    with pytest.raises(AuthError):
        service._session(object(), "principal", stepped=True, old_session="old-session")
    sql, params = queries[0]
    assert "s.principal_id=%s" in sql and "s.principal_revision=p.revision" in sql
    assert "s.last_seen_at>now()" in sql and "FOR UPDATE OF s,p" in sql
    assert params == ("old-session", "principal", 1800)


@pytest.mark.asyncio
async def test_jobs_list_checks_each_job_and_receipt_lookup_is_scoped():
    from fatty_trader.console.commands import CommandError

    store, commands, reads = BoundaryStore(), Mock(), Mock()
    reads.read.return_value = {
        "data": {"items": [{"id": "mine"}, {"id": "other"}], "total": 2},
        "scope": SCOPE,
    }

    def get_job(job_id, principal):
        if job_id == "other":
            raise CommandError("not_found", "Job not found", 404)
        return {"id": job_id}

    commands.get_job.side_effect = get_job
    commands.by_idempotency_key.return_value = {"id": "mine"}
    app = FastAPI()
    mount_console(app, ENV, store=store, commands=commands, reads=reads)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ENV["CONSOLE_ORIGIN"]
    ) as client:
        client.cookies.set("console_session_local", "opaque-token")
        jobs = await client.get("/api/v1/jobs")
        assert jobs.json()["data"]["items"] == [{"id": "mine"}]
        assert jobs.json()["data"]["total"] is None
        receipt = await client.get(
            "/api/v1/jobs/by-idempotency/receipt-key", params={"action": "report.export"}
        )
        assert receipt.status_code == 200 and receipt.json()["data"]["id"] == "mine"
        args = commands.by_idempotency_key.call_args.args
        assert args[0] == "receipt-key" and args[1]["id"] == "principal"
        assert args[2:] == ("report.export", SCOPE)


@pytest.mark.asyncio
async def test_sse_checks_each_job_and_filters_privileged_observations():
    from fatty_trader.console.commands import CommandError

    class EventStore(BoundaryStore):
        reads = 0

        def event_bounds(self, scope):
            return {"first": 1, "last": 3}

        def events(self, cursor, scope, limit):
            self.reads += 1
            if self.reads > 1:
                self.active = False
                return []
            return [
                {
                    "id": i,
                    "event": "changed",
                    "resource_type": kind,
                    "resource_id": resource,
                    "resource_version": 1,
                    "schema_version": 1,
                }
                for i, kind, resource in [
                    (1, "job", "other"),
                    (2, "effective_config", "private"),
                    (3, "job", "mine"),
                ]
            ]

    store, commands = EventStore(), Mock()

    def get_job(job_id, principal):
        if job_id == "other":
            raise CommandError("not_found", "Job not found", 404)
        return {"id": job_id}

    commands.get_job.side_effect = get_job
    app = FastAPI()
    mount_console(app, ENV, store=store, commands=commands, reads=Mock())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ENV["CONSOLE_ORIGIN"]
    ) as client:
        client.cookies.set("console_session_local", "opaque-token")
        result = await client.get("/api/v1/events", params={"cursor": "0"})
        assert '"resource_id":"mine"' in result.text
        assert '"resource_id":"other"' not in result.text
        assert '"resource_id":"private"' not in result.text


@pytest.mark.parametrize(
    "challenge_principal,challenge_session", [("other", "session"), ("principal", "other")]
)
def test_stepup_challenge_rejects_different_principal_or_session(
    monkeypatch, challenge_principal, challenge_session
):
    store = Mock()

    @contextmanager
    def connection():
        yield object()

    store.connection = connection
    monkeypatch.setattr(
        "fatty_trader.console.auth.execute_one",
        lambda *args: {"principal_id": challenge_principal, "session_id": challenge_session},
    )
    service = AuthService(store, AuthConfig.from_environ(ENV))
    service._session = Mock()
    credential = {
        "response": {
            "clientDataJSON": bytes_to_base64url(json.dumps({"crossOrigin": False}).encode())
        }
    }
    with pytest.raises(AuthError):
        service.finish(
            "stepup", "challenge", credential, {"id": "principal", "session_id": "session"}
        )
    service._session.assert_not_called()


def test_identity_effect_uses_shared_authority_fence():
    store = Mock()
    service = AuthService(store, AuthConfig.from_environ(ENV))
    connection, job = object(), {"id": "job"}
    service.effect_authority(connection, job)
    store.cursor.assert_called_once_with(connection)
    store.effect_authority.assert_called_once_with(store.cursor.return_value, job)


@pytest.mark.asyncio
async def test_enqueue_and_replay_return_durable_location_headers():
    store, commands = BoundaryStore(), Mock()
    commands.enqueue.return_value = {"id": "job-id", "idempotency_replayed": True}
    app = FastAPI()
    mount_console(app, ENV, store=store, commands=commands, reads=Mock())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ENV["CONSOLE_ORIGIN"]
    ) as client:
        client.cookies.set("console_session_local", "opaque-token")
        headers = {
            "Origin": ENV["CONSOLE_ORIGIN"],
            "X-CSRF-Token": "csrf",
            "Idempotency-Key": "receipt",
        }
        response = await client.post(
            "/api/v1/jobs",
            json={
                "preview_id": "preview",
                "preview_digest": "digest",
                "reason": "operator request",
                "policy_version": "version",
            },
            headers=headers,
        )
        assert response.status_code == 202
        assert response.headers["Location"] == "/api/v1/jobs/job-id"
        assert response.headers["Idempotency-Replayed"] == "true"
