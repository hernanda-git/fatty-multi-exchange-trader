"""Small, isolated process entry points used by Docker Compose.

The worker implementations are intentionally conservative until their domain queues
are implemented: they expose a stable command boundary and stay closed by default.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import re
import shutil
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from fatty_trader.analyzer.codex_runner import CodexRunner
from fatty_trader.analyzer.postgres_worker import process_received_batch
from fatty_trader.config.telegram import TelegramSettings
from fatty_trader.domain.enums import Direction, Exchange, MarginMode
from fatty_trader.domain.models import BitgetLiveRiskConfig, InstrumentSpec, VenueRiskConfig
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

# Bitget LIVE policy, pinned in application code. These are invariants, not
# tunables: a live trade commits at most 1 USDT margin at exactly 20x.
_LIVE_MAX_MARGIN_PER_TRADE_USDT = Decimal("1")
_LIVE_LEVERAGE = 20


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
    preflight: Callable[[Any], Any]
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
        allowed_environment += (
            "BITGET_MODE",
            "BITGET_EXECUTION_ENABLED",
            "BITGET_PROTECTION_CAPABILITY_GATE_ENABLED",
            "BITGET_PROTECTION_STALE_SECONDS",
        )
    if name == "monitor-bitget":
        allowed_environment += (
            "BITGET_MODE",
            "BITGET_FALLBACK_MUTATIONS_ENABLED",
            "BITGET_PROTECTION_STREAM_ENABLED",
            "BITGET_PROTECTION_STREAM_MODE",
            "BITGET_PROTECTION_STREAM_STALE_SECONDS",
            "BITGET_PROTECTION_STREAM_HEARTBEAT_SECONDS",
            "BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED",
            "BITGET_PROTECTION_STREAM_SYMBOLS",
            "BITGET_PROTECTION_REST_WATCHDOG_SECONDS",
        )
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


def build_bitget_protection_admission(
    environ: Mapping[str, str],
    *,
    repository: object | None = None,
    now: Callable[[], datetime] | None = None,
) -> Callable[[str], tuple[bool, str]] | None:
    """Build the optional symbol-local protection admission callback.

    The default is deliberately disabled so the existing dispatcher behavior is
    unchanged until capability records and a healthy protection lane are deployed.
    When enabled, an absent or stale capability record blocks only that symbol.
    """
    raw_enabled = environ.get("BITGET_PROTECTION_CAPABILITY_GATE_ENABLED", "0").lower()
    if raw_enabled not in {"0", "1"}:
        raise ValueError("BITGET_PROTECTION_CAPABILITY_GATE_ENABLED must be 0 or 1")
    if raw_enabled == "0":
        return None
    try:
        stale_after = float(environ.get("BITGET_PROTECTION_STALE_SECONDS", "5"))
    except ValueError as exc:
        raise ValueError("BITGET_PROTECTION_STALE_SECONDS must be positive") from exc
    if not math.isfinite(stale_after) or stale_after <= 0:
        raise ValueError("BITGET_PROTECTION_STALE_SECONDS must be positive")
    environment = environ.get("BITGET_MODE", "DEMO").strip().upper()
    if environment not in {"DEMO", "LIVE"}:
        raise ValueError("BITGET_MODE must be DEMO or LIVE")
    if repository is None:
        import psycopg

        from fatty_trader.storage.protection_capabilities import (
            PostgresProtectionCapabilityRepository,
        )

        repository = PostgresProtectionCapabilityRepository(psycopg.connect)
    clock = now or (lambda: datetime.now(UTC))

    from fatty_trader.exchanges.bitget.protection_capability import can_admit_symbol

    def admit(symbol: str) -> tuple[bool, str]:
        capability = repository.get("bitget", environment, symbol)  # type: ignore[attr-defined]
        if capability is None:
            return False, "protection-capability-unknown"
        return can_admit_symbol(
            capability,
            now=clock(),
            stale_after=stale_after,
            required_environment=environment,
        )

    return admit


def build_bitget_protection_stream(
    environ: Mapping[str, str],
    *,
    repository: object | None = None,
    transport: object | None = None,
    clock: Callable[[], float] | None = None,
    wall_clock: Callable[[], float] | None = None,
    active_symbol_source: Callable[[], Iterable[str]] | None = None,
) -> Any | None:
    """Build the disabled-by-default, observe-only Bitget Classic stream."""
    raw_enabled = environ.get("BITGET_PROTECTION_STREAM_ENABLED", "0").lower()
    if raw_enabled not in {"0", "1"}:
        raise ValueError("BITGET_PROTECTION_STREAM_ENABLED must be 0 or 1")
    if raw_enabled == "0":
        return None
    raw_mutations = environ.get("BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED", "0").lower()
    if raw_mutations not in {"0", "1"}:
        raise ValueError("BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED must be 0 or 1")
    mode = environ.get("BITGET_PROTECTION_STREAM_MODE", "observe").strip().lower()
    if mode != "observe" or raw_mutations == "1":
        raise ValueError("Bitget protection stream is observe-only until close path is verified")
    symbols = tuple(
        symbol.strip().upper()
        for symbol in environ.get("BITGET_PROTECTION_STREAM_SYMBOLS", "").split(",")
        if symbol.strip()
    )

    environment = environ.get("BITGET_MODE", "DEMO").strip().upper()
    if environment not in {"DEMO", "LIVE"}:
        raise ValueError("BITGET_MODE must be DEMO or LIVE")
    try:
        stale_after = float(environ.get("BITGET_PROTECTION_STREAM_STALE_SECONDS", "5"))
        heartbeat_interval = float(environ.get("BITGET_PROTECTION_STREAM_HEARTBEAT_SECONDS", "25"))
    except ValueError as exc:
        raise ValueError("Bitget protection stream timing values must be positive") from exc
    if not math.isfinite(stale_after) or stale_after <= 0:
        raise ValueError("Bitget protection stream stale timeout must be positive")
    if not math.isfinite(heartbeat_interval) or heartbeat_interval <= 0:
        raise ValueError("Bitget protection stream heartbeat interval must be positive")
    if repository is None:
        import psycopg

        from fatty_trader.storage.protection_capabilities import (
            PostgresProtectionCapabilityRepository,
        )

        repository = PostgresProtectionCapabilityRepository(psycopg.connect)
    from fatty_trader.exchanges.bitget.websocket import BitgetClassicWebSocket
    from fatty_trader.execution.bitget_protection_stream import BitgetProtectionStreamRuntime

    socket = BitgetClassicWebSocket(
        api_key=environ.get("BITGET_API_KEY", ""),
        api_secret=environ.get("BITGET_API_SECRET", ""),
        passphrase=environ.get("BITGET_API_PASSPHRASE", ""),
        symbols=symbols,
        transport=transport,  # type: ignore[arg-type]
        clock=clock,
        wall_clock=wall_clock,
        stale_after=stale_after,
        heartbeat_interval=heartbeat_interval,
    )
    return BitgetProtectionStreamRuntime(
        socket,
        repository,
        environment=environment,
        active_symbol_source=active_symbol_source,
    )


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
    capability_repository_factory: Callable[[], object] | None = None,
) -> BitgetExecutionRuntime | None:
    """Build the POST-capable graph only after every explicit cutover gate passes."""
    config = service_config("dispatcher-bitget", environ)
    if not config.execution_enabled:
        return None
    import psycopg

    from fatty_trader.exchanges.bitget.async_execution import (
        AsyncBitgetExecution,
        AsyncBitgetExecutionClient,
    )
    from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetClient, AsyncBitgetVenue
    from fatty_trader.exchanges.bitget.client import BitgetRestClient
    from fatty_trader.exchanges.bitget.live import LiveIntentStoreProtocol
    from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
    from fatty_trader.storage.balance_reservations import PostgresBitgetMarginReservationRepository
    from fatty_trader.storage.live_intents import PostgresLiveIntentStore

    using_default_client = client_factory is None
    if client_factory is None:
        client_factory = BitgetRestClient
    if intent_store_factory is None:
        import psycopg

        def default_intent_store_factory() -> object:
            return PostgresLiveIntentStore(psycopg.connect)

        intent_store_factory = default_intent_store_factory
    if capability_repository_factory is None and using_default_client:
        import psycopg

        from fatty_trader.storage.protection_capabilities import (
            PostgresProtectionCapabilityRepository,
        )

        def default_capability_repository_factory() -> object:
            return PostgresProtectionCapabilityRepository(psycopg.connect)

        capability_repository_factory = default_capability_repository_factory
    client = client_factory(
        environ["BITGET_API_KEY"],
        environ["BITGET_API_SECRET"],
        environ["BITGET_API_PASSPHRASE"],
        config.venue_mode,
    )
    venue = AsyncBitgetVenue(cast(AsyncBitgetClient, client))
    capability_repository = (
        capability_repository_factory() if capability_repository_factory is not None else None
    )
    reservation_repository = PostgresBitgetMarginReservationRepository(psycopg.connect)
    execution = BitgetDispatchExecution(
        AsyncBitgetExecution(
            cast(AsyncBitgetExecutionClient, client),
            venue,
            capability_repository=capability_repository,
            reconciliation_repository=reservation_repository,
            environment=config.venue_mode,
        ),
        cast(LiveIntentStoreProtocol, intent_store_factory()),
        reservation_repository=reservation_repository,
    )
    return BitgetExecutionRuntime(
        execution=execution,
        preflight=_bitget_dispatch_preflight(
            venue, environ, reservation_repository=reservation_repository
        ),
        client=client,
    )


def _bitget_dispatch_preflight(
    venue: Any, environ: Mapping[str, str], *, reservation_repository: Any | None = None
) -> Callable[[Any], Any]:
    """Return a fail-closed admission factory; production always reserves first."""
    allocation_pct = Decimal(environ.get("BITGET_ALLOCATION_PCT", "0.20"))
    # Both bounds default to 20 so an unset environment is 20x, never 50x.
    max_leverage = int(environ.get("BITGET_MAX_LEVERAGE", "20"))
    min_leverage = int(environ.get("BITGET_MIN_LEVERAGE", "20"))
    max_age_seconds = Decimal(environ.get("BITGET_BALANCE_MAX_AGE_SECONDS", "5"))
    ttl_seconds = Decimal(environ.get("BITGET_BALANCE_RESERVATION_TTL_SECONDS", "30"))
    if not (Decimal("0") < allocation_pct <= Decimal("1")):
        raise ValueError("BITGET_ALLOCATION_PCT must be in (0, 1]")
    # Live is pinned to exactly 20x. A range is a foot-gun: 20/50 in the
    # environment would silently allow 50x, which is not the intended policy.
    if min_leverage != _LIVE_LEVERAGE or max_leverage != _LIVE_LEVERAGE:
        raise ValueError(
            "Bitget LIVE leverage is fixed at 20x: BITGET_MIN_LEVERAGE and "
            f"BITGET_MAX_LEVERAGE must both be 20 (got {min_leverage}/{max_leverage})"
        )
    if max_age_seconds <= 0 or ttl_seconds <= 0:
        raise ValueError("Bitget balance age and reservation TTL must be positive")
    raw_margin_cap = environ.get("BITGET_MAX_MARGIN_PER_TRADE_USDT", "").strip()
    if not raw_margin_cap:
        raise ValueError(
            "BITGET_MAX_MARGIN_PER_TRADE_USDT is required for Bitget LIVE; "
            "refusing to size without a hard per-trade margin cap"
        )
    try:
        max_margin_per_trade = Decimal(raw_margin_cap)
    except Exception as exc:  # noqa: BLE001 - surfaced as a startup config error
        raise ValueError(
            f"BITGET_MAX_MARGIN_PER_TRADE_USDT must be a positive USDT amount: {raw_margin_cap!r}"
        ) from exc
    if not max_margin_per_trade.is_finite() or max_margin_per_trade <= 0:
        raise ValueError("BITGET_MAX_MARGIN_PER_TRADE_USDT must be a finite positive amount")
    # The policy is a hard ceiling of exactly 1 USDT, not a tunable. A larger
    # value (including a huge exponent like 1e999999) would silently widen the
    # cap, so anything other than exactly 1 is a configuration error.
    if max_margin_per_trade != _LIVE_MAX_MARGIN_PER_TRADE_USDT:
        raise ValueError(
            "Bitget LIVE margin cap is fixed at 1 USDT: "
            f"BITGET_MAX_MARGIN_PER_TRADE_USDT must be exactly 1 (got {raw_margin_cap!r})"
        )

    async def preflight(dispatch: Any) -> Any:
        symbol = dispatch.pair_token if hasattr(dispatch, "pair_token") else dispatch
        if not isinstance(symbol, str) or not re.fullmatch(r"^[A-Z0-9]{2,20}$", symbol):
            raise ValueError("dispatch symbol failed Bitget symbol validation")
        snapshot = await venue.preflight(symbol)
        metadata = snapshot.metadata
        account = snapshot.account
        available_balance = account.available
        if available_balance <= 0:
            raise ValueError(
                "Bitget available USDT margin is zero; fund the configured "
                "LIVE account before enabling execution"
            )
        if reservation_repository is None:
            # Test/legacy seam only; runtime wires the durable admission branch below.
            # max_margin_per_trade is validated above and is never None here.
            allocation = min(available_balance * allocation_pct, max_margin_per_trade)
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
                    default_leverage=min_leverage,
                    max_leverage=min(metadata.max_leverage, max_leverage),
                    max_auto_margin_usdt=allocation,
                    free_margin_usdt=available_balance,
                    free_margin_headroom_pct=allocation_pct,
                    max_position_notional_usdt=allocation * Decimal(max_leverage),
                    margin_mode=MarginMode.ISOLATED,
                ),
            )
        observed_at = account.observed_at
        if datetime.now(UTC) - observed_at > __import__("datetime").timedelta(
            seconds=float(max_age_seconds)
        ):
            raise ValueError("Bitget balance snapshot is stale")
        from fatty_trader.execution.bitget_admission import BitgetEntrySubmission
        from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
        from fatty_trader.execution.bitget_dispatcher import BitgetAdmission
        from fatty_trader.risk.live_policy import LiveSizingInput, plan_live_position

        risk = BitgetLiveRiskConfig(
            min_leverage=min_leverage,
            max_leverage=max_leverage,
            allocation_pct=allocation_pct,
            max_margin_per_trade_usdt=max_margin_per_trade,
        )
        active_position_count = getattr(venue, "active_position_count", None)
        if not callable(active_position_count):
            raise ValueError("Bitget venue cannot read all active positions for admission")
        active_positions = await active_position_count()
        if (
            isinstance(active_positions, bool)
            or not isinstance(active_positions, int)
            or active_positions < 0
        ):
            raise ValueError("Bitget active position count is invalid")
        decision = plan_live_position(
            LiveSizingInput(
                meta=metadata,
                risk=risk,
                available_usdt=available_balance,
                entry=snapshot.current_price,
                direction=Direction(dispatch.direction),
                active_positions=active_positions,
                stop_loss=dispatch.stop_loss,
            )
        )
        if (
            not decision.accepted
            or decision.quantity is None
            or decision.leverage is None
            or decision.margin_usdt is None
            or decision.notional_usdt is None
        ):
            raise ValueError(f"Bitget live sizing rejected: {decision.reason}")
        client_order_id = BitgetDispatchExecution.client_oid(dispatch)
        admission = reservation_repository.reserve(
            exchange="bitget",
            dispatch_id=dispatch.id,
            client_order_id=client_order_id,
            total_balance=account.total_balance,
            available_balance=available_balance,
            equity=account.equity,
            margin_coin=account.margin_coin,
            observed_at=observed_at,
            planned_margin_usdt=decision.margin_usdt,
            headroom=Decimal("1"),
            ttl=__import__("datetime").timedelta(seconds=float(ttl_seconds)),
            max_margin_per_trade_usdt=max_margin_per_trade,
        )
        if (
            not admission.accepted
            or admission.snapshot_id is None
            or admission.reservation_id is None
        ):
            raise ValueError(f"Bitget margin admission rejected: {admission.reason or 'unknown'}")
        return BitgetAdmission(
            BitgetEntrySubmission(
                quantity=decision.quantity,
                effective_leverage=decision.leverage,
                planned_margin_usdt=decision.margin_usdt,
                planned_notional_usdt=decision.notional_usdt,
                margin_mode="ISOLATED",
                balance_snapshot_id=admission.snapshot_id,
                margin_reservation_id=admission.reservation_id,
                observed_at=observed_at,
            )
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
    protection_admission = build_bitget_protection_admission(environ)
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
            PostgresReconciliationRepository(cast(Any, psycopg.connect))
            if bitget_kill_switch_enforced(environ)
            else None
        ),
        protection_admission=protection_admission,
    )
    if runtime is not None:
        await runtime.execution.reconcile_active_reservations()  # type: ignore[attr-defined]
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


async def run_bitget_monitor_loop(
    monitor: Any,
    *,
    interval: float,
    stream: Any | None = None,
    watchdog: Any | None = None,
    watchdog_interval: float | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run monitor, optional stream, and optional watchdog with shared shutdown."""
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("Bitget monitor interval must be positive")
    effective_watchdog_interval: float | None = None
    if watchdog is not None:
        effective_watchdog_interval = interval if watchdog_interval is None else watchdog_interval
        if not math.isfinite(effective_watchdog_interval) or effective_watchdog_interval <= 0:
            raise ValueError("Bitget watchdog interval must be positive")
    stop = stop_event or asyncio.Event()
    background: list[asyncio.Task[Any]] = []
    if stream is not None:
        background.append(asyncio.create_task(stream.run(stop)))
    if watchdog is not None:
        assert effective_watchdog_interval is not None
        background.append(
            asyncio.create_task(
                _run_bitget_watchdog_loop(watchdog, effective_watchdog_interval, stop)
            )
        )
    try:
        while not stop.is_set():
            report = await monitor.run_once()
            print(
                f"service=monitor-bitget state={getattr(report, 'status', 'unknown')} "
                f"reasons={','.join(getattr(report, 'reasons', ())) or 'none'}",
                flush=True,
            )
            _raise_if_background_failed(background, stop)
            await _wait_for_stop(stop, interval)
    finally:
        stop.set()
        for task in background:
            if not task.done():
                task.cancel()
        if background:
            await asyncio.gather(*background, return_exceptions=True)


