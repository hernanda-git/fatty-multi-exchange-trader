"""Durable Telegram bot notification delivery from ``notifications_outbox``."""

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
    claim_token: str = ""


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
        claim_token = f"{worker_id}:{uuid4()}"
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
                (claim_token, lease_seconds),
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
        return OutboxNotification(
            UUID(str(values["id"])), payload, int(values["attempts"]), claim_token
        )

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
        claim_token = notification.claim_token or worker_id
        try:
            await self._sender.send(format_notification_html(notification.payload))
        except NotificationDeliveryError as exc:
            if not exc.retryable or notification.attempts >= self._max_attempts:
                self._outbox.mark_failed(notification.id, claim_token)
                return "failed"
            self._outbox.mark_retry(
                notification.id,
                claim_token,
                self._retry_seconds * min(notification.attempts, self._max_attempts),
            )
            return "retry"
        self._outbox.mark_sent(notification.id, claim_token)
        return "sent"


def format_notification_html(payload: Mapping[str, Any]) -> str:
    """Render arbitrary outbox JSON as bounded, escaped Telegram HTML."""
    if payload.get("kind") == "heartbeat":
        return _format_heartbeat_html(payload)
    if payload.get("kind") == "source-forward":
        return format_source_forward_html(payload)
    if payload.get("kind") == "signal-analysis":
        return _format_signal_analysis_html(payload)
    if payload.get("kind") == "execution-event":
        return _format_execution_event_html(payload)
    if payload.get("kind") == "execution-alert":
        return _format_execution_alert_html(payload)
    if payload.get("kind") == "system-event":
        return _format_system_event_html(payload)
    title = _safe_text(payload.get("kind", "Operator alert"), limit=100)
    lines = [f"<b>Fatty Trader: {escape(title)}</b>"]
    for key in sorted(payload):
        if key == "kind":
            continue
        value = "[redacted]" if _SECRET_KEY.search(str(key)) else _safe_value(payload[key])
        lines.append(f"<b>{escape(str(key).replace('_', ' ').title())}:</b> {escape(value)}")
    return "\n".join(lines)[:4000]


def format_source_forward_html(payload: Mapping[str, Any]) -> str:
    """Render a durable source relay without attempting provider-side media mutation."""
    message_id = payload.get("source_message_id")
    text = _safe_text(payload.get("raw_text", ""), limit=3500)
    suffix = (
        "\n\n<i>Lampiran media terdeteksi; teks belum diekstrak.</i>"
        if payload.get("has_media")
        else ""
    )
    return (
        "<b>Pesan sumber diterima</b>\n"
        f"Referensi: <code>#{escape(str(message_id))}</code>\n\n"
        f"{escape(text)}{suffix}"
    )[:4000]


def _format_signal_analysis_html(payload: Mapping[str, Any]) -> str:
    source_id = escape(_safe_value(payload.get("source_message_id", "?")))
    source_received_at = _format_timestamp(str(payload.get("source_received_at", "")))
    if payload.get("canonical_signal") is not True:
        management_action = payload.get("management_action")
        management_symbol = escape(_safe_value(payload.get("management_symbol", "")))
        source_text = escape(_safe_text(payload.get("source_text", ""), limit=1000))
        if management_action == "TP1_BOOKED":
            icon = "🟢"
            heading = f"Manajemen Posisi · {management_symbol}"
            detail = "TP1 booked terdeteksi dari source trader."
        elif management_action == "SL_TO_ENTRY":
            icon = "🟡"
            heading = f"Manajemen Posisi · {management_symbol}"
            detail = "SL to entry terdeteksi dari source trader."
        elif management_action == "CLOSE":
            icon = "🔴"
            heading = f"Manajemen Posisi · {management_symbol}"
            detail = "Instruksi close terdeteksi dari source trader."
        else:
            icon = "⚪"
            heading = "Update Sumber"
            detail = "Tidak ada order dibuat · bukan setup baru"
        return (
            f"{icon} <b>{escape(heading)}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{source_text}\n\n"
            f"<i>{detail}</i>\n"
            f"ID: <code>#{source_id}</code>\n"
            f"Waktu: <code>{escape(source_received_at)}</code>"
        )
    pair = escape(_safe_value(payload.get("pair", "?")))
    direction = escape(_safe_value(payload.get("direction", "?")))
    entry = escape(_safe_value(payload.get("entry", "?")))
    stop_loss = escape(_safe_value(payload.get("stop_loss", "?")))
    take_profits = escape(_safe_value(payload.get("take_profits", [])))
    dispatches = payload.get("dispatches", 0)
    try:
        dispatch_count = max(0, int(dispatches))
    except (TypeError, ValueError):
        dispatch_count = 0
    process_label = f"{dispatch_count} proses eksekusi dibuat"
    direction_icon = "🟢" if direction == "LONG" else "🔴" if direction == "SHORT" else "⚪"
    return (
        f"📊 <b>Setup Terdeteksi · {pair} {direction_icon} {direction}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Entry  : <code>{entry}</code>\n"
        f"SL     : <code>{stop_loss}</code>\n"
        f"TP     : <code>{take_profits}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Status : {process_label}\n"
        f"ID: <code>#{source_id}</code>\n"
        f"Waktu: <code>{escape(source_received_at)}</code>"
    )[:4000]


