"""Small, isolated process entry points used by Docker Compose.

The worker implementations are intentionally conservative until their domain queues
are implemented: they expose a stable command boundary and stay PAPER-only.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fatty_trader.analyzer.codex_runner import CodexRunner
from fatty_trader.analyzer.postgres_worker import process_received_batch
from fatty_trader.config.telegram import TelegramSettings
from fatty_trader.intake.persistence import PostgresRawMessageRepository
from fatty_trader.intake.telegram import TelegramForwarder
from fatty_trader.intake.telethon_client import build_telethon_client
from fatty_trader.storage.migrations import apply_migrations
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
    venue_mode: str
    execution_enabled: bool
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
    "dispatcher-bitget": ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE"),
    "monitor-binance": ("BINANCE_API_KEY", "BINANCE_API_SECRET"),
    "monitor-bitget": ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE"),
    "operator-bot": ("TG_BOT_TOKEN", "TG_OPERATOR_ID"),
}


def service_config(name: str, environ: Mapping[str, str]) -> ServiceConfig:
    """Return a paper-first config with an isolated Bitget venue mode."""
    if name not in SUPPORTED_SERVICES:
        raise ValueError(f"unsupported service: {name}")
    mode = environ.get("TRADER_MODE", "PAPER").upper()
    if mode != "PAPER":
        raise ValueError("only PAPER mode is enabled by this local topology")
    venue_mode = "PAPER"
    if name in {"dispatcher-bitget", "monitor-bitget"}:
        venue_mode = environ.get("BITGET_MODE", "PAPER").upper()
        if venue_mode not in {"PAPER", "LIVE"}:
            raise ValueError("BITGET_MODE must be PAPER or LIVE")
    credentials = _CREDENTIALS[name]
    execution_enabled = False
    if name == "dispatcher-bitget":
        raw_execution = environ.get("BITGET_EXECUTION_ENABLED", "0").lower()
        if raw_execution not in {"0", "1"}:
            raise ValueError("BITGET_EXECUTION_ENABLED must be 0 or 1")
        execution_enabled = raw_execution == "1"
        if execution_enabled:
            _validate_bitget_cutover(environ)
    common = ("TRADER_MODE", "SERVICE_NAME", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER")
    allowed_environment = common + credentials
    if name == "dispatcher-bitget":
        allowed_environment += ("BITGET_MODE", "BITGET_EXECUTION_ENABLED")
    return ServiceConfig(
        name, mode, venue_mode, execution_enabled, credentials, allowed_environment
    )


def _validate_bitget_cutover(environ: Mapping[str, str]) -> None:
    try:
        canary_max_orders = int(environ.get("BITGET_CANARY_MAX_ORDERS", "0"))
    except ValueError as exc:
        raise ValueError("BITGET_CANARY_MAX_ORDERS must be a positive integer canary cap") from exc
    if canary_max_orders < 1:
        raise ValueError("positive Bitget canary cap is required when execution is enabled")
    canary_symbol = environ.get("BITGET_CANARY_SYMBOL", "")
    if not re.fullmatch(r"[A-Z0-9]+", canary_symbol):
        raise ValueError(
            "valid uppercase Bitget canary symbol is required when execution is enabled"
        )
    if not environ.get("BITGET_APPROVAL_REFERENCE", "").strip():
        raise ValueError("Bitget approval reference is required when execution is enabled")
    try:
        max_clock_skew_ms = int(environ.get("BITGET_MAX_CLOCK_SKEW_MS", "0"))
    except ValueError as exc:
        raise ValueError("Bitget clock skew limit must be a positive integer") from exc
    if max_clock_skew_ms < 1:
        raise ValueError("Bitget clock skew limit must be positive")


def bitget_dispatcher_state(
    environ: Mapping[str, str], *, execution_client_factory: Callable[[], object] | None = None
) -> str:
    """Return the safe dispatcher startup state without touching credentials when gated."""
    config = service_config("dispatcher-bitget", environ)
    if not config.execution_enabled:
        return "cutover-gated"
    if execution_client_factory is None:
        return "execution-not-wired"
    execution_client_factory()
    return "execution-enabled"


async def run_bitget_dispatcher(environ: Mapping[str, str]) -> None:
    """Run the durable dispatcher in observe-only mode until human cutover wiring exists."""
    from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
    from fatty_trader.execution.bitget_dispatcher import BitgetDispatcher, DispatchGate
    from fatty_trader.storage.reconciliation import PostgresReconciliationRepository

    config = service_config("dispatcher-bitget", environ)
    state = bitget_dispatcher_state(environ)
    if state != "cutover-gated":
        raise RuntimeError("Bitget execution is not wired by this observe-only service")
    import psycopg

    dispatcher = BitgetDispatcher(
        PostgresBitgetDispatchRepository(psycopg.connect),
        gate=DispatchGate(execution_enabled=False),
        preflight=lambda _: (_ for _ in ()).throw(RuntimeError("cutover gate is closed")),
        kill_switch=PostgresReconciliationRepository(psycopg.connect),
    )
    interval = float(environ.get("WORKER_HEARTBEAT_SECONDS", "30"))
    lease_seconds = int(environ.get("BITGET_DISPATCH_LEASE_SECONDS", "30"))
    while True:
        cycle_state = await dispatcher.run_once("dispatcher-bitget", lease_seconds)
        print(
            f"service=dispatcher-bitget mode=PAPER venue_mode={config.venue_mode} "
            f"state={cycle_state}",
            flush=True,
        )
        await asyncio.sleep(interval)


async def run_bitget_monitor(environ: Mapping[str, str]) -> None:
    """Run only signed provider GETs and persist fail-closed reconciliation state."""
    import psycopg

    from fatty_trader.exchanges.bitget.client import BitgetRestClient
    from fatty_trader.execution.bitget_monitor import BitgetMonitor
    from fatty_trader.storage.reconciliation import PostgresReconciliationRepository

    config = service_config("monitor-bitget", environ)
    client = BitgetRestClient(
        environ["BITGET_API_KEY"], environ["BITGET_API_SECRET"], environ["BITGET_API_PASSPHRASE"]
    )
    repository = PostgresReconciliationRepository(psycopg.connect)
    monitor = BitgetMonitor(client, repository)
    interval = float(environ.get("WORKER_HEARTBEAT_SECONDS", "30"))
    try:
        while True:
            report = await monitor.run_once()
            print(
                f"service=monitor-bitget mode={config.mode} venue_mode={config.venue_mode} "
                f"state={report.status} reasons={','.join(report.reasons) or 'none'}",
                flush=True,
            )
            await asyncio.sleep(interval)
    finally:
        await client.aclose()


def apply_schema() -> None:
    """Apply the bootstrap schema when invoked by the migration container."""
    import psycopg

    with psycopg.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(INITIAL_SCHEMA_SQL)
            apply_migrations(cursor)
        connection.commit()


async def run_worker(name: str) -> None:
    config = service_config(name, os.environ)
    if name == "intake":
        await run_intake(os.environ)
        return
    if name == "analyzer":
        await run_analyzer(os.environ)
        return
    if name == "dispatcher-bitget":
        await run_bitget_dispatcher(os.environ)
        return
    if name == "monitor-bitget":
        await run_bitget_monitor(os.environ)
        return
    interval = float(os.environ.get("WORKER_HEARTBEAT_SECONDS", "30"))
    while True:
        # Keep this boundary observable without writing secrets or business payloads.
        state = "heartbeat-only"
        print(
            f"service={config.name} mode={config.mode} venue_mode={config.venue_mode} "
            f"state={state}",
            flush=True,
        )
        await asyncio.sleep(interval)


async def run_analyzer(environ: Mapping[str, str]) -> None:
    """Continuously analyze durable RECEIVED rows and enqueue PAPER intents."""
    import psycopg

    runner = CodexRunner()
    account_label = environ.get("CODEX_ACCOUNT_LABEL", "UNCONFIGURED")
    codex_cli = "available" if shutil.which("codex") else "unavailable"
    poll_seconds = float(environ.get("ANALYZER_POLL_SECONDS", "5"))
    batch_size = int(environ.get("ANALYZER_BATCH_SIZE", "10"))
    while True:
        processed = process_received_batch(
            psycopg.connect,
            runner=runner,
            limit=batch_size,
        )
        print(
            f"service=analyzer mode=PAPER state=ready processed={processed} "
            f"codex_cli={codex_cli} codex_account={account_label}",
            flush=True,
        )
        await asyncio.sleep(poll_seconds if processed == 0 else 0)


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
