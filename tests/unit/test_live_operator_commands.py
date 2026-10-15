from decimal import Decimal
from types import SimpleNamespace

import pytest

from fatty_trader.console.telegram import TelegramCommandAdapter
from fatty_trader.operator.command_parser import CommandError
from fatty_trader.operator.live_commands import OperatorReadService

SCOPE = {"account_id": "configured-bitget", "venue": "bitget", "environment": "LIVE"}


class Store:
    def __init__(self):
        self.password_session_active = True
        self.preview = {
            "id": "preview-1",
            "policy_version": "policy",
            "resource_version": "version",
        }

    def one(self, sql, params=()):
        if "FROM console_principals p JOIN console_sessions" in sql:
            assert params == ("console-password-owner",)
            assert "s.last_seen_at>now()" in sql and "s.principal_revision=p.revision" in sql
            return (
                {
                    "id": "console-password-owner",
                    "session_id": "real-password-session",
                    "roles": ["owner"],
                }
                if self.password_session_active
                else None
            )
        if "console_previews" in sql:
            return self.preview
        return None

    def query(self, sql, params=()):
        return [{"client_order_id": "owned-root"}]

    def latest_snapshot(self, kind, scope):
        return {"data": {"values": {"lastPr": "10"}}}


class Commands:
    def __init__(self):
        self.previews = []
        self.enqueues = []

    async def preview(self, request, principal, scope):
        self.previews.append((request, principal, scope))
        return {
            "id": "preview-1",
            "preview_digest": "digest",
            "confirmation_phrase": "CONFIRM owned-root",
        }

    def enqueue(self, request, principal, key):
        self.enqueues.append((request, principal, key))
        return {"id": "job-1", "state": "queued"}


def adapter():
    store, commands = Store(), Commands()
    return TelegramCommandAdapter(store, commands, operator_id=1, scope=SCOPE), store, commands


def handle(service, text, **overrides):
    options = dict(sender_id=1, is_private=True, is_forwarded=False)
    options.update(overrides)
    return service.handle(text, **options)


@pytest.mark.parametrize(
    "override", [{"sender_id": 2}, {"is_private": False}, {"is_forwarded": True}]
)
def test_private_identity_required_before_preview(override):
    service, _, commands = adapter()
    with pytest.raises(PermissionError):
        handle(service, "/close BTCUSDT", **override)
    assert commands.previews == []


def test_close_uses_same_typed_durable_preview():
    service, _, commands = adapter()
    reply = handle(service, "/close BTCUSDT")
    request, principal, scope = commands.previews[0]
    assert request["action"] == "position.close"
    assert request["arguments"] == {"client_oid": "owned-root"}
    assert principal["session_id"] == "real-password-session"
    assert scope == SCOPE
    assert "preview-1" in reply
    assert commands.enqueues == []


def test_confirmation_is_persisted_preview_and_shared_authority():
    service, _, commands = adapter()
    reply = handle(service, "/confirm preview-1 digest CONFIRM owned-root")
    request, principal, key = commands.enqueues[0]
    assert request["preview_id"] == "preview-1"
    assert request["preview_digest"] == "digest"
    assert request["confirmation_phrase"] == "CONFIRM owned-root"
    assert request["resource_version"] == "version"
    assert request["channel"] == "telegram"
    assert "job-1" in reply


@pytest.mark.parametrize("text", ["/close all", "/cancel all", "/close BTCUSDT confirm=legacy"])
def test_unsafe_account_wide_and_legacy_tokens_prohibited(text):
    service, _, commands = adapter()
    with pytest.raises(CommandError):
        handle(service, text)
    assert commands.previews == []


def test_telegram_cannot_fabricate_password_session():
    service, store, commands = adapter()
    store.password_session_active = False
    with pytest.raises(CommandError, match="shared password"):
        handle(service, "/close BTCUSDT")
    assert commands.previews == []


def test_manual_market_and_limit_share_risk_admission():
    service, _, commands = adapter()
    handle(service, "/open BTCUSDT LONG margin=auto leverage=20 entry=limit:10 sl=9 tp=11")
    assert commands.previews[0][0]["arguments"] == {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "order_type": "LIMIT",
        "entry_price": "10",
        "stop_loss": "9",
        "take_profit": "11",
        "leverage": 20,
    }
    assert "margin" not in commands.previews[0][0]["arguments"]


def test_read_only_service_has_no_confirmation_or_mutation_authority():
    gateway = SimpleNamespace(
        get_price=lambda symbol: Decimal("10"), get_balance=lambda: Decimal("3")
    )
    service = OperatorReadService(gateway, operator_id=1)
    assert "10" in handle(service, "/price BTCUSDT")
    assert "3" in handle(service, "/balance")
    assert not hasattr(service, "_pending")
    with pytest.raises(CommandError, match="shared durable"):
        handle(service, "/close BTCUSDT")