def _format_execution_event_html(payload: Mapping[str, Any]) -> str:
    reason = str(payload.get("reason", ""))
    dispatch_id = escape(_safe_value(payload.get("dispatch_id", "")))
    if reason == "cutover-gated":
        return (
            "⛔ <b>Eksekusi Diblokir</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Tidak ada order dikirim.\n"
            "Alasan: mode DEMO · eksekusi live belum diaktifkan.\n"
            f"Dispatch: <code>{dispatch_id}</code>"
        )
    state = escape(_safe_value(payload.get("to_state", "diperbarui")))
    return (
        f"📈 <b>Status Eksekusi</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Status: <code>{state}</code>\n"
        f"Alasan: <code>{escape(reason)}</code>\n"
        f"Dispatch: <code>{dispatch_id}</code>"
    )


def _format_execution_alert_html(payload: Mapping[str, Any]) -> str:
    reason = str(payload.get("reason", ""))
    dispatch_id = escape(_safe_value(payload.get("dispatch_id", "")))
    if reason == "cutover-gated":
        return _format_execution_event_html(payload)
    return (
        "⚠️ <b>Perhatian Eksekusi</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Alasan: <code>{escape(reason)}</code>\n"
        f"Dispatch: <code>{dispatch_id}</code>\n"
        "Perlu pemeriksaan operator."
    )


def _format_system_event_html(payload: Mapping[str, Any]) -> str:
    message = escape(_safe_text(payload.get("message", ""), limit=1000))
    return (
        "🤖 <b>System Event</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{message}"
    )


def _format_heartbeat_html(payload: Mapping[str, Any]) -> str:
    """Render heartbeat cards in the established rich Telegram report layout."""

    def value(key: str, default: str = "N/A") -> str:
        return escape(_safe_value(payload.get(key, default)))

    report = (
        "<b>Fatty Trader</b>  <i>Ringkasan Operasional</i>\n\n"
        "<b>Kesehatan</b>\n"
        "<pre>Status   🟢 ONLINE\n"
        f"Mode     {value('mode')}\n"
        f"Venue    {value('venue_mode')}\n"
        f"Host     {value('host')}\n"
        f"Sumber   {value('source')}</pre>\n\n"
        "<b>Pesan terbaru</b>\n"
        f"Referensi <code>#{value('latest_source_message_id')}</code>\n"
        f"Diterima  <code>{value('latest_source_received_at')}</code>\n\n"
        "<b>Data sistem</b>\n"
        f"<pre>Pesan masuk       {value('raw_messages')}\n"
        f"Menunggu analisis  {value('received')}\n"
        f"Selesai dianalisis {value('analyzed')}\n"
        f"Gagal dianalisis   {value('failed')}\n"
        f"Setup valid        {value('canonical_signals')}\n"
        f"Dispatch           {value('dispatches')}\n"
        f"Live intents       {value('live_order_intents')}</pre>\n\n"
        "<b>Notifikasi</b>\n"
        f"<pre>Antrean           {value('notification_pending')}\n"
        f"Gagal             {value('notification_failed')}</pre>\n\n"
        "<b>Perlindungan</b>\n"
        f"<pre>Mode              {value('mode')}\n"
        f"Eksekusi aktif    {value('execution_enabled')}\n"
        f"Codex             {value('codex')}</pre>"
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


def _format_timestamp(iso_string: str) -> str:
    """Format ISO timestamp to human-readable form in GMT+7 Jakarta time."""
    if not iso_string:
        return "?"
    try:
        from datetime import datetime, timezone, timedelta
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        jakarta_tz = timezone(timedelta(hours=7))
        dt_jakarta = dt.astimezone(jakarta_tz)
        return dt_jakarta.strftime("%d %b %Y, %H:%M WIB")
    except (ValueError, TypeError):
        return iso_string
