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
    assert "<br>" not in text


def test_signal_analysis_without_trade_is_a_plain_language_update() -> None:
    text = format_notification_html(
        {
            "kind": "signal-analysis",
            "source_message_id": 16102,
            "status": "CODEX_SUCCEEDED",
            "canonical_signal": False,
            "source_text": "$WLD TP1 booked here at 2R",
            "dispatches": 0,
        }
    )

    assert "<b>Update sumber</b>" in text
    assert "TP1 booked here at 2R" in text
    assert "Tidak ada order dibuat" in text
    assert "Source Revision" not in text
    assert "Canonical Signal" not in text


def test_signal_analysis_with_trade_uses_compact_trade_card() -> None:
    text = format_notification_html(
        {
            "kind": "signal-analysis",
            "source_message_id": 16103,
            "status": "CODEX_SUCCEEDED",
            "canonical_signal": True,
            "pair": "WLD",
            "direction": "LONG",
            "entry": "0.47",
            "stop_loss": "0.4562",
            "take_profits": ["0.51"],
            "dispatches": 1,
        }
    )

    assert "<b>Setup terdeteksi · WLD LONG</b>" in text
    assert "Entry  : <code>0.47</code>" in text
    assert "SL     : <code>0.4562</code>" in text
    assert "TP     : <code>0.51</code>" in text
    assert "1 proses eksekusi dibuat" in text


def test_cutover_event_states_that_no_order_was_sent() -> None:
    text = format_notification_html(
        {
            "kind": "execution-event",
            "dispatch_id": "577ed2b8-9511-4abc-851b-2f2d256714bf",
            "from_state": "QUEUED",
            "to_state": "REJECTED",
            "reason": "cutover-gated",
        }
    )

    assert "<b>Eksekusi diblokir</b>" in text
    assert "Tidak ada order dikirim" in text
    assert "cutover-gated" not in text
    assert "577ed2b8" not in text


def test_source_tp1_update_is_labeled_as_position_management() -> None:
    text = format_notification_html(
        {
            "kind": "signal-analysis",
            "source_message_id": 16105,
            "canonical_signal": False,
            "management_action": "TP1_BOOKED",
            "management_symbol": "WLDUSDT",
            "source_text": "$WLD TP1 booked here at 2R",
        }
    )

    assert "<b>Manajemen posisi · WLDUSDT</b>" in text
    assert "TP1 booked terdeteksi dari source trader." in text
    assert "Tidak ada order dibuat" not in text


def test_heartbeat_uses_rich_report_layout() -> None:
    text = format_notification_html(
        {
            "kind": "heartbeat",
            "mode": "DEMO",
            "venue_mode": "LIVE",
            "host": "fspmi-hostinger",
            "source": "@fattyfatclub",
            "latest_source_message_id": 16096,
            "latest_source_received_at": "2026-09-06 13:55 UTC",
            "raw_messages": 4,
            "received": 0,
            "analyzed": 4,
            "failed": 0,
            "canonical_signals": 1,
            "dispatches": 2,
            "live_order_intents": 0,
            "notification_pending": 0,
            "notification_failed": 0,
            "execution_enabled": 0,
            "codex": "UNCONFIGURED",
        }
    )

    assert "<b>Fatty Trader</b>" in text
    assert "<i>Ringkasan Operasional</i>" in text
    assert "<b>Kesehatan</b>" in text
    assert "<b>Data sistem</b>" in text
    assert "Signals" not in text
    assert "<br>" not in text


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
    retry_worker = NotificationWorker(retry_outbox, retry_sender, max_attempts=3, retry_seconds=7)
    assert await retry_worker.run_once("worker-a", 30) == "retry"
    assert retry_outbox.calls[-1] == ("retry", (item.id, "worker-a", 14))

    failed_outbox = FakeOutbox(OutboxNotification(uuid4(), {"reason": "timeout"}, attempts=3))
    assert (
        await NotificationWorker(failed_outbox, retry_sender, max_attempts=3).run_once(
            "worker-a", 30
        )
        == "failed"
    )
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


def test_postgres_outbox_uses_unique_claim_tokens_for_overlapping_worker_instances() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.statements: list[tuple[str, tuple[Any, ...]]] = []
            self.rows: list[dict[str, object] | None] = [
                {"id": str(uuid4()), "payload": {"reason": "first"}, "attempts": 1},
                {"id": str(uuid4()), "payload": {"reason": "second"}, "attempts": 1},
            ]

        def execute(self, statement: str, params: tuple[Any, ...] = ()) -> None:
            self.statements.append((statement, params))

        def fetchone(self) -> dict[str, object] | None:
            return self.rows.pop(0) if self.rows else None

    class Connection:
        def __init__(self) -> None:
            self.cursor_value = Cursor()

        def cursor(self) -> Cursor:
            return self.cursor_value

        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

    connection = Connection()
    outbox = PostgresNotificationOutbox(lambda: connection)
    first = outbox.claim("notification-sender", 30)
    second = outbox.claim("notification-sender", 30)

    assert first is not None and second is not None
    assert first.claim_token != second.claim_token
    outbox.mark_sent(first.id, first.claim_token)
    claim_owners = [params[0] for _, params in connection.cursor_value.statements[:2]]
    sent_params = connection.cursor_value.statements[2][1]
    assert claim_owners == [first.claim_token, second.claim_token]
    assert sent_params[-1] == first.claim_token
