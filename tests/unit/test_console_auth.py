"""Offline shared-password/session boundary regressions; no live endpoints."""

import hashlib
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI

from fatty_trader.console.api import mount_console
from fatty_trader.console.auth import OWNER_ID, AuthConfig, AuthError, AuthService, digest, utcnow

TEST_PASSWORD = "synthetic-test-password-not-a-deployed-secret"
TEST_SALT = bytes(range(16))
TEST_HASH = (
    "scrypt$16384$8$1$"
    + TEST_SALT.hex()
    + "$"
    + hashlib.scrypt(TEST_PASSWORD.encode(), salt=TEST_SALT, n=16384, r=8, p=1, dklen=32).hex()
)

ENV = {
    "CONSOLE_ENABLED": "1",
    "CONSOLE_ORIGIN": "http://localhost:18081",
    "CONSOLE_ALLOW_LOCALHOST_HTTP": "1",
    "CONSOLE_ACCOUNT_ID": "account-one",
    "BITGET_MODE": "LIVE",
    "CONSOLE_PASSWORD_HASH": TEST_HASH,
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


def test_csrf_origin_role_and_missing_sessions():
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
        service.principal(None)
    assert digest("opaque-secret") != "opaque-secret"


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
    token = service._session(object(), OWNER_ID, consented=True, old_session="old-session")
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


def test_rate_limit_fails_closed_and_scope_never_inferred():
    store = Mock()
    store.one.return_value = {"attempts": 21}
    service = AuthService(store, AuthConfig.from_environ(ENV))
    with pytest.raises(AuthError) as limit:
        service.rate_limit("127.0.0.1", "password-login")
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
        service._session(object(), "principal", consented=True, old_session="old-session")
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


@pytest.fixture
def password_service(monkeypatch):
    store = Mock()

    @contextmanager
    def connection():
        yield object()

    store.connection = connection
    calls = []

    def execute_one(conn, sql, params=()):
        calls.append((sql, params))
        if "SELECT revision,enabled" in sql:
            return {"revision": 3, "enabled": True}
        return None

    monkeypatch.setattr("fatty_trader.console.auth.execute_one", execute_one)
    return AuthService(store, AuthConfig.from_environ(ENV)), store, calls


def test_password_login_creates_only_scoped_owner_and_hashes_opaque_session(password_service):
    service, store, calls = password_service
    token = service.login(TEST_PASSWORD, "old-browser-token")
    owner_sql, owner = next(item for item in calls if "INSERT INTO console_principals" in item[0])
    assert owner == (
        OWNER_ID,
        '[{"account_id": "account-one", "venue": "bitget", "environment": "LIVE"}]',
        service.config.password_revision,
    )
    assert "ON CONFLICT(id)" in owner_sql and "revision=EXCLUDED.revision" in owner_sql
    session = next(params for sql, params in calls if "INSERT INTO console_sessions" in sql)
    assert session[0] == digest(token) and session[2] == OWNER_ID
    assert session[3] != token and len(token) >= 32 and session[5] is None
    assert TEST_PASSWORD not in repr(calls) and TEST_HASH not in repr(calls)
    assert (
        "UPDATE console_sessions SET revoked_at=now() WHERE token_hash=%s",
        (digest("old-browser-token"),),
    ) in calls


def test_wrong_password_never_touches_session_or_principal(password_service):
    service, store, calls = password_service
    with pytest.raises(AuthError) as error:
        service.login("synthetic-wrong-password")
    assert error.value.code == "authentication_rejected"
    assert calls == []


@pytest.mark.parametrize(
    "encoded", ["", "plaintext-is-not-a-hash", "scrypt$32768$8$1$00$00", "scrypt$16384$8$1$00$00"]
)
def test_missing_or_invalid_password_hash_refuses_login(encoded):
    store = Mock()
    service = AuthService(store, AuthConfig.from_environ({**ENV, "CONSOLE_PASSWORD_HASH": encoded}))
    with pytest.raises(AuthError) as error:
        service.login(TEST_PASSWORD)
    assert error.value.status == 503 and TEST_PASSWORD not in str(error.value)
    store.connection.assert_not_called()


def test_hash_file_and_password_epoch_are_scoped_and_fail_closed(tmp_path):
    hash_file = tmp_path / "password.hash"
    hash_file.write_text(TEST_HASH + "\n", encoding="utf-8")
    values = {**ENV, "CONSOLE_PASSWORD_HASH": "", "CONSOLE_PASSWORD_HASH_FILE": str(hash_file)}
    config = AuthConfig.from_environ(values)
    assert config.password_hash == TEST_HASH
    assert TEST_HASH not in repr(config)
    assert config.password_revision == AuthConfig.from_environ(ENV).password_revision
    changed = AuthConfig.from_environ({**ENV, "CONSOLE_PASSWORD_HASH": TEST_HASH + "00"})
    rescoped = AuthConfig.from_environ({**ENV, "CONSOLE_ACCOUNT_ID": "different-account"})
    assert (
        len({config.password_revision, changed.password_revision, rescoped.password_revision}) == 3
    )
    hash_file.unlink()
    assert AuthConfig.from_environ(values).password_hash == ""


def test_session_query_rejects_old_identity_and_password_epoch():
    store = Mock()
    store.one.return_value = None
    service = AuthService(store, AuthConfig.from_environ(ENV))
    with pytest.raises(AuthError):
        service.principal("opaque-session")
    sql, params = store.one.call_args.args
    assert "p.id=%s" in sql and "p.revision=%s" in sql
    assert params == (digest("opaque-session"), OWNER_ID, service.config.password_revision, 1800)


def test_consent_rotates_and_mints_exact_expiring_grant_without_password(
    password_service, monkeypatch
):
    service, store, calls = password_service
    binding = {"preview_digest": "review-digest", "action": "position.close", "scope": SCOPE}

    def execute_one(conn, sql, params=()):
        calls.append((sql, params))
        if "FROM console_previews" in sql:
            return binding
        if "SELECT id FROM console_sessions" in sql:
            return {"id": "rotated-session"}
        return None

    monkeypatch.setattr("fatty_trader.console.auth.execute_one", execute_one)
    service._session = Mock(return_value="new-opaque-token")
    assert service.consent({"id": OWNER_ID, "session_id": "old-session"}, binding) == (
        "new-opaque-token"
    )
    service._session.assert_called_once_with(
        service._session.call_args.args[0], OWNER_ID, consented=True, old_session="old-session"
    )
    sql, params = next(item for item in calls if "INSERT INTO console_step_up_grants" in item[0])
    assert params[1:5] == (OWNER_ID, "rotated-session", "review-digest", "position.close")
    assert params[-1] == 300 and "expires_at" in sql
    assert "expires_at>now()" in calls[0][0] and "actor_id=%s" in calls[0][0]


@pytest.mark.parametrize(
    "change",
    [
        {"scope": {**SCOPE, "account_id": "other"}},
        {"action": "trade.open"},
        {"preview_digest": "different-review"},
    ],
)
def test_consent_cannot_substitute_scope_action_or_digest(password_service, monkeypatch, change):
    service, store, calls = password_service
    binding = {"preview_digest": "review-digest", "action": "position.close", "scope": SCOPE}
    monkeypatch.setattr("fatty_trader.console.auth.execute_one", lambda *args: binding)
    service._session = Mock()
    with pytest.raises(AuthError):
        service.consent({"id": OWNER_ID, "session_id": "session"}, {**binding, **change})
    service._session.assert_not_called()


@pytest.mark.asyncio
async def test_login_api_exact_origin_rate_limit_and_no_old_ceremony_routes(monkeypatch):
    store = BoundaryStore()
    app = FastAPI()
    mount_console(app, ENV, store=store, commands=Mock(), reads=Mock())
    login = Mock(return_value="new-opaque-session")
    monkeypatch.setattr(app.state.console_auth, "login", login)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ENV["CONSOLE_ORIGIN"]
    ) as client:
        denied = await client.post("/api/v1/auth/login", json={"password": TEST_PASSWORD})
        assert denied.status_code == 403
        login.assert_not_called()
        result = await client.post(
            "/api/v1/auth/login",
            json={"password": TEST_PASSWORD},
            headers={"Origin": ENV["CONSOLE_ORIGIN"]},
        )
        assert result.status_code == 200
        cookie = result.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert result.json()["csrf_token"] == "csrf"
        assert TEST_PASSWORD not in result.text
        obsolete = await client.post("/api/v1/auth/authenticate/start", json={})
        assert obsolete.status_code in {404, 405}
        invalid = await client.post(
            "/api/v1/auth/login",
            json={"password": TEST_PASSWORD, "username": "not-supported"},
            headers={"Origin": ENV["CONSOLE_ORIGIN"]},
        )
        assert invalid.status_code == 422 and TEST_PASSWORD not in invalid.text


