"""Small, isolated process entry points used by Docker Compose.

The worker implementations are intentionally conservative until their domain queues
are implemented: they expose a stable command boundary and stay PAPER-only.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fatty_trader.config.telegram import TelegramSettings
from fatty_trader.intake.persistence import PostgresRawMessageRepository
from fatty_trader.intake.telegram import TelegramForwarder
from fatty_trader.intake.telethon_client import build_telethon_client
from fatty_trader.storage.schema import INITIAL_SCHEMA_SQL

SUPPORTED_SERVICES = (
    "intake",
    "analyzer",
    "dispatcher-binance",
    "dispatcher-bitget",
    "monitor-binance",
    "monitor-bitget",
    "operator-bot",
)


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    mode: str
    required_credentials: tuple[str, ...]
    allowed_environment: tuple[str, ...]


_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "intake": (
        "TG_API_ID",
        "TG_API_HASH",
        "TELEGRAM_SESSION",
        "TELEGRAM_SOURCE_CHANNELS",
        "TELEGRAM_TARGET_CHAT_ID",
    ),
    "analyzer": (),
    "dispatcher-binance": ("BINANCE_API_KEY", "BINANCE_API_SECRET"),
    "dispatcher-bitget": ("BITGET_API_KEY", "BITGET_API_SECRET"),
    "monitor-binance": ("BINANCE_API_KEY", "BINANCE_API_SECRET"),
    "monitor-bitget": ("BITGET_API_KEY", "BITGET_API_SECRET"),
    "operator-bot": ("TG_BOT_TOKEN", "TG_OPERATOR_ID"),
}


def service_config(name: str, environ: Mapping[str, str]) -> ServiceConfig:
    """Return a paper-first config and the credential names this process may use."""
    if name not in SUPPORTED_SERVICES:
        raise ValueError(f"unsupported service: {name}")
    mode = environ.get("TRADER_MODE", "PAPER").upper()
    if mode != "PAPER":
        raise ValueError("only PAPER mode is enabled by this local topology")
    credentials = _CREDENTIALS[name]
    common = ("TRADER_MODE", "SERVICE_NAME", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER")
    return ServiceConfig(name, mode, credentials, common + credentials)


def apply_schema() -> None:
    """Apply the bootstrap schema when invoked by the migration container."""
    import psycopg

    with psycopg.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(INITIAL_SCHEMA_SQL)
        connection.commit()


async def run_worker(name: str) -> None:
    config = service_config(name, os.environ)
    if name == "intake":
        await run_intake(os.environ)
        return
    interval = float(os.environ.get("WORKER_HEARTBEAT_SECONDS", "30"))
    while True:
        # Keep this boundary observable without writing secrets or business payloads.
        print(f"service={config.name} mode={config.mode} state=ready", flush=True)
        await asyncio.sleep(interval)


def intake_settings(environ: Mapping[str, str]) -> TelegramSettings | None:
    """Return settings only when fully configured; missing config disables intake."""
    try:
        return TelegramSettings.from_mapping(environ)
    except ValueError:
        return None


async def run_intake(
    environ: Mapping[str, str],
    *,
    client_factory: Any = build_telethon_client,
    repository: Any = None,
) -> None:
    """Run the real Telethon intake, or remain inert when config is absent."""
    settings = intake_settings(environ)
    if settings is None:
        print("service=intake mode=PAPER state=disabled reason=missing-telegram-config", flush=True)
        while True:
            await asyncio.sleep(float(environ.get("WORKER_HEARTBEAT_SECONDS", "30")))
    client = client_factory(settings)
    if repository is None:
        import psycopg

        repository = PostgresRawMessageRepository(psycopg.connect)
    forwarder = TelegramForwarder(client, settings, repository)
    await forwarder.attach()
    await client.start()
    print("service=intake mode=PAPER state=ready", flush=True)
    await client.run_until_disconnected()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fatty trader isolated service runner")
    parser.add_argument("--service", required=True)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.service == "migrate":
        if args.check:
            return 0
        apply_schema()
        return 0
    if args.service == "init":
        return 0
    if args.service == "web":
        return 0
    if args.check:
        service_config(args.service, os.environ)
        return 0
    asyncio.run(run_worker(args.service))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
