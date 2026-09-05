"""Standalone, closed-by-default Telegram notification sender service."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Mapping

from fatty_trader.config.notifications import TelegramNotificationSettings
from fatty_trader.notifications import (
    NotificationWorker,
    PostgresNotificationOutbox,
    TelegramBotSender,
)


def notification_settings(environ: Mapping[str, str]) -> TelegramNotificationSettings | None:
    """Return complete bot configuration, otherwise leave the service inert."""
    try:
        return TelegramNotificationSettings.from_mapping(environ)
    except ValueError:
        return None


async def run(environ: Mapping[str, str]) -> None:
    settings = notification_settings(environ)
    if settings is None:
        print(
            "service=notification-sender state=disabled reason=missing-telegram-config",
            flush=True,
        )
        while True:
            await asyncio.sleep(_positive_float(environ, "NOTIFICATION_POLL_SECONDS", 30.0))
    import psycopg

    worker = NotificationWorker(
        PostgresNotificationOutbox(psycopg.connect),
        TelegramBotSender(settings),
        max_attempts=_positive_int(environ, "NOTIFICATION_MAX_ATTEMPTS", 10),
        retry_seconds=_positive_int(environ, "NOTIFICATION_RETRY_SECONDS", 30),
    )
    poll_seconds = _positive_float(environ, "NOTIFICATION_POLL_SECONDS", 5.0)
    lease_seconds = _positive_int(environ, "NOTIFICATION_LEASE_SECONDS", 30)
    while True:
        state = await worker.run_once("notification-sender", lease_seconds)
        print(f"service=notification-sender state={state}", flush=True)
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
    parser = argparse.ArgumentParser(description="Fatty Telegram notification sender")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        # Missing bot configuration is valid: this service deliberately stays inert.
        notification_settings(os.environ)
        return 0
    asyncio.run(run(os.environ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
