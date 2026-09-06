"""Durable Telegram bot notification delivery from ``notifications_outbox``.

The worker owns lease-safe claiming and records only delivery state. It never logs
bot tokens, payloads, Telegram response bodies, or transport exception text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html import escape
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx

from fatty_trader.config.notifications import TelegramNotificationSettings

_SECRET_KEY = re.compile(
    r"(?:token|secret|password|api[_-]?key|authorization|session|cookie|passphrase)", re.I
)
_INLINE_SECRET = re.compile(
    r"(?i)\b(token|secret|password|api[_-]?key|authorization|session|cookie|passphrase)"
    r"\s*[=:]\s*[^\s<]+"
)
_BOT_TOKEN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")


@dataclass(frozen=True)
class OutboxNotification:
    id: UUID
    payload: Mapping[str, Any]
    attempts: int


class NotificationOutbox(Protocol):
    def claim(self, worker_id: str, lease_seconds: int) -> OutboxNotification | None: ...

    def mark_sent(self, notification_id: UUID, worker_id: str) -> None: ...

    def mark_retry(self, notification_id: UUID, worker_id: str, delay_seconds: int) -> None: ...

    def mark_failed(self, notification_id: UUID, worker_id: str) -> None: ...


class NotificationSender(Protocol):
    async def send(self, text: str) -> None: ...


class NotificationDeliveryError(Exception):
    """Sanitized classification of a Telegram delivery failure."""

    def __init__(self, *, retryable: bool) -> None:
        super().__init__("notification delivery failed")
        self.retryable = retryable


class TelegramBotSender:
    """Minimal Bot API client; only sanitized status is exposed to the worker."""

    def __init__(self, settings: TelegramNotificationSettings) -> None:
        self._target_chat_id = settings.target_chat_id
        self._endpoint = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"

    async def send(self, text: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self._endpoint,
                    json={
                        "chat_id": self._target_chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
        except httpx.HTTPError as exc:
            raise NotificationDeliveryError(retryable=True) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise NotificationDeliveryError(retryable=True)
        if response.status_code >= 400:
            raise NotificationDeliveryError(retryable=False)
        try:
            delivered = bool(response.json().get("ok"))
        except (TypeError, ValueError):
            delivered = False
        if not delivered:
            raise NotificationDeliveryError(retryable=True)


class PostgresNotificationOutbox:
    """Lease-safe PostgreSQL queue boundary for Telegram notifications."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def claim(self, worker_id: str, lease_seconds: int) -> OutboxNotification | None:
        if not worker_id:
            raise ValueError("worker_id is required")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                WITH next_notification AS (
                    SELECT id
                    FROM notifications_outbox
                    WHERE sent_at IS NULL
                      AND failed_at IS NULL
                      AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                      AND (claimed_by IS NULL OR lease_until <= now())
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE notifications_outbox outbox
                SET claimed_by = %s,
                    lease_until = now() + (%s * interval '1 second'),
                    attempts = attempts + 1
                FROM next_notification next
                WHERE outbox.id = next.id
                RETURNING outbox.id, outbox.payload, outbox.attempts
                """,
                (worker_id, lease_seconds),
            )
            row = cursor.fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        if row is None:
            return None
        values = (
            row if isinstance(row, dict) else {"id": row[0], "payload": row[1], "attempts": row[2]}
        )
        payload = values["payload"]
        if not isinstance(payload, Mapping):
            raise ValueError("notification payload must be an object")
        return OutboxNotification(UUID(str(values["id"])), payload, int(values["attempts"]))

    def mark_sent(self, notification_id: UUID, worker_id: str) -> None:
        self._update_claimed(
            """UPDATE notifications_outbox
               SET sent_at = now(), claimed_by = NULL, lease_until = NULL
               WHERE id = %s AND claimed_by = %s AND sent_at IS NULL AND failed_at IS NULL""",
            notification_id,
            worker_id,
        )

    def mark_retry(self, notification_id: UUID, worker_id: str, delay_seconds: int) -> None:
        self._update_claimed(
            """UPDATE notifications_outbox
               SET claimed_by = NULL, lease_until = NULL,
                   next_attempt_at = now() + (%s * interval '1 second')
               WHERE id = %s AND claimed_by = %s AND sent_at IS NULL AND failed_at IS NULL""",
            notification_id,
            worker_id,
            delay_seconds,
        )

    def mark_failed(self, notification_id: UUID, worker_id: str) -> None:
        self._update_claimed(
            """UPDATE notifications_outbox
               SET failed_at = now(), claimed_by = NULL, lease_until = NULL
               WHERE id = %s AND claimed_by = %s AND sent_at IS NULL AND failed_at IS NULL""",
            notification_id,
            worker_id,
        )

    def _update_claimed(
        self, statement: str, notification_id: UUID, worker_id: str, *extra: int
    ) -> None:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(statement, (*extra, notification_id, worker_id))
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def enqueue_notification(
    connection_factory: Callable[[], Any], *, dedup_key: str, payload: Mapping[str, Any]
) -> None:
    """Persist one operator event for the independent Telegram sender."""
    connection = connection_factory()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO notifications_outbox (id, dedup_key, payload)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (dedup_key) DO NOTHING
            """,
            (uuid4(), dedup_key, json.dumps(dict(payload))),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


class NotificationWorker:
    """Deliver one row at a time, retaining retries and terminal failures durably."""

    def __init__(
        self,
        outbox: NotificationOutbox,
        sender: NotificationSender,
        *,
        max_attempts: int = 10,
        retry_seconds: int = 30,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retry_seconds < 1:
            raise ValueError("retry_seconds must be positive")
        self._outbox = outbox
        self._sender = sender
        self._max_attempts = max_attempts
        self._retry_seconds = retry_seconds

    async def run_once(self, worker_id: str, lease_seconds: int) -> str:
        notification = self._outbox.claim(worker_id, lease_seconds)
        if notification is None:
            return "idle"
        try:
            await self._sender.send(format_notification_html(notification.payload))
        except NotificationDeliveryError as exc:
            if not exc.retryable or notification.attempts >= self._max_attempts:
                self._outbox.mark_failed(notification.id, worker_id)
                return "failed"
            self._outbox.mark_retry(
                notification.id,
                worker_id,
                self._retry_seconds * min(notification.attempts, self._max_attempts),
            )
            return "retry"
        self._outbox.mark_sent(notification.id, worker_id)
        return "sent"


def format_notification_html(payload: Mapping[str, Any]) -> str:
    """Render arbitrary outbox JSON as bounded, escaped Telegram HTML."""
    if payload.get("kind") == "heartbeat":
        return _format_heartbeat_html(payload)
    title = _safe_text(payload.get("kind", "Operator alert"), limit=100)
    lines = [f"<b>Fatty Trader: {escape(title)}</b>"]
    for key in sorted(payload):
        if key == "kind":
            continue
        value = "[redacted]" if _SECRET_KEY.search(str(key)) else _safe_value(payload[key])
        lines.append(f"<b>{escape(str(key).replace('_', ' ').title())}:</b> {escape(value)}")
    # Telegram's HTML subset does not support <br>; literal newlines preserve
    # card readability without causing a permanent Bot API parse failure.
    return "\n".join(lines)[:4000]


def _format_heartbeat_html(payload: Mapping[str, Any]) -> str:
    """Render heartbeat cards in the established rich Telegram report layout."""

    def value(key: str, default: str = "N/A") -> str:
        return escape(_safe_value(payload.get(key, default)))

    report = (
        "<b>Fatty Signal Relay</b>  <i>Paper Ops</i>\n\n"
        "<b>Status</b>\n"
        "<pre>Overall  🟢 ONLINE\n"
        f"Mode     {value('mode')}\n"
        f"Venue    {value('venue_mode')}\n"
        f"Host     {value('host')}\n"
        f"Source   {value('source')}</pre>\n\n"
        "<b>Latest Signal</b>\n"
        f"Message  <code>{value('latest_source_message_id')}</code>\n"
        f"Received <code>{value('latest_source_received_at')}</code>\n\n"
        "<b>Database</b>\n"
        f"<pre>Messages          {value('raw_messages')}\n"
        f"Received          {value('received')}\n"
        f"Analyzed          {value('analyzed')}\n"
        f"Failed            {value('failed')}\n"
        f"Signals           {value('canonical_signals')}\n"
        f"Dispatches        {value('dispatches')}\n"
        f"Live intents      {value('live_order_intents')}</pre>\n\n"
        "<b>Notifications</b>\n"
        f"<pre>Pending           {value('notification_pending')}\n"
        f"Failed            {value('notification_failed')}</pre>\n\n"
        "<b>Safety</b>\n"
        f"<pre>Mode              {value('mode')}\n"
        f"Execution enabled {value('execution_enabled')}\n"
        f"Codex account     {value('codex')}</pre>"
    )
    return report[:4000]


def _safe_value(value: Any) -> str:
    if isinstance(value, Mapping):
        return ", ".join(
            f"{key}=[redacted]" if _SECRET_KEY.search(str(key)) else f"{key}={_safe_value(item)}"
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        )
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_safe_value(item) for item in value)
    return _safe_text(value, limit=1000)


def _safe_text(value: Any, *, limit: int) -> str:
    text = str(value).strip() or "(empty)"
    text = _BOT_TOKEN.sub("[redacted]", text)
    text = _INLINE_SECRET.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    return text[:limit]
