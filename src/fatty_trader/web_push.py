"""iOS/Web Push delivery for console notifications (VAPID, RFC 8030/8291).

Two processes share these tables: the authenticated console web API registers
and disables subscriptions, and the notification sender worker fans each newly
claimed outbox notification out to every enabled subscription and drives the
per-subscription delivery ledger with bounded retries. Nothing here is a trading
authority; a push is transport evidence only.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html import unescape
from typing import Any, Protocol
from uuid import UUID, uuid4

from fatty_trader.notifications import NotificationPayloadError, format_notification_html

_SEND_TIMEOUT_SECONDS = 15.0
_MIN_LEASE_SECONDS = 15
_DEFAULT_TTL_SECONDS = 3600
_TAG_LIMIT = 64
_TITLE_LIMIT = 80
_BODY_LIMIT = 400
_TAG_PATTERN = re.compile(r"<[^>]+>")


class WebPushGone(Exception):
    """The push service reports the subscription is permanently gone (404/410)."""


class WebPushRetryable(Exception):
    """Transient transport/5xx/429 failure; the ledger schedules another attempt."""


class WebPushFailed(Exception):
    """Permanent failure for this delivery; retrying the same payload is pointless."""


@dataclass(frozen=True)
class WebPushConfig:
    """Complete sender configuration; absence leaves web push visibly disabled."""

    private_key: str
    subject: str
    ttl_seconds: int = _DEFAULT_TTL_SECONDS

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> WebPushConfig | None:
        path = environ.get("WEB_PUSH_VAPID_PRIVATE_KEY_FILE", "").strip()
        if not path:
            return None
        from pathlib import Path

        try:
            key = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not key:
            return None
        subject = environ.get("WEB_PUSH_VAPID_SUBJECT", "").strip() or "https://localhost"
        return cls(private_key=key, subject=subject, ttl_seconds=_ttl(environ))


def _ttl(environ: Mapping[str, str]) -> int:
    raw = environ.get("WEB_PUSH_TTL_SECONDS", str(_DEFAULT_TTL_SECONDS))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("WEB_PUSH_TTL_SECONDS must be a positive integer") from exc
    if value < 1:
        raise ValueError("WEB_PUSH_TTL_SECONDS must be a positive integer")
    return value


def read_public_key_file(environ: Mapping[str, str]) -> str | None:
    """Return the configured browser-facing VAPID public key, else None."""
    path = environ.get("CONSOLE_VAPID_PUBLIC_KEY_FILE", "").strip()
    if not path:
        return None
    from pathlib import Path

    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def plain_text_from_html(html: str) -> str:
    """Collapse the bounded Telegram HTML rendering into push-safe plain text."""
    text = _TAG_PATTERN.sub("", html)
    return unescape(text).strip()


_KIND_TITLES: Mapping[str, str] = {
    "signal-analysis": "Signal analyzed",
    "source-forward": "Source message",
    "execution-event": "Trade update",
    "execution-alert": "Trade alert",
    "console-health-report": "Console health report",
    "heartbeat": "Heartbeat",
    "kaka-paper-digest": "Paper digest",
    "push-test": "Push test",
}


def format_push_text(payload: Mapping[str, Any]) -> tuple[str, str]:
    """Derive a bounded (title, body) pair from an outbox payload."""
    kind = str(payload.get("kind", "Operator alert"))
    title = _KIND_TITLES.get(kind, "Fatty Trader: " + kind)
    html = format_notification_html(payload)
    body = plain_text_from_html(html)
    return title[:_TITLE_LIMIT], body[:_BODY_LIMIT]


def build_push_message(notification_id: UUID | str, payload: Mapping[str, Any]) -> str:
    """Render the JSON payload a service worker displays; content is pre-bounded."""
    title, body = format_push_text(payload)
    return json.dumps(
        {
            "title": title,
            "body": body,
            "url": "/notifications",
            "tag": str(notification_id)[:_TAG_LIMIT],
        }
    )


@dataclass(frozen=True)
class PushSubscription:
    id: str
    endpoint: str
    p256dh: str
    auth: str


@dataclass(frozen=True)
class PushDelivery:
    notification_id: UUID
    payload: Mapping[str, Any]
    attempts: int
    subscription: PushSubscription
    claim_token: str


class PushTransport(Protocol):
    """Performs one push POST; raises the classified failure types above."""

    async def __call__(self, subscription: PushSubscription, message: str) -> None: ...


class WebPushSender:
    """Classified pywebpush transport; sanitized failures only, never raw bodies."""

    def __init__(self, config: WebPushConfig) -> None:
        self._config = config

    async def __call__(self, subscription: PushSubscription, message: str) -> None:
        subscription_info = {
            "endpoint": subscription.endpoint,
            "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
        }
        await self._post(subscription_info, message)

    async def _post(self, subscription_info: Mapping[str, Any], message: str) -> None:
        try:
            await asyncio.to_thread(self._sync_post, dict(subscription_info), message)
        except (WebPushGone, WebPushRetryable, WebPushFailed):
            raise
        except Exception:
            raise WebPushRetryable() from None

    def _sync_post(self, subscription_info: dict[str, Any], message: str) -> None:
        from pywebpush import WebPushException, webpush

        try:
            response = webpush(
                subscription_info=subscription_info,
                data=message,
                vapid_private_key=self._config.private_key,
                vapid_claims={"sub": self._config.subject},
                ttl=self._config.ttl_seconds,
                timeout=10,
            )
        except WebPushException as exc:
            raise _classify(getattr(exc.response, "status_code", None)) from None
        except Exception:
            raise WebPushRetryable() from None
        if response.status_code >= 400:
            raise _classify(response.status_code)


def _classify(status: int | None) -> Exception:
    if status in {404, 410}:
        return WebPushGone()
    if status is None or status == 429 or status >= 500:
        return WebPushRetryable()
    return WebPushFailed()


class WebPushStore(Protocol):
    def fan_out(self, notification_id: UUID) -> int: ...
    def claim_due(self, worker_id: str, lease_seconds: int, limit: int) -> list[PushDelivery]: ...
    def mark_sent(self, delivery: PushDelivery) -> bool: ...
    def mark_retry(self, delivery: PushDelivery, delay_seconds: int) -> bool: ...
    def mark_failed(self, delivery: PushDelivery, reason: str) -> bool: ...
    def disable_subscription(self, subscription_id: str, reason: str) -> bool: ...
    def prune_finished(self, retention_days: int) -> int: ...


class PostgresWebPushStore:
    """Token-fenced push ledger boundary; mirrors the outbox claim/lease pattern."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def fan_out(self, notification_id: UUID) -> int:
        """Create the pending delivery rows for every enabled subscription once."""
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO web_push_deliveries (notification_id, subscription_id)
                SELECT %s, s.id FROM web_push_subscriptions s
                WHERE s.disabled_at IS NULL
                ON CONFLICT (notification_id, subscription_id) DO NOTHING
                """,
                (str(notification_id),),
            )
            created = int(cursor.rowcount)
            connection.commit()
            return created
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def claim_due(self, worker_id: str, lease_seconds: int, limit: int) -> list[PushDelivery]:
        if lease_seconds < _MIN_LEASE_SECONDS:
            raise ValueError(f"push lease must be at least {_MIN_LEASE_SECONDS} seconds")
        if limit < 1:
            return []
        claim_token = f"{worker_id}:{uuid4()}"
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                WITH due AS (
                    SELECT d.notification_id, d.subscription_id
                    FROM web_push_deliveries d
                    WHERE d.sent_at IS NULL AND d.failed_at IS NULL
                      AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= now())
                      AND (d.claimed_by IS NULL OR d.lease_until <= now())
                    ORDER BY d.created_at, d.notification_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                UPDATE web_push_deliveries d
                SET claimed_by = %s,
                    lease_until = now() + (%s * interval '1 second'),
                    attempts = d.attempts + 1
                FROM due, web_push_subscriptions s, notifications_outbox n
                WHERE d.notification_id = due.notification_id
                  AND d.subscription_id = due.subscription_id
                  AND s.id = d.subscription_id
                  AND n.id = d.notification_id
                RETURNING d.notification_id, d.attempts, n.payload,
                          s.id, s.endpoint, s.p256dh, s.auth
                """,
                (limit, claim_token, lease_seconds),
            )
            rows = list(cursor.fetchall())
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        deliveries: list[PushDelivery] = []
        for row in rows:
            values = (
                row
                if isinstance(row, dict)
                else {
                    "notification_id": row[0],
                    "attempts": row[1],
                    "payload": row[2],
                    "id": row[3],
                    "endpoint": row[4],
                    "p256dh": row[5],
                    "auth": row[6],
                }
            )
            payload = values["payload"]
            if not isinstance(payload, Mapping):
                continue
            deliveries.append(
                PushDelivery(
                    notification_id=UUID(str(values["notification_id"])),
                    payload=payload,
                    attempts=int(values["attempts"]),
                    subscription=PushSubscription(
                        id=str(values["id"]),
                        endpoint=str(values["endpoint"]),
                        p256dh=str(values["p256dh"]),
                        auth=str(values["auth"]),
                    ),
                    claim_token=claim_token,
                )
            )
        return deliveries

    def mark_sent(self, delivery: PushDelivery) -> bool:
        return self._update(
            """UPDATE web_push_deliveries
               SET sent_at = now(), claimed_by = NULL, lease_until = NULL, last_error = NULL
               WHERE notification_id = %s AND subscription_id = %s AND claimed_by = %s
                 AND sent_at IS NULL AND failed_at IS NULL""",
            delivery,
        )

    def mark_retry(self, delivery: PushDelivery, delay_seconds: int) -> bool:
        return self._update(
            """UPDATE web_push_deliveries
               SET claimed_by = NULL, lease_until = NULL,
                   next_attempt_at = now() + (%s * interval '1 second'),
                   last_error = %s
               WHERE notification_id = %s AND subscription_id = %s AND claimed_by = %s
                 AND sent_at IS NULL AND failed_at IS NULL""",
            delivery,
            delay_seconds,
            "retryable",
        )

    def mark_failed(self, delivery: PushDelivery, reason: str) -> bool:
        return self._update(
            """UPDATE web_push_deliveries
               SET failed_at = now(), claimed_by = NULL, lease_until = NULL, last_error = %s
               WHERE notification_id = %s AND subscription_id = %s AND claimed_by = %s
                 AND sent_at IS NULL AND failed_at IS NULL""",
            delivery,
            reason,
        )

    def _update(self, sql: str, delivery: PushDelivery, *extra: Any) -> bool:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                sql,
                (
                    *extra,
                    str(delivery.notification_id),
                    delivery.subscription.id,
                    delivery.claim_token,
                ),
            )
            updated = cursor.rowcount == 1
            connection.commit()
            return bool(updated)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def disable_subscription(self, subscription_id: str, reason: str) -> bool:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """UPDATE web_push_subscriptions
                   SET disabled_at = now(), last_error = %s
                   WHERE id = %s AND disabled_at IS NULL""",
                (reason[:200], subscription_id),
            )
            updated = cursor.rowcount == 1
            connection.commit()
            return bool(updated)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def prune_finished(self, retention_days: int) -> int:
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """DELETE FROM web_push_deliveries
                   WHERE (sent_at IS NOT NULL OR failed_at IS NOT NULL)
                     AND created_at < now() - (%s * interval '1 day')""",
                (retention_days,),
            )
            removed = int(cursor.rowcount)
            connection.commit()
            return removed
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class WebPushDeliveryWorker:
    """Deliver due push rows with bounded retries; never blocks the Telegram path."""

    def __init__(
        self,
        store: WebPushStore,
        sender: PushTransport,
        *,
        max_attempts: int = 5,
        retry_seconds: int = 60,
        batch_size: int = 10,
    ) -> None:
        if max_attempts < 1 or retry_seconds < 1 or batch_size < 1:
            raise ValueError("web push worker limits must be positive")
        self._store = store
        self._sender = sender
        self._max_attempts = max_attempts
        self._retry_seconds = retry_seconds
        self._batch_size = batch_size

    async def run_once(self, worker_id: str, lease_seconds: int) -> str:
        deliveries = self._store.claim_due(worker_id, lease_seconds, self._batch_size)
        if not deliveries:
            return "idle"
        delivered = 0
        for delivery in deliveries:
            try:
                message = build_push_message(delivery.notification_id, delivery.payload)
            except NotificationPayloadError:
                self._store.mark_failed(delivery, "invalid-payload")
                continue
            try:
                async with asyncio.timeout(_SEND_TIMEOUT_SECONDS):
                    await self._sender(delivery.subscription, message)
            except WebPushGone:
                self._store.disable_subscription(delivery.subscription.id, "subscription-gone")
                self._store.mark_failed(delivery, "subscription-gone")
            except WebPushRetryable:
                if delivery.attempts >= self._max_attempts:
                    self._store.mark_failed(delivery, "attempts-exhausted")
                else:
                    delay = self._retry_seconds * min(delivery.attempts, self._max_attempts)
                    self._store.mark_retry(delivery, delay)
            except Exception:
                self._store.mark_failed(delivery, "permanent")
            else:
                self._store.mark_sent(delivery)
                delivered += 1
        return "delivered" if delivered else "attempted"
