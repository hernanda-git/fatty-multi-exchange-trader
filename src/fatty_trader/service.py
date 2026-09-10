"""Small, isolated process entry points used by Docker Compose.

The worker implementations are intentionally conservative until their domain queues
are implemented: they expose a stable command boundary and stay closed by default.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from fatty_trader.analyzer.codex_runner import CodexRunner
from fatty_trader.analyzer.postgres_worker import process_received_batch
from fatty_trader.config.telegram import TelegramSettings
from fatty_trader.domain.enums import Exchange, MarginMode
from fatty_trader.domain.models import InstrumentSpec, VenueRiskConfig
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
    "source-management",
)


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    mode: str
    venue_mode: str
    execution_enabled: bool
    required_credentials: tuple[str, ...]
    allowed_environment: tuple[str, ...]


@dataclass(frozen=True)
class BitgetExecutionRuntime:
    """Owned enabled-only dependencies for the live Bitget dispatcher."""

    execution: object
    preflight: Callable[[str], Any]
    client: object


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
    "operator-bot": (
        "TG_BOT_TOKEN",
        "TG_OPERATOR_ID",
        "BITGET_API_KEY",
        "BITGET_API_SECRET",
        "BITGET_API_PASSPHRASE",
    ),
    "source-management": (
        "BITGET_API_KEY",
        "BITGET_API_SECRET",
        "BITGET_API_PASSPHRASE",
    ),
}


def service_config(name: str, environ: Mapping[str, str]) -> ServiceConfig:
    """Return a DEMO-first config with an isolated Bitget venue mode."""
    if name not in SUPPORTED_SERVICES:
        raise ValueError(f"unsupported service: {name}")
    mode = environ.get("TRADER_MODE", "DEMO").upper()
    if mode not in {"DEMO", "LIVE"}:
        raise ValueError("TRADER_MODE must be DEMO or LIVE")
    venue_mode = mode
    if name in {"dispatcher-bitget", "monitor-bitget"}:
        venue_mode = environ.get("BITGET_MODE", "DEMO").upper()
        if venue_mode not in {"DEMO", "LIVE"}:
            raise ValueError("BITGET_MODE must be DEMO or LIVE")
        if venue_mode != mode:
            raise ValueError("TRADER_MODE and BITGET_MODE must match")
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
    approval_reference = environ.get("BITGET_APPROVAL_REFERENCE", "").strip()
    if not approval_reference:
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


def build_bitget_execution_runtime(
    environ: Mapping[str, str],
    *,
    client_factory: Callable[..., object] | None = None,
    intent_store_factory: Callable[[], object] | None = None,
) -> BitgetExecutionRuntime | None:
    """Build the POST-capable graph only after every explicit cutover gate passes."""
    config = service_config("dispatcher-bitget", environ)
    if not config.execution_enabled:
        return None
    from fatty_trader.exchanges.bitget.async_execution import AsyncBitgetExecution
    from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue
    from fatty_trader.exchanges.bitget.client import BitgetRestClient
    from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
    from fatty_trader.storage.live_intents import PostgresLiveIntentStore

    if client_factory is None:
        client_factory = BitgetRestClient
    if intent_store_factory is None:
        import psycopg

        def default_intent_store_factory() -> object:
            return PostgresLiveIntentStore(psycopg.connect)

        intent_store_factory = default_intent_store_factory
    client = client_factory(
        environ["BITGET_API_KEY"],
        environ["BITGET_API_SECRET"],
        environ["BITGET_API_PASSPHRASE"],
        config.venue_mode,
    )
    venue = AsyncBitgetVenue(client)  # type: ignore[arg-type]
    execution = BitgetDispatchExecution(
        AsyncBitgetExecution(client, venue),  # type: ignore[arg-type]
        intent_store_factory(),  # type: ignore[arg-type]
    )
    return BitgetExecutionRuntime(
        execution=execution,
        preflight=_bitget_dispatch_preflight(venue, environ),
        client=client,
    )


def _bitget_dispatch_preflight(venue: Any, environ: Mapping[str, str]) -> Callable[[str], Any]:
    allocation_pct = Decimal(environ.get("BITGET_ALLOCATION_PCT", "0.20"))
    max_leverage = int(environ.get("BITGET_MAX_LEVERAGE", "50"))
    default_leverage = int(environ.get("BITGET_MIN_LEVERAGE", "20"))

    async def preflight(symbol: str) -> tuple[InstrumentSpec, VenueRiskConfig]:
        if not re.fullmatch(r"^[A-Z0-9]{2,20}$", symbol):
            raise ValueError("dispatch symbol failed Bitget symbol validation")
        snapshot = await venue.preflight(symbol)
        metadata = snapshot.metadata
        allocation = snapshot.available_balance * allocation_pct
        return (
            InstrumentSpec(
                exchange=Exchange.BITGET,
                symbol=metadata.symbol,
                qty_step=metadata.size_step,
                min_qty=metadata.min_order_qty,
                min_notional=metadata.min_notional,
                max_leverage=min(metadata.max_leverage, max_leverage),
                contract_multiplier=metadata.contract_value,
            ),
            VenueRiskConfig(
                exchange=Exchange.BITGET,
                base_margin_usdt=allocation,
                default_leverage=default_leverage,
                max_leverage=min(metadata.max_leverage, max_leverage),
                max_auto_margin_usdt=allocation,
                free_margin_usdt=snapshot.available_balance,
                free_margin_headroom_pct=allocation_pct,
                max_position_notional_usdt=allocation * Decimal(max_leverage),
                margin_mode=MarginMode.ISOLATED,
            ),
        )

    return preflight


async def run_bitget_dispatcher(environ: Mapping[str, str]) -> None:
    """Run the durable dispatcher; its REST execution graph remains closed by default."""
    from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
    from fatty_trader.execution.bitget_dispatcher import BitgetDispatcher, DispatchGate
    from fatty_trader.storage.reconciliation import PostgresReconciliationRepository

    config = service_config("dispatcher-bitget", environ)
    import psycopg

    runtime = build_bitget_execution_runtime(environ)
    dispatcher = BitgetDispatcher(
        PostgresBitgetDispatchRepository(psycopg.connect),
        gate=DispatchGate(
            execution_enabled=config.execution_enabled,
            canary_max_orders=int(environ.get("BITGET_CANARY_MAX_ORDERS", "0")),
            canary_symbol=environ.get("BITGET_CANARY_SYMBOL", "").strip() or None,
        ),
        execution=runtime.execution if runtime is not None else None,  # type: ignore[arg-type]
        preflight=(
            runtime.preflight
            if runtime is not None
            else lambda _: (_ for _ in ()).throw(RuntimeError("cutover gate is closed"))
        ),
        kill_switch=(
            PostgresReconciliationRepository(psycopg.connect)
            if bitget_kill_switch_enforced(environ)
            else None
        ),
    )
    interval = float(environ.get("BITGET_DISPATCH_POLL_SECONDS", "30"))
    lease_seconds = int(environ.get("BITGET_DISPATCH_LEASE_SECONDS", "30"))
    try:
        while True:
            cycle_state = await dispatcher.run_once("dispatcher-bitget", lease_seconds)
            print(
                f"service=dispatcher-bitget mode={config.mode} venue_mode={config.venue_mode} "
                f"state={cycle_state}",
                flush=True,
            )
            await asyncio.sleep(interval)
    finally:
        if runtime is not None:
            await runtime.client.aclose()  # type: ignore[attr-defined]


async def run_bitget_monitor(environ: Mapping[str, str]) -> None:
    """Run only signed provider GETs and persist fail-closed reconciliation state."""
    import psycopg

    from fatty_trader.exchanges.bitget.client import BitgetRestClient
    from fatty_trader.execution.bitget_monitor import BitgetMonitor
    from fatty_trader.storage.reconciliation import PostgresReconciliationRepository

    config = service_config("monitor-bitget", environ)
    client = BitgetRestClient(
        environ["BITGET_API_KEY"],
        environ["BITGET_API_SECRET"],
        environ["BITGET_API_PASSPHRASE"],
        mode=config.venue_mode,
    )
    repository = PostgresReconciliationRepository(psycopg.connect)
    max_clock_skew_ms = int(environ.get("BITGET_MAX_CLOCK_SKEW_MS", "10000"))
    if max_clock_skew_ms < 0:
        raise ValueError("BITGET_MAX_CLOCK_SKEW_MS must not be negative")
    monitor = BitgetMonitor(
        client,
        repository,
        max_clock_skew_ms=max_clock_skew_ms,
        enforce_kill_switch=bitget_kill_switch_enforced(environ),
    )
    interval = float(environ.get("BITGET_MONITOR_POLL_SECONDS", "30"))
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
    if name == "operator-bot":
        await run_operator_bot(os.environ)
        return
    if name == "source-management":
        await run_source_management(os.environ)
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


def bitget_kill_switch_enforced(environ: Mapping[str, str]) -> bool:
    """Kill switch latches are replaced by durable operator alerts.

    The monitor still detects anomalies but enqueues notifications rather than
    blocking execution, so signals are never silently dropped.
    """
    return False


def enabled_dispatch_exchanges(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Return the configured engines that can actually consume analyzer dispatches."""
    raw = environ.get("DISPATCH_EXCHANGES", "binance,bitget")
    exchanges = tuple(
        dict.fromkeys(part.strip().lower() for part in raw.split(",") if part.strip())
    )
    if not exchanges or any(exchange not in {"binance", "bitget"} for exchange in exchanges):
        raise ValueError("DISPATCH_EXCHANGES must contain supported engines")
    return exchanges