@pytest.mark.asyncio
async def test_consent_api_requires_current_session_csrf_and_one_bound_target(monkeypatch):
    class ConsentStore(BoundaryStore):
        def one(self, sql, params=()):
            if "FROM console_previews" in sql:
                return {
                    "actor_id": "principal",
                    "preview_digest": "review-digest",
                    "action": "position.close",
                    "scope": SCOPE,
                }
            return super().one(sql, params)

    store, app = ConsentStore(), FastAPI()
    mount_console(app, ENV, store=store, commands=Mock(), reads=Mock())
    consent = Mock(return_value="rotated-session-token")
    monkeypatch.setattr(app.state.console_auth, "consent", consent)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ENV["CONSOLE_ORIGIN"]
    ) as client:
        headers = {"Origin": ENV["CONSOLE_ORIGIN"], "X-CSRF-Token": "csrf"}
        target = {"preview_digest": "review-digest"}
        assert (
            await client.post("/api/v1/auth/consent", json=target, headers=headers)
        ).status_code == 401
        client.cookies.set("console_session_local", "active-token")
        assert (
            await client.post(
                "/api/v1/auth/consent", json=target, headers={"Origin": ENV["CONSOLE_ORIGIN"]}
            )
        ).status_code == 403
        assert (
            await client.post("/api/v1/auth/consent", json={}, headers=headers)
        ).status_code == 400
        assert (
            await client.post(
                "/api/v1/auth/consent", json={**target, "job_id": "job"}, headers=headers
            )
        ).status_code == 400
        consent.assert_not_called()
        result = await client.post("/api/v1/auth/consent", json=target, headers=headers)
        assert result.status_code == 200
        assert consent.call_args.args[1] == {
            "preview_digest": "review-digest",
            "action": "position.close",
            "scope": SCOPE,
        }


