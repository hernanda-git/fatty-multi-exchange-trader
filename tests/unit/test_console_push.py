"""Offline console push-subscription API boundary: auth, validation and audit."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI

from fatty_trader.console.api import mount_console
from fatty_trader.console.auth import AuthConfig, AuthError, AuthService, utcnow

TEST_PASSWORD = "synthetic-test-password-not-a-deployed-secret"
TEST_SALT = bytes(range(16))
TEST_HASH = (
    "scrypt$16384$8$1$"
    + TEST_SALT.hex()
    + "$"
    + hashlib.scrypt(TEST_PASSWORD.encode(), salt=TEST_SALT, n=16384, r=8, p=1, dklen=32).hex()
)
SCOPE = {"account_id": "account-one", "venue": "bitget", "environment": "LIVE"}


def environment(public_key_file: Path | None = None) -> dict[str, str]:
    env = {
        "CONSOLE_ENABLED": "1",
        "CONSOLE_ORIGIN": "http://localhost:18081",
        "CONSOLE_ALLOW_LOCALHOST_HTTP": "1",
        "CONSOLE_ACCOUNT_ID": "account-one",
        "BITGET_MODE": "LIVE",
        "CONSOLE_PASSWORD_HASH": TEST_HASH,
    }
    if public_key_file is not None:
        env["CONSOLE_VAPID_PUBLIC_KEY_FILE"] = str(public_key_file)
    return env


class PushStore:
    def __init__(self) -> None:
        self.audits: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.removable = True

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if "console_auth_rates" in sql:
            return {"attempts": 1}
        if "console_sessions s JOIN console_principals" in sql:
            now = utcnow()
            return {
                "id": "principal",
                "display_name": "Operator",
                "enabled": True,
                "roles": ["owner"],
                "scopes": [SCOPE],
                "session_id": "session",
                "csrf_token": "csrf",
                "authenticated_at": now,
                "step_up_at": None,
                "last_seen_at": now,
                "expires_at": now + timedelta(hours=12),
                "absolute_expires_at": now + timedelta(hours=12),
            }
        if "SELECT count(*) AS n" in sql:
            return {"n": 2}
        if "INSERT INTO web_push_subscriptions" in sql:
            self.executed.append((sql, params))
            return {"id": "subscribed-id-1"}
        if "UPDATE web_push_subscriptions SET disabled_at" in sql:
            self.executed.append((sql, params))
            return {"id": params[0]} if self.removable else None
        return None

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        assert "web_push_subscriptions" in sql
        return [
            {
                "id": "subscribed-id-1",
                "user_agent": "iPhone",
                "created_at": "2026-10-15T00:00:00Z",
                "last_seen_at": "2026-10-15T00:00:00Z",
                "disabled_at": None,
            }
        ]

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        self.executed.append((sql, params))
        return 1

    def audit(self, *args: Any, **kwargs: Any) -> int:
        self.audits.append((args, kwargs))
        return 1


def build_client(env: dict[str, str], store: PushStore) -> httpx.AsyncClient:
    app = FastAPI()
    reads = Mock()
    reads.read.return_value = {
        "data": {"items": []},
        "evidence": [],
        "warnings": [],
        "scope": SCOPE,
    }
    mount_console(app, env, store=store, commands=Mock(), reads=reads)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=env["CONSOLE_ORIGIN"]
    )
    client.cookies.set("console_session_local", "opaque-token")
    return client


@pytest.mark.asyncio
async def test_push_config_reports_disabled_without_server_key() -> None:
    store = PushStore()
    async with build_client(environment(), store) as client:
        result = await client.get("/api/v1/push/config")
    assert result.status_code == 200
    body = result.json()["data"]
    assert body["enabled"] is False
    assert body["public_key"] is None


@pytest.mark.asyncio
async def test_push_config_exposes_public_key_when_provisioned(tmp_path: Path) -> None:
    public = tmp_path / "web-push-public.txt"
    public.write_text("BPublicVapidKey\n", encoding="utf-8")
    store = PushStore()
    async with build_client(environment(public), store) as client:
        result = await client.get("/api/v1/push/config")
    assert result.status_code == 200
    body = result.json()["data"]
    assert body["enabled"] is True
    assert body["public_key"] == "BPublicVapidKey"


@pytest.mark.asyncio
async def test_subscribe_requires_csrf_origin_and_https_endpoint() -> None:
    store = PushStore()
    subscription = {
        "endpoint": "https://web.push.apple.com/example-endpoint",
        "p256dh": "p" * 40,
        "auth": "a" * 20,
        "user_agent": "iPhone",
    }
    async with build_client(environment(), store) as client:
        no_csrf = await client.post(
            "/api/v1/push/subscriptions",
            json=subscription,
            headers={"Origin": "http://localhost:18081"},
        )
        assert no_csrf.status_code == 403 and no_csrf.json()["code"] == "csrf_rejected"

        plain = await client.post(
            "/api/v1/push/subscriptions",
            json={**subscription, "endpoint": "http://push.example/insecure"},
            headers={"Origin": "http://localhost:18081", "X-CSRF-Token": "csrf"},
        )
        assert plain.status_code == 422 and plain.json()["code"] == "invalid_request"

        saved = await client.post(
            "/api/v1/push/subscriptions",
            json=subscription,
            headers={"Origin": "http://localhost:18081", "X-CSRF-Token": "csrf"},
        )
        assert saved.status_code == 200
        assert saved.json()["data"]["id"] == "subscribed-id-1"
    insert = next(params for sql, params in store.executed if "INSERT INTO web_push" in sql)
    assert insert[1] == subscription["endpoint"]
    assert any(args[0] == "push.subscribe" for args, _ in store.audits)


@pytest.mark.asyncio
async def test_list_and_remove_subscriptions() -> None:
    store = PushStore()
    async with build_client(environment(), store) as client:
        listing = await client.get("/api/v1/push/subscriptions")
        assert listing.status_code == 200
        assert listing.json()["data"]["enabled_count"] == 2
        assert listing.json()["data"]["items"][0]["user_agent"] == "iPhone"

        removed = await client.post(
            "/api/v1/push/subscriptions/remove",
            json={"id": "subscribed-id-1"},
            headers={"Origin": "http://localhost:18081", "X-CSRF-Token": "csrf"},
        )
        assert removed.status_code == 200 and removed.json()["data"]["disabled"] is True

        store.removable = False
        missing = await client.post(
            "/api/v1/push/subscriptions/remove",
            json={"id": "unknown-subscription"},
            headers={"Origin": "http://localhost:18081", "X-CSRF-Token": "csrf"},
        )
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_test_notification_is_queued_through_the_outbox() -> None:
    store = PushStore()
    async with build_client(environment(), store) as client:
        result = await client.post(
            "/api/v1/push/test",
            json={},
            headers={"Origin": "http://localhost:18081", "X-CSRF-Token": "csrf"},
        )
    assert result.status_code == 200 and result.json()["data"]["queued"] is True
    insert = next(params for sql, params in store.executed if "notifications_outbox" in sql)
    assert insert[1].startswith("push-test:")
    assert '"kind": "push-test"' in insert[2]
    assert any(args[0] == "push.test" for args, _ in store.audits)


def test_origin_check_still_rejects_unknown_origin_for_push_routes() -> None:
    service = AuthService(Mock(), AuthConfig.from_environ(environment()))
    with pytest.raises(AuthError):
        service.origin_check("https://evil.example", "application/json")