async def run_analyzer(environ: Mapping[str, str]) -> None:
    """Continuously analyze durable RECEIVED rows and enqueue DEMO intents."""
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
            exchanges=enabled_dispatch_exchanges(environ),
        )
        mode = environ.get("TRADER_MODE", "DEMO").upper()
        print(
            f"service=analyzer mode={mode} state=ready processed={processed} "
            f"codex_cli={codex_cli} codex_account={account_label}",
            flush=True,
        )
        await asyncio.sleep(poll_seconds if processed == 0 else 0)


async def run_operator_bot(environ: Mapping[str, str]) -> None:
    """Run the private authenticated operator command listener in a dedicated thread."""
    import psycopg

    from fatty_trader.exchanges.bitget.client import BitgetRestClient
    from fatty_trader.operator.bitget_gateway import BitgetOperatorGateway
    from fatty_trader.operator.live_commands import OperatorCommandService
    from fatty_trader.operator.telegram_polling import TelegramBotApi, TelegramCommandPoller
    from fatty_trader.operator.update_receipts import PostgresTelegramUpdateReceiptStore
    from fatty_trader.storage.live_intents import PostgresLiveIntentStore

    required = (
        "TG_BOT_TOKEN",
        "TG_OPERATOR_ID",
        "BITGET_API_KEY",
        "BITGET_API_SECRET",
        "BITGET_API_PASSPHRASE",
    )
    missing = [name for name in required if not environ.get(name)]
    if missing:
        raise ValueError("operator-bot requires Telegram and Bitget credentials")
    mode = environ.get("BITGET_MODE", "DEMO").upper()
    client = BitgetRestClient(
        environ["BITGET_API_KEY"],
        environ["BITGET_API_SECRET"],
        environ["BITGET_API_PASSPHRASE"],
        mode=mode,
    )
    gateway = BitgetOperatorGateway(client, PostgresLiveIntentStore(psycopg.connect))
    mutations_raw = environ.get("BITGET_OPERATOR_MUTATIONS_ENABLED", "0").lower()
    if mutations_raw not in {"0", "1"}:
        raise ValueError("BITGET_OPERATOR_MUTATIONS_ENABLED must be 0 or 1")
    commands = OperatorCommandService(
        gateway,
        operator_id=int(environ["TG_OPERATOR_ID"]),
        mutations_enabled=mutations_raw == "1",
    )
    api = TelegramBotApi(environ["TG_BOT_TOKEN"])
    poller = TelegramCommandPoller(
        command_service=commands,
        fetch_updates=api.fetch_updates,
        send_reply=api.send_reply,
        receipt_store=PostgresTelegramUpdateReceiptStore(lambda: psycopg.connect()),
    )

    def listen() -> None:
        try:
            while True:
                try:
                    poller.run_once()
                except RuntimeError:
                    print("service=operator-bot state=poll-retry", flush=True)
                    time.sleep(3)
        finally:
            api.close()
            gateway.close()

    await asyncio.to_thread(listen)