async def _run_bitget_watchdog_loop(
    watchdog: Any, interval: float, stop_event: asyncio.Event
) -> None:
    """Run the REST protection watchdog independently of the legacy monitor cadence."""
    while not stop_event.is_set():
        refresh_symbols = getattr(watchdog, "refresh_symbols", None)
        stream_symbols = getattr(watchdog, "stream_symbols", ())
        if callable(refresh_symbols):
            refresh_symbols(stream_symbols)
        report = await watchdog.run_once()
        print(
            f"service=monitor-bitget component=protection-watchdog "
            f"state={getattr(report, 'status', 'unknown')} "
            f"reasons={','.join(getattr(report, 'reasons', ())) or 'none'}",
            flush=True,
        )
        await _wait_for_stop(stop_event, interval)


async def _wait_for_stop(stop_event: asyncio.Event, interval: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=interval)
    except TimeoutError:
        return


def _raise_if_background_failed(
    background: list[asyncio.Task[Any]], stop_event: asyncio.Event
) -> None:
    if stop_event.is_set():
        return
    for task in background:
        if not task.done():
            continue
        if task.cancelled():
            raise RuntimeError("Bitget protection background task was cancelled")
        exception = task.exception()
        if exception is not None:
            raise exception
        raise RuntimeError("Bitget protection background task stopped unexpectedly")


