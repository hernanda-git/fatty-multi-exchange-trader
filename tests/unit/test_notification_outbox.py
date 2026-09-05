from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from fatty_trader.config.notifications import TelegramNotificationSettings
from fatty_trader.notifications import (
    NotificationDeliveryError,
    NotificationWorker,
    OutboxNotification,
    PostgresNotificationOutbox,
    format_notification_html,
)


def test_notification_settings_hide_token_and_require_target() -> None:
    settings = TelegramNotificationSettings.from_mapping(
        {"TG_BOT_TOKEN": "123456:private-token", "TELEGRAM_TARGET_CHAT_ID": "-10042"}
    )

    assert settings.target_chat_id == -10042
    assert "private-token" not in repr(settings)
    with pytest.raises(ValueError, match="TG_BOT_TOKEN"):
        TelegramNotificationSettings.from_mapping({"TELEGRAM_TARGET_CHAT_ID": "1"})


def test_notification_sender_is_a_separate_inert_compose_service() -> None:
    from fatty_trader.notification_service import notification_settings

    compose = (Path(__file__).parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
    assert "  notification-sender:" in compose
    assert "fatty_trader.notification_service" in compose
    assert "TG_BOT_TOKEN: ${TG_BOT_TOKEN:-}" in compose
    assert notification_settings({}) is None


def test_notification_html_escapes_and_redacts_untrusted_payload() -> None:
    text = format_notification_html(
        {
            "kind": "<execution>",
            "reason": "token=not-for-telegram <script>",
            "api_secret": "must-not-appear",
            "nested": {"password": "nope", "symbol": "BTCUSDT"},
        }
    )

    assert "<script>" not in text
    assert "&lt;execution&gt;" in text
    assert "not-for-telegram" not in text
    assert "must-not-appear" not in text
    assert "password=[redacted]" in text


class FakeOutbox:
    def __init__(self, notification: OutboxNotification | None) -> None:
        self.notification = notification
        self.calls: list[tuple[str, object]] = []

    def claim(self, worker_id: str, lease_seconds: int) -> OutboxNotification | None:
        self.calls.append(("claim", (worker_id, lease_seconds)))
        return self.notification

    def mark_sent(self, notification_id: object, worker_id: str) -> None:
        self.calls.append(("sent", (notification_id, worker_id)))

    def mark_retry(self, notification_id: object, worker_id: str, delay_seconds: int) -> None:
        self.calls.append(("retry", (notification_id, worker_id, delay_seconds)))

    def mark_failed(self, notification_id: object, worker_id: str) -> None:
        self.calls.append(("failed", (notification_id, worker_id)))


class FakeSender:
    def __init__(self, error: NotificationDeliveryError | None = None) -> None:
        self.error = error
        self.messages: list[str] = []

    async def send(self, text: str) -> None:
        self.messages.append(text)
        if self.error is not None:
            raise self.error


@pytest.mark.asyncio
async def test_worker_marks_success_and_only_sends_safe_html() -> None:
    item = OutboxNotification(uuid4(), {"reason": "hello <world>"}, attempts=1)
    outbox = FakeOutbox(item)
    sender = FakeSender()

    assert await NotificationWorker(outbox, sender).run_once("worker-a", 30) == "sent"
    assert outbox.calls[-1][0] == "sent"
    assert "<world>" not in sender.messages[0]
    assert "&lt;world&gt;" in sender.messages[0]


@pytest.mark.asyncio
async def test_worker_retries_transient_failures_then_marks_attempt_limit_failed() -> None:
    item = OutboxNotification(uuid4(), {"reason": "timeout"}, attempts=2)
    retry_outbox = FakeOutbox(item)
    retry_sender = FakeSender(NotificationDeliveryError(retryable=True))
    retry_worker = NotificationWorker(
        retry_outbox, retry_sender, max_attempts=3, retry_seconds=7
    )
    assert await retry_worker.run_once(
        "worker-a", 30
    ) == "retry"
    assert retry_outbox.calls[-1] == ("retry", (item.id, "worker-a", 14))

    failed_outbox = FakeOutbox(OutboxNotification(uuid4(), {"reason": "timeout"}, attempts=3))
    assert await NotificationWorker(
        failed_outbox, retry_sender, max_attempts=3
    ).run_once("worker-a", 30) == "failed"
    assert failed_outbox.calls[-1][0] == "failed"


def test_postgres_outbox_claim_is_lease_safe_and_updates_are_worker_bound() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.statements: list[tuple[str, tuple[Any, ...]]] = []
            self.rows: list[dict[str, object] | None] = [
                {"id": str(uuid4()), "payload": {"reason": "x"}, "attempts": 1}
            ]

        def execute(self, statement: str, params: tuple[Any, ...] = ()) -> None:
            self.statements.append((statement, params))

        def fetchone(self) -> dict[str, object] | None:
            return self.rows.pop(0) if self.rows else None

    class Connection:
        def __init__(self) -> None:
            self.cursor_value = Cursor()
            self.commits = 0
            self.rollbacks = 0

        def cursor(self) -> Cursor:
            return self.cursor_value

        def commit(self) -> None:
            self.commits += 1

        def rollback(self) -> None:
            self.rollbacks += 1

    connection = Connection()
    outbox = PostgresNotificationOutbox(lambda: connection)
    item = outbox.claim("sender-1", 30)
    assert item is not None
    outbox.mark_retry(item.id, "sender-1", 60)

    claim_sql = connection.cursor_value.statements[0][0]
    retry_sql = connection.cursor_value.statements[1][0]
    assert "FOR UPDATE SKIP LOCKED" in claim_sql
    assert "lease_until <= now()" in claim_sql
    assert "attempts = attempts + 1" in claim_sql
    assert "claimed_by = %s" in retry_sql
    assert connection.commits == 2