async def run_source_management(environ: Mapping[str, str]) -> None:
    """Execute durable source-management updates (TP1 booked, SL to entry, close)."""
    import psycopg

    from fatty_trader.exchanges.bitget.client import BitgetRestClient
    from fatty_trader.execution.source_management import SourceManagementExecutor
    from fatty_trader.operator.bitget_gateway import BitgetOperatorGateway
    from fatty_trader.storage.live_intents import PostgresLiveIntentStore
    from fatty_trader.storage.source_management import PostgresSourceManagementStore

    mode = environ.get("BITGET_MODE", "DEMO").upper()
    client = BitgetRestClient(
        environ["BITGET_API_KEY"],
        environ["BITGET_API_SECRET"],
        environ["BITGET_API_PASSPHRASE"],
        mode=mode,
    )
    gateway = BitgetOperatorGateway(client, PostgresLiveIntentStore(psycopg.connect))
    executor = SourceManagementExecutor(
        PostgresSourceManagementStore(psycopg.connect), gateway
    )
    interval = float(environ.get("SOURCE_MANAGEMENT_POLL_SECONDS", "30"))
    while True:
        state = await asyncio.to_thread(executor.run_once, "source-management")
        print(f"service=source-management mode={mode} state={state}", flush=True)
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
        print("service=intake mode=DEMO state=disabled reason=missing-telegram-config", flush=True)
        while True:
            await asyncio.sleep(float(environ.get("WORKER_HEARTBEAT_SECONDS", "30")))
    client = client_factory(settings)
    if repository is None:
        import psycopg

        repository = PostgresRawMessageRepository(psycopg.connect)
    forwarder = TelegramForwarder(client, settings, repository)
    await forwarder.attach()
    await client.start()
    print("service=intake mode=DEMO state=ready", flush=True)
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