def build_bitget_monitor_protection(
    environ: Mapping[str, str],
    client: Any,
    *,
    repository: object | None = None,
    transport: object | None = None,
    clock: Callable[[], float] | None = None,
    wall_clock: Callable[[], float] | None = None,
    now: Callable[[], datetime] | None = None,
    active_symbol_source: Callable[[], Iterable[str]] | None = None,
) -> tuple[Any | None, Any | None, float | None]:
    """Build the optional observe-only stream and its paired REST watchdog."""
    stream = build_bitget_protection_stream(
        environ,
        repository=repository,
        transport=transport,
        clock=clock,
        wall_clock=wall_clock,
        active_symbol_source=active_symbol_source,
    )
    if stream is None:
        return None, None, None
    watchdog_interval = _positive_seconds(
        environ,
        "BITGET_PROTECTION_REST_WATCHDOG_SECONDS",
        5.0,
        maximum=60.0,
    )
    from fatty_trader.execution.bitget_protection_watchdog import BitgetProtectionWatchdog

    environment = environ.get("BITGET_MODE", "DEMO").strip().upper()
    active_symbols = tuple(stream.symbols)
    watchdog = BitgetProtectionWatchdog(
        stream.socket,
        stream.repository,
        environment=environment,
        symbols=active_symbols,
        read_position=client.get_single_position,
        now=now or (lambda: datetime.now(UTC)),
    )
    return stream, watchdog, watchdog_interval


