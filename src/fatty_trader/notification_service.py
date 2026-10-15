"""Standalone, closed-by-default Telegram + web-push notification sender service."""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from collections.abc import Mapping

from fatty_trader.config.notifications import TelegramNotificationSettings
from fatty_trader.notifications import (
    NotificationWorker,
    PostgresNotificationOutbox,
    TelegramBotSender,
)
from fatty_trader.web_push import (
    PostgresWebPushStore,
    WebPushConfig,
    WebPushDeliveryWorker,
    WebPushSender,
)
from fatty_trader.worker_health import owned_worker_health, worker_progress

_PRUNE_INTERVAL_SECONDS = 86_400.0
_DELIVERY_RETENTION_DAYS = 30


def notification_settings(environ: Mapping[str, str]) -> TelegramNotificationSettings | None:
    """Return complete bot configuration, otherwise leave the Telegram lane inert."""
    try:
        return TelegramNotificationSettings.from_mapping(environ)
    except ValueError:
        return None


def web_push_settings(environ: Mapping[str, str]) -> WebPushConfig | None:
    """Return complete VAPID configuration, otherwise leave the push lane inert."""
    return WebPushConfig.from_environ(environ)


@owned_worker_health("notification-sender")
async def run(environ: Mapping[str, str]) -> None:
    settings = notification_settings(environ)
    push_config = web_push_settings(environ)
    poll_seconds = _positive_float(environ, "NOTIFICATION_POLL_SECONDS", 5.0)
    if settings is None and push_config is None:
        print(
            "service=notification-sender state=disabled reason=missing-telegram-and-push-config",
            flush=True,
        )
        while True:
            await asyncio.sleep(poll_seconds)
    import psycopg
    from psycopg.rows import dict_row

    push_store: PostgresWebPushStore | None = None
    push_worker: WebPushDeliveryWorker | None = None
    if push_config is not None:
        push_store = PostgresWebPushStore(lambda: psycopg.connect(row_factory=dict_row))
        push_worker = WebPushDeliveryWorker(
            push_store,
            WebPushSender(push_config),
            max_attempts=_positive_int(environ, "WEB_PUSH_MAX_ATTEMPTS", 5),
            retry_seconds=_positive_int(environ, "WEB_PUSH_RETRY_SECONDS", 60),
            batch_size=_positive_int(environ, "WEB_PUSH_BATCH_SIZE", 10),
        )
    else:
        print(
            "service=notification-sender state=push-disabled reason=missing-vapid-config",
            flush=True,
        )
    worker: NotificationWorker | None = None
    if settings is not None:
        worker = NotificationWorker(
            PostgresNotificationOutbox(psycopg.connect),
            TelegramBotSender(settings),
            max_attempts=_positive_int(environ, "NOTIFICATION_MAX_ATTEMPTS", 10),
            retry_seconds=_positive_int(environ, "NOTIFICATION_RETRY_SECONDS", 30),
            fan_out=push_store.fan_out if push_store is not None else None,
        )
    lease_seconds = _positive_int(environ, "NOTIFICATION_LEASE_SECONDS", 30)
    last_prune = time.monotonic()
    while True:
        state = await worker.run_once("notification-sender", lease_seconds) if worker else "idle"
        if push_worker is not None:
            try:
                push_state = await push_worker.run_once("notification-sender", lease_seconds)
            except Exception as exc:
                # A push-ledger or transport fault must never suppress the
                # independent Telegram lane; surface it loudly in the log.
                push_state = "error"
                print(
                    f"service=notification-sender push_error={type(exc).__name__}",
                    flush=True,
                )
        else:
            push_state = "idle"
        if state in {"idle", "sent"} and push_state in {"idle", "delivered"}:
            worker_progress()
        print(
            f"service=notification-sender state={state} push={push_state}",
            flush=True,
        )
        if push_store is not None and time.monotonic() - last_prune > _PRUNE_INTERVAL_SECONDS:
            last_prune = time.monotonic()
            try:
                removed = push_store.prune_finished(_DELIVERY_RETENTION_DAYS)
                print(f"service=notification-sender push_pruned={removed}", flush=True)
            except Exception:
                print("service=notification-sender push_prune_error", flush=True)
        await asyncio.sleep(0 if state == "sent" else poll_seconds)


def _positive_int(environ: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_float(environ: Mapping[str, str], name: str, default: float) -> float:
    try:
        value = float(environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Fatty Telegram + web-push notification sender")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        from fatty_trader.worker_health import check_worker_health

        return check_worker_health("notification-sender", os.environ)
    asyncio.run(run(os.environ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
