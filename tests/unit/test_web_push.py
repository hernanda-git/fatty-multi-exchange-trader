"""Web-push payload rendering, configuration and durable delivery ledger behavior."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from fatty_trader.web_push import (
    PushDelivery,
    PushSubscription,
    WebPushConfig,
    WebPushDeliveryWorker,
    WebPushFailed,
    WebPushGone,
    WebPushRetryable,
    build_push_message,
    format_push_text,
    plain_text_from_html,
    read_public_key_file,
)


def _delivery(attempts: int = 1, payload: dict[str, Any] | None = None) -> PushDelivery:
    return PushDelivery(
        notification_id=uuid4(),
        payload=payload or {"kind": "operator-test", "note": "hello"},
        attempts=attempts,
        subscription=PushSubscription(
            id="sub-1",
            endpoint="https://push.example/endpoint",
            p256dh="p" * 32,
            auth="a" * 16,
        ),
        claim_token="worker:claim",
    )


class FakeStore:
    def __init__(self, deliveries: list[PushDelivery]) -> None:
        self.pending = list(deliveries)
        self.events: list[tuple[Any, ...]] = []

    def fan_out(self, notification_id: Any) -> int:
        self.events.append(("fan_out", notification_id))
        return 1

    def claim_due(self, worker_id: str, lease_seconds: int, limit: int) -> list[PushDelivery]:
        claimed, self.pending = self.pending[:limit], self.pending[limit:]
        return claimed

    def mark_sent(self, delivery: PushDelivery) -> bool:
        self.events.append(("sent", delivery.subscription.id))
        return True

    def mark_retry(self, delivery: PushDelivery, delay_seconds: int) -> bool:
        self.events.append(("retry", delivery.subscription.id, delay_seconds))
        return True

    def mark_failed(self, delivery: PushDelivery, reason: str) -> bool:
        self.events.append(("failed", delivery.subscription.id, reason))
        return True

    def disable_subscription(self, subscription_id: str, reason: str) -> bool:
        self.events.append(("disabled", subscription_id, reason))
        return True

    def prune_finished(self, retention_days: int) -> int:
        self.events.append(("prune", retention_days))
        return 0


class Sender:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.messages: list[str] = []

    async def __call__(self, subscription: PushSubscription, message: str) -> None:
        self.messages.append(message)
        if self.failure is not None:
            raise self.failure


def test_configuration_is_inert_without_a_key_file(tmp_path: Path) -> None:
    assert WebPushConfig.from_environ({}) is None
    assert WebPushConfig.from_environ({"WEB_PUSH_VAPID_PRIVATE_KEY_FILE": ""}) is None
    missing = WebPushConfig.from_environ(
        {"WEB_PUSH_VAPID_PRIVATE_KEY_FILE": str(tmp_path / "absent.pem")}
    )
    assert missing is None


def test_configuration_requires_a_positive_ttl(tmp_path: Path) -> None:
    key = tmp_path / "key.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
    with pytest.raises(ValueError, match="WEB_PUSH_TTL_SECONDS"):
        WebPushConfig.from_environ(
            {"WEB_PUSH_VAPID_PRIVATE_KEY_FILE": str(key), "WEB_PUSH_TTL_SECONDS": "0"}
        )


def test_configuration_converts_pem_for_pywebpush(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from py_vapid import Vapid

    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    target = tmp_path / "vapid.pem"
    target.write_bytes(pem)

    config = WebPushConfig.from_environ({"WEB_PUSH_VAPID_PRIVATE_KEY_FILE": str(target)})
    assert config is not None
    assert config.subject == "https://localhost"
    parsed = Vapid.from_string(config.private_key)
    expected_public = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    actual_public = parsed.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    assert actual_public == expected_public


def test_unreadable_pem_is_inert_instead_of_crashing(tmp_path: Path) -> None:
    target = tmp_path / "garbage.pem"
    target.write_text("not a key at all", encoding="utf-8")
    assert WebPushConfig.from_environ({"WEB_PUSH_VAPID_PRIVATE_KEY_FILE": str(target)}) is None


def test_public_key_file_is_optional(tmp_path: Path) -> None:
    assert read_public_key_file({}) is None
    target = tmp_path / "public.txt"
    target.write_text("BPublicKeyValue\n", encoding="utf-8")
    assert read_public_key_file({"CONSOLE_VAPID_PUBLIC_KEY_FILE": str(target)}) == (
        "BPublicKeyValue"
    )


def test_push_text_is_bounded_plain_text_with_kind_title() -> None:
    text = plain_text_from_html("<b>Signal</b>: <i>MAGICUSDT</i>\nline")
    assert text == "Signal: MAGICUSDT\nline"
    title, body = format_push_text({"kind": "signal-analysis", "symbol": "MAGICUSDT"})
    assert title == "Signal analyzed"
    assert "<" not in body and len(body) <= 400
    title, _ = format_push_text({"kind": "push-test"})
    assert title == "Push test"


def test_push_message_is_worker_json_with_console_url() -> None:
    identifier = uuid4()
    raw = build_push_message(identifier, {"kind": "execution-alert", "reason": "preflight"})
    data = json.loads(raw)
    assert data["url"] == "/notifications"
    assert data["tag"] == str(identifier)
    assert data["title"] and data["body"]


@pytest.mark.asyncio
async def test_delivery_success_marks_sent() -> None:
    delivery = _delivery()
    store = FakeStore([delivery])
    sender = Sender()
    worker = WebPushDeliveryWorker(store, sender, max_attempts=3, retry_seconds=10)
    assert await worker.run_once("worker", 30) == "delivered"
    assert store.events == [("sent", "sub-1")]
    assert json.loads(sender.messages[0])["url"] == "/notifications"


@pytest.mark.asyncio
async def test_retryable_failure_schedules_retry_then_exhausts() -> None:
    store = FakeStore([_delivery(attempts=1)])
    worker = WebPushDeliveryWorker(
        store, Sender(WebPushRetryable()), max_attempts=5, retry_seconds=60
    )
    assert await worker.run_once("worker", 30) == "attempted"
    assert store.events == [("retry", "sub-1", 60)]

    exhausted = FakeStore([_delivery(attempts=5)])
    worker = WebPushDeliveryWorker(
        exhausted, Sender(WebPushRetryable()), max_attempts=5, retry_seconds=60
    )
    await worker.run_once("worker", 30)
    assert exhausted.events == [("failed", "sub-1", "attempts-exhausted")]


@pytest.mark.asyncio
async def test_gone_subscription_is_disabled_and_delivery_fails() -> None:
    store = FakeStore([_delivery()])
    worker = WebPushDeliveryWorker(store, Sender(WebPushGone()), max_attempts=5, retry_seconds=60)
    await worker.run_once("worker", 30)
    assert ("disabled", "sub-1", "subscription-gone") in store.events
    assert ("failed", "sub-1", "subscription-gone") in store.events


@pytest.mark.asyncio
async def test_permanent_and_invalid_payloads_fail_without_retry() -> None:
    store = FakeStore(
        [_delivery(), _delivery(payload={"kind": "console-health-report", "snapshot": "bad"})]
    )
    worker = WebPushDeliveryWorker(store, Sender(WebPushFailed()), max_attempts=5, retry_seconds=60)
    await worker.run_once("worker", 30)
    assert ("failed", "sub-1", "permanent") in store.events
    assert ("failed", "sub-1", "invalid-payload") in store.events


@pytest.mark.asyncio
async def test_idle_when_nothing_is_due() -> None:
    worker = WebPushDeliveryWorker(FakeStore([]), Sender(), max_attempts=5, retry_seconds=60)
    assert await worker.run_once("worker", 30) == "idle"