def _positive_seconds(
    environ: Mapping[str, str], name: str, default: float, *, maximum: float
) -> float:
    try:
        value = float(environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be positive") from exc
    if not math.isfinite(value) or value <= 0 or value > maximum:
        raise ValueError(f"{name} must be positive and no greater than {maximum:g}")
    return value


async def run_bitget_monitor(environ: Mapping[str, str]) -> None:
    """Run only signed provider GETs and persist fail-closed reconciliation state."""
    import psycopg

    from fatty_trader.exchanges.bitget.client import BitgetRestClient
    from fatty_trader.execution.bitget_monitor import BitgetMonitor
    from fatty_trader.storage.live_intents import PostgresLiveIntentStore
    from fatty_trader.storage.reconciliation import PostgresReconciliationRepository

    config = service_config("monitor-bitget", environ)
    client = BitgetRestClient(
        environ["BITGET_API_KEY"],
        environ["BITGET_API_SECRET"],
        environ["BITGET_API_PASSPHRASE"],
        mode=config.venue_mode,
    )
    repository = PostgresReconciliationRepository(psycopg.connect)
    live_intent_store = PostgresLiveIntentStore(psycopg.connect)
    max_clock_skew_ms = int(environ.get("BITGET_MAX_CLOCK_SKEW_MS", "10000"))
    if max_clock_skew_ms < 0:
        raise ValueError("BITGET_MAX_CLOCK_SKEW_MS must not be negative")
    fallback_raw = environ.get("BITGET_FALLBACK_MUTATIONS_ENABLED", "0").lower()
    if fallback_raw not in {"0", "1"}:
        raise ValueError("BITGET_FALLBACK_MUTATIONS_ENABLED must be 0 or 1")
    monitor = BitgetMonitor(
        client,
        repository,
        max_clock_skew_ms=max_clock_skew_ms,
        enforce_kill_switch=bitget_kill_switch_enforced(environ),
        fallback_mutations_enabled=((fallback_raw == "1") and bitget_kill_switch_enforced(environ)),
        live_intent_store=live_intent_store,
    )
    interval = float(environ.get("BITGET_MONITOR_POLL_SECONDS", "30"))
    from fatty_trader.execution.bitget_fallback_protection import load_active

    stream, watchdog, watchdog_interval = build_bitget_monitor_protection(
        environ,
        client,
        active_symbol_source=lambda: [entry["symbol"] for entry in load_active()],
    )
    try:
        await run_bitget_monitor_loop(
            monitor,
            interval=interval,
            stream=stream,
            watchdog=watchdog,
            watchdog_interval=watchdog_interval,
        )
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
    """Enforce Bitget anomaly latches only on the LIVE execution lane."""
    return (
        environ.get("TRADER_MODE", "").upper() == "LIVE"
        and environ.get("BITGET_MODE", "").upper() == "LIVE"
    )


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
            image_analysis_enabled=environ.get("BITGET_IMAGE_ANALYSIS_ENABLED", "0") == "1",
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
    from fatty_trader.operator.health import (
        build_operator_diagnostic,
        build_operator_health_report,
    )
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
        health_reader=lambda: build_operator_health_report(
            gateway,
            psycopg.connect,
            mode=environ.get("TRADER_MODE", "DEMO"),
            venue_mode=mode,
            execution_enabled=environ.get("BITGET_EXECUTION_ENABLED", "0") == "1",
            fallback_mutations_enabled=environ.get("BITGET_FALLBACK_MUTATIONS_ENABLED", "0"),
            stream_enabled=environ.get("BITGET_PROTECTION_STREAM_ENABLED", "0"),
            stream_mode=environ.get("BITGET_PROTECTION_STREAM_MODE", "observe"),
            stream_mutations_enabled=environ.get("BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED", "0"),
            capability_gate_enabled=environ.get("BITGET_PROTECTION_CAPABILITY_GATE_ENABLED", "0"),
        ),
        diagnostic_reader=lambda kind: build_operator_diagnostic(kind, gateway, psycopg.connect),
    )
    api = TelegramBotApi(environ["TG_BOT_TOKEN"])
    api.set_my_commands()
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
    mutations_raw = environ.get("BITGET_OPERATOR_MUTATIONS_ENABLED", "0").lower()
    if mutations_raw not in {"0", "1"}:
        raise ValueError("BITGET_OPERATOR_MUTATIONS_ENABLED must be 0 or 1")
    executor = SourceManagementExecutor(
        PostgresSourceManagementStore(psycopg.connect),
        gateway,
        mutations_enabled=mutations_raw == "1",
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