def test_expired_preview_or_old_identity_cannot_receive_consent(password_service, monkeypatch):
    service, store, calls = password_service
    binding = {"preview_digest": "expired-review", "action": "position.close", "scope": SCOPE}
    monkeypatch.setattr("fatty_trader.console.auth.execute_one", lambda *args: None)
    service._session = Mock()
    for principal in (
        {"id": OWNER_ID, "session_id": "active"},
        {"id": "former-identity", "session_id": "old"},
    ):
        with pytest.raises(AuthError):
            service.consent(principal, binding)
    service._session.assert_not_called()


def test_session_revocation_is_self_owned_only():
    store = Mock()
    service = AuthService(store, AuthConfig.from_environ(ENV))
    principal = {"id": OWNER_ID, "roles": ["owner"]}
    store.one.return_value = {"principal_id": "former-identity"}
    with pytest.raises(AuthError) as error:
        service.revoke(principal, "other-session")
    assert error.value.status == 404
    store.execute.assert_not_called()
    store.one.return_value = {"principal_id": OWNER_ID}
    service.revoke(principal, "own-session")
    assert store.execute.call_args.args[1] == ("own-session",)


@pytest.mark.asyncio
async def test_password_api_durable_rate_limit_precedes_password_verification(monkeypatch):
    class LimitedStore(BoundaryStore):
        def one(self, sql, params=()):
            if "console_auth_rates" in sql:
                assert params[0] != TEST_PASSWORD
                return {"attempts": 11}
            return super().one(sql, params)

    app = FastAPI()
    mount_console(app, ENV, store=LimitedStore(), commands=Mock(), reads=Mock())
    login = Mock()
    monkeypatch.setattr(app.state.console_auth, "login", login)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ENV["CONSOLE_ORIGIN"]
    ) as client:
        result = await client.post(
            "/api/v1/auth/login",
            json={"password": TEST_PASSWORD},
            headers={"Origin": ENV["CONSOLE_ORIGIN"]},
        )
        assert result.status_code == 429 and result.json()["code"] == "rate_limited"
        assert TEST_PASSWORD not in result.text
    login.assert_not_called()
