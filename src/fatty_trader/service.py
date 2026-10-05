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
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from fatty_trader.worker_health import owned_worker_health, worker_progress

SUPPORTED_SERVICES = (
    "intake",
    "analyzer",
    "dispatcher-binance",
    "dispatcher-bitget",
    "monitor-binance",
    "monitor-bitget",
    "operator-bot",
    "source-management",
    "paper-kaka",
)

# Bitget LIVE policy, pinned in application code. These are invariants, not
# tunables: a live trade plans at most 1 USDT of isolated margin at exactly 20x.
# The cap bounds the planned margin; a market fill can realize slightly more
# through slippage (filled_notional / 20), so it is a ceiling on planning, not on
# the exchange's post-fill number.
_LIVE_MAX_MARGIN_PER_TRADE_USDT = Decimal("1")
_LIVE_LEVERAGE = 20
# Lowest leverage the LIVE sizing policy may back off to. It exists so a signal whose
# stop needs more liquidation headroom than 20x allows can still trade at a lower
# leverage instead of being refused; the ceiling above is never exceeded.
_LIVE_LEVERAGE_FLOOR = 5


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
    # Paper lane: deliberately no credentials, so it cannot reach any venue.
    "paper-kaka": (),
}


def service_config(name: str, environ: Mapping[str, str]) -> ServiceConfig:
    """Return a DEMO-first config with an isolated Bitget venue mode."""
    if name not in SUPPORTED_SERVICES:
        raise ValueError(f"unsupported service: {name}")
    # Compose pins this marker; development/library fixtures remain isolated.
    if "FATTY_PRODUCTION_LIVE_ONLY" in environ:
        from fatty_trader.production_policy import validate_production_live_policy

        validate_production_live_policy(environ)
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
    """Build the disabled-by-default, observe-only stream (V2 for LIVE)."""
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
    from fatty_trader.exchanges.bitget.websocket_v2 import BitgetV2WebSocket
    from fatty_trader.execution.bitget_protection_stream import BitgetProtectionStreamRuntime

    # LIVE uses the supported public/private V2 split, never a Classic fallback.
    socket_type = BitgetV2WebSocket if environment == "LIVE" else BitgetClassicWebSocket
    socket = socket_type(
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
    from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
    from fatty_trader.storage.balance_reservations import PostgresBitgetMarginReservationRepository
    from fatty_trader.storage.live_intents import PostgresLiveIntentStore
    from fatty_trader.storage.reconciliation import PostgresReconciliationRepository

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
    dispatch_repository = PostgresBitgetDispatchRepository(psycopg.connect)
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    recovery_reader = GetOnlyProtectionRecovery(
        client, dispatch_repository, environment=config.venue_mode
    )
    execution = BitgetDispatchExecution(
        AsyncBitgetExecution(
            cast(AsyncBitgetExecutionClient, client),
            venue,
            capability_repository=capability_repository,
            reconciliation_repository=reservation_repository,
            fallback_protection_enabled=_bitget_fallback_mutations_enabled(environ),
            environment=config.venue_mode,
        ),
        cast(LiveIntentStoreProtocol, intent_store_factory()),
        reservation_repository=reservation_repository,
        dispatch_repository=dispatch_repository,
        kill_switch=PostgresReconciliationRepository(psycopg.connect),
        recovery_protection=recovery_reader,
    )
    return BitgetExecutionRuntime(
        execution=execution,
        preflight=_bitget_dispatch_preflight(
            venue, environ, reservation_repository=reservation_repository
        ),
        client=client,
    )


def _bitget_fallback_mutations_enabled(environ: Mapping[str, str]) -> bool:
    """Whether bot-managed TP/SL closes can actually be submitted by the monitor.

    Mirrors the monitor wiring: the flag alone is not enough, the kill switch
    also has to permit mutations, otherwise the fallback row is never executed.
    """
    raw = environ.get("BITGET_FALLBACK_MUTATIONS_ENABLED", "0").strip().lower()
    if raw not in {"0", "1"}:
        raise ValueError("BITGET_FALLBACK_MUTATIONS_ENABLED must be 0 or 1")
    return raw == "1" and bitget_kill_switch_enforced(environ)


def _bitget_dispatch_preflight(
    venue: Any, environ: Mapping[str, str], *, reservation_repository: Any | None = None
) -> Callable[[Any], Any]:
    """Return a fail-closed admission factory; production always reserves first."""
    from datetime import timedelta

    environment = environ.get("BITGET_MODE", "DEMO").strip().upper()
    if environment not in {"DEMO", "LIVE"}:
        raise ValueError("BITGET_MODE must be DEMO or LIVE")
    max_positions = int(environ.get("BITGET_MAX_NORMAL_POSITIONS", "5"))
    if max_positions <= 0:
        raise ValueError("BITGET_MAX_NORMAL_POSITIONS must be a positive integer")
    allocation_pct = Decimal(environ.get("BITGET_ALLOCATION_PCT", "0.20"))
    # Both bounds default to 20 so an unset environment is 20x, never 50x.
    max_leverage = int(environ.get("BITGET_MAX_LEVERAGE", "20"))
    min_leverage = int(environ.get("BITGET_MIN_LEVERAGE", "20"))
    max_age_seconds = Decimal(environ.get("BITGET_BALANCE_MAX_AGE_SECONDS", "5"))
    ttl_seconds = Decimal(environ.get("BITGET_BALANCE_RESERVATION_TTL_SECONDS", "30"))
    future_skew_seconds = Decimal(environ.get("BITGET_BALANCE_MAX_FUTURE_SKEW_SECONDS", "1"))
    if not all(value.is_finite() for value in (max_age_seconds, ttl_seconds, future_skew_seconds)):
        raise ValueError("Bitget snapshot age policy must be finite")
    if future_skew_seconds < 0:
        raise ValueError("Bitget snapshot future skew must be non-negative")
    max_snapshot_age = timedelta(seconds=float(max_age_seconds))
    max_future_skew = timedelta(seconds=float(future_skew_seconds))

    def validate_observed_at(value: Any, label: str) -> None:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"Bitget {label} snapshot timestamp is invalid")
        now = datetime.now(UTC)
        if now - value > max_snapshot_age:
            raise ValueError(f"Bitget {label} snapshot is stale")
        if value - now > max_future_skew:
            raise ValueError(f"Bitget {label} snapshot is future-dated")

    if not (Decimal("0") < allocation_pct <= Decimal("1")):
        raise ValueError("BITGET_ALLOCATION_PCT must be in (0, 1]")
    # The ceiling is pinned at 20x: never above. The floor only bounds how far the
    # sizing policy may back off to give a wide stop enough liquidation headroom.
    if max_leverage != _LIVE_LEVERAGE:
        raise ValueError(
            "Bitget LIVE leverage ceiling is fixed at 20x: "
            f"BITGET_MAX_LEVERAGE must be 20 (got {max_leverage})"
        )
    if not (_LIVE_LEVERAGE_FLOOR <= min_leverage <= max_leverage):
        raise ValueError(
            "Bitget LIVE leverage floor must be an integer in "
            f"[{_LIVE_LEVERAGE_FLOOR}, {max_leverage}] "
            f"(got BITGET_MIN_LEVERAGE={min_leverage})"
        )
    if max_age_seconds <= 0 or ttl_seconds <= 0:
        raise ValueError("Bitget balance age and reservation TTL must be positive")
    # Now actually honored: the guard's span-relative floor used to be silently fixed
    # at the model default, so tuning this env had no effect.
    liquidation_buffer = Decimal(environ.get("BITGET_LIQUIDATION_BUFFER", "0.10"))
    if not (Decimal("0") < liquidation_buffer <= Decimal("1")):
        raise ValueError("BITGET_LIQUIDATION_BUFFER must be in (0, 1]")
    if environ.get("BITGET_MAX_MARGIN_FLOOR_ESCAPE_USDT", "").strip():
        raise ValueError("floor-escape margin is not supported by the pinned LIVE policy")
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
    # The policy is pinned, not a tunable. A different value could silently widen
    # or narrow the approved cap, so reject every non-exact configuration.
    if max_margin_per_trade != _LIVE_MAX_MARGIN_PER_TRADE_USDT:
        raise ValueError(
            "Bitget LIVE margin cap is fixed at 1 USDT: "
            "BITGET_MAX_MARGIN_PER_TRADE_USDT must be exactly 1 "
            f"(got {raw_margin_cap!r})"
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
        validate_observed_at(observed_at, "balance")
        from fatty_trader.execution.bitget_admission import BitgetEntrySubmission
        from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
        from fatty_trader.execution.bitget_dispatcher import BitgetAdmission
        from fatty_trader.risk.live_policy import LiveSizingInput, plan_live_position

        risk = BitgetLiveRiskConfig(
            min_leverage=min_leverage,
            max_leverage=max_leverage,
            allocation_pct=allocation_pct,
            max_margin_per_trade_usdt=max_margin_per_trade,
            liquidation_buffer=liquidation_buffer,
            max_normal_positions=max_positions,
        )
        read_positions = getattr(venue, "active_position_snapshot", None)
        if not callable(read_positions):
            raise ValueError("Bitget venue cannot read all active positions for admission")
        positions = await cast(Any, read_positions)()
        validate_observed_at(positions.observed_at, "positions")
        validate_observed_at(observed_at, "balance")
        # Storage rechecks the oldest evidence after its admission lock wait.
        observed_at = min(observed_at, positions.observed_at)
        provider_active_symbols = positions.symbols
        if not isinstance(provider_active_symbols, tuple) or any(
            not isinstance(item, str) or not re.fullmatch(r"[A-Z0-9]{2,20}", item)
            for item in provider_active_symbols
        ):
            raise ValueError("Bitget active position symbols are invalid")
        active_positions = len(provider_active_symbols)
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
            symbol=metadata.symbol,
            environment=environment,
            max_positions=risk.max_normal_positions,
            provider_active_symbols=provider_active_symbols,
            max_snapshot_age=max_snapshot_age,
            max_future_skew=max_future_skew,
        )
        if (
            not admission.accepted
            or admission.snapshot_id is None
            or admission.reservation_id is None
        ):
            if admission.reason == "stale-source-message":
                from fatty_trader.intake.freshness import SourceFreshnessExpired

                raise SourceFreshnessExpired(admission.reason)
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
        # Wired in every mode, not only LIVE/LIVE: an execution graph that can POST
        # exists whenever BITGET_EXECUTION_ENABLED=1, so a DEMO/paper lane would
        # otherwise ignore the operator's emergency stop. The latch itself stays
        # LIVE-only (bitget_kill_switch_enforced), so this is a read gate.
        kill_switch=PostgresReconciliationRepository(cast(Any, psycopg.connect)),
        protection_admission=protection_admission,
    )
    interval = float(environ.get("BITGET_DISPATCH_POLL_SECONDS", "30"))
    lease_seconds = int(environ.get("BITGET_DISPATCH_LEASE_SECONDS", "30"))
    try:
        if runtime is not None:
            await runtime.execution.recover_entry_lifecycles()  # type: ignore[attr-defined]
            if runtime.execution.recovery_ready is not True:  # type: ignore[attr-defined]
                raise RuntimeError("Bitget lifecycle recovery is not ready")
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


# Monitor liveness guards.
#
# Incident 2026-09-27: the monitor process spun at ~100% CPU with a blocked event
# loop — no log output for 3h50m while the container still reported healthy, because
# the inherited healthcheck only validated configuration. Two guards close that hole:
# a heartbeat file whose freshness the container healthcheck verifies, and a
# supervisor thread that exits the process when the heartbeat goes stale so
# ``restart: unless-stopped`` brings back a working process. The supervisor has to be
# a plain thread: while the event loop is blocked, no coroutine-level timeout can fire.
_DEFAULT_HEARTBEAT_PATH = "/tmp/fatty-monitor-heartbeat"


def write_monitor_heartbeat(path: str, *, now: float | None = None) -> None:
    """Record process liveness for the healthcheck and the stall supervisor."""
    stamp = time.monotonic() if now is None else now
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"{stamp}\n")
    except OSError:
        # Liveness bookkeeping must never take the monitor down.
        return


def monitor_heartbeat_age(path: str, *, now: float | None = None) -> float | None:
    """Seconds since the last heartbeat, or None when it is missing/unreadable."""
    try:
        with open(path, encoding="utf-8") as handle:
            stamp = float(handle.read().strip())
    except (OSError, ValueError):
        return None
    return (time.monotonic() if now is None else now) - stamp


def monitor_heartbeat_is_fresh(path: str, max_age: float, *, now: float | None = None) -> bool:
    """Whether the monitor made progress within ``max_age`` seconds.

    A non-positive age means the stamp predates this boot (``time.monotonic`` resets
    across restarts), which must count as stale rather than infinitely fresh.
    """
    age = monitor_heartbeat_age(path, now=now)
    return age is not None and 0 <= age <= max_age


def start_monitor_stall_supervisor(
    path: str,
    *,
    max_age: float,
    check_interval: float | None = None,
    exit_code: int = 1,
    exit_callable: Callable[[int], None] | None = None,
) -> threading.Thread:
    """Exit the process when the heartbeat goes stale, so Compose restarts it."""
    period = max(1.0, min(max_age / 3, 30.0)) if check_interval is None else check_interval
    exiter = os._exit if exit_callable is None else exit_callable

    def supervise() -> None:
        while True:
            time.sleep(period)
            if not monitor_heartbeat_is_fresh(path, max_age):
                print(
                    "service=monitor-bitget state=stalled "
                    f"heartbeat_age={monitor_heartbeat_age(path)} max_age={max_age} "
                    "action=exit-for-restart",
                    flush=True,
                )
                exiter(exit_code)
                return

    thread = threading.Thread(target=supervise, name="monitor-stall-supervisor", daemon=True)
    thread.start()
    return thread


async def _run_cycle_with_timeout(
    run_once: Callable[[], Any], *, cycle_timeout: float | None, component: str
) -> Any:
    """Await one cycle, or fail loudly instead of hanging forever.

    A blocked event loop cannot be rescued from inside the loop, which is what the
    supervisor thread is for; this guards the slower case of a single await that never
    returns, such as a provider socket that opens and then goes silent.
    """
    if cycle_timeout is None:
        return await run_once()
    try:
        return await asyncio.wait_for(run_once(), timeout=cycle_timeout)
    except TimeoutError as exc:
        raise RuntimeError(
            f"{component} cycle exceeded the {cycle_timeout}s budget; restarting the worker"
        ) from exc


async def run_bitget_monitor_loop(
    monitor: Any,
    *,
    interval: float,
    stream: Any | None = None,
    watchdog: Any | None = None,
    watchdog_interval: float | None = None,
    stop_event: asyncio.Event | None = None,
    heartbeat_path: str | None = None,
    cycle_timeout: float | None = None,
    stall_timeout: float | None = None,
) -> None:
    """Run monitor, optional stream, and optional watchdog with shared shutdown."""
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("Bitget monitor interval must be positive")
    if cycle_timeout is not None and (not math.isfinite(cycle_timeout) or cycle_timeout <= 0):
        raise ValueError("Bitget monitor cycle timeout must be positive")
    if stall_timeout is not None and (not math.isfinite(stall_timeout) or stall_timeout <= 0):
        raise ValueError("Bitget monitor stall timeout must be positive")
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
                _run_bitget_watchdog_loop(
                    watchdog,
                    effective_watchdog_interval,
                    stop,
                    heartbeat_path=heartbeat_path,
                    cycle_timeout=cycle_timeout,
                )
            )
        )
    if heartbeat_path is not None:
        # Seed the heartbeat so a fresh process is not judged stalled before its
        # first cycle has had a chance to run.
        write_monitor_heartbeat(heartbeat_path)
    if heartbeat_path is not None and stall_timeout is not None:
        start_monitor_stall_supervisor(heartbeat_path, max_age=stall_timeout)
    try:
        while not stop.is_set():
            report = await _run_cycle_with_timeout(
                monitor.run_once, cycle_timeout=cycle_timeout, component="Bitget monitor"
            )
            if heartbeat_path is not None:
                write_monitor_heartbeat(heartbeat_path)
            print(
                f"service=monitor-bitget state={getattr(report, 'status', 'unknown')} "
                f"reasons={','.join(getattr(report, 'reasons', ())) or 'none'} "
                f"latched_reason={getattr(report, 'latched_reason', None) or 'none'}",
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
    watchdog: Any,
    interval: float,
    stop_event: asyncio.Event,
    *,
    heartbeat_path: str | None = None,
    cycle_timeout: float | None = None,
) -> None:
    """Run the REST protection watchdog independently of the legacy monitor cadence."""
    while not stop_event.is_set():
        refresh_symbols = getattr(watchdog, "refresh_symbols", None)
        stream_symbols = getattr(watchdog, "stream_symbols", ())
        if callable(refresh_symbols):
            refresh_symbols(stream_symbols)
        report = await _run_cycle_with_timeout(
            watchdog.run_once, cycle_timeout=cycle_timeout, component="Bitget protection watchdog"
        )
        if heartbeat_path is not None:
            write_monitor_heartbeat(heartbeat_path)
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
    kill_switch: object | None = None,
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
    # Use the same fail-closed predicate the monitor and dispatcher already use.
    # Gating on BITGET_MODE alone would latch on the monitor while the read gate
    # is live in every mode, silently blocking paper execution in a mixed config.
    latching_enabled = bitget_kill_switch_enforced(environ)
    stale_cycles_before_latch = 3
    if latching_enabled:
        raw_cycles = environ.get("BITGET_PROTECTION_STALE_CYCLES_BEFORE_LATCH", "").strip()
        if raw_cycles:
            try:
                stale_cycles_before_latch = int(raw_cycles)
            except ValueError as exc:
                raise ValueError(
                    "BITGET_PROTECTION_STALE_CYCLES_BEFORE_LATCH must be an integer"
                ) from exc
            if stale_cycles_before_latch < 1:
                raise ValueError("BITGET_PROTECTION_STALE_CYCLES_BEFORE_LATCH must be at least 1")
    watchdog = BitgetProtectionWatchdog(
        stream.socket,
        stream.repository,
        environment=environment,
        symbols=active_symbols,
        read_position=client.get_single_position,
        now=now or (lambda: datetime.now(UTC)),
        kill_switch=kill_switch if latching_enabled else None,
        stale_cycles_before_latch=stale_cycles_before_latch,
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
    # Liveness: a cycle budget, and a stall budget after which the supervisor exits
    # the process so Compose restarts a working one. The default stall budget is
    # generous enough to survive a slow provider read but far shorter than the 3h50m
    # silent stall this guards against.
    cycle_timeout = float(environ.get("BITGET_MONITOR_CYCLE_TIMEOUT_SECONDS", "120"))
    stall_timeout = float(
        environ.get("BITGET_MONITOR_STALL_TIMEOUT_SECONDS", str(max(interval * 3, 90)))
    )
    heartbeat_path = environ.get("BITGET_MONITOR_HEARTBEAT_PATH", _DEFAULT_HEARTBEAT_PATH)
    from fatty_trader.execution.bitget_fallback_protection import load_active

    stream, watchdog, watchdog_interval = build_bitget_monitor_protection(
        environ,
        client,
        active_symbol_source=lambda: [entry["symbol"] for entry in load_active()],
        kill_switch=repository,
    )
    try:
        await run_bitget_monitor_loop(
            monitor,
            interval=interval,
            stream=stream,
            watchdog=watchdog,
            watchdog_interval=watchdog_interval,
            heartbeat_path=heartbeat_path,
            cycle_timeout=cycle_timeout,
            stall_timeout=stall_timeout,
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
    if name == "paper-kaka":
        await run_paper_kaka(os.environ)
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


def build_codex_runner(
    environ: Mapping[str, str], *, popen_factory: Callable[..., Any] | None = None
) -> CodexRunner:
    """Pass only purpose-supplied Codex auth, never the service capability environment.

    CODEX_HOME must be a dedicated CLI home with only auth.json mounted read-only;
    the runner ignores its user config/rules rather than enabling local MCP tools.
    This mapping is explicit: no fallback to the parent process environment.
    """
    return CodexRunner(
        auth_env={
            key: environ[key] for key in ("CODEX_HOME", "OPENAI_API_KEY") if environ.get(key)
        },
        popen_factory=popen_factory,
    )


@owned_worker_health("analyzer")
async def run_analyzer(environ: Mapping[str, str]) -> None:
    """Continuously analyze durable RECEIVED rows and enqueue DEMO intents."""
    import psycopg

    runner = build_codex_runner(environ)
    account_label = environ.get("CODEX_ACCOUNT_LABEL", "unset")
    codex_cli = "available" if shutil.which("codex") else "unavailable"
    poll_seconds = float(environ.get("ANALYZER_POLL_SECONDS", "5"))
    batch_size = int(environ.get("ANALYZER_BATCH_SIZE", "10"))
    while True:
        processed = process_received_batch(
            psycopg.connect,
            runner=runner,
            limit=batch_size,
            exchanges=enabled_dispatch_exchanges(environ),
            channel_ids=analyzer_channel_ids(environ),
            image_analysis_enabled=environ.get("BITGET_IMAGE_ANALYSIS_ENABLED", "0") == "1",
        )
        mode = environ.get("TRADER_MODE", "DEMO").upper()
        print(
            f"service=analyzer mode={mode} state=ready processed={processed} "
            # "account_label" is a static operator-supplied name, not an auth probe.
            # The old key name read as a status and led an audit to conclude Codex was
            # unconfigured while its token was valid.
            f"codex_cli={codex_cli} codex_account_label={account_label}",
            flush=True,
        )
        worker_progress()
        await asyncio.sleep(poll_seconds if processed == 0 else 0)


@owned_worker_health("operator-bot")
async def run_operator_bot(environ: Mapping[str, str]) -> None:
    """Run the private authenticated operator command listener in a dedicated thread."""
    import psycopg

    from fatty_trader.exchanges.bitget.client import BitgetRestClient
    from fatty_trader.operator.bitget_gateway import BitgetOperatorGateway
    from fatty_trader.operator.health import (
        build_operator_diagnostic,
        create_shared_health_reader,
        load_operator_health_snapshot,
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
        health_reader=create_shared_health_reader(
            lambda: load_operator_health_snapshot(
                gateway,
                psycopg.connect,
                mode=environ.get("TRADER_MODE", "DEMO"),
                venue_mode=mode,
                execution_enabled=environ.get("BITGET_EXECUTION_ENABLED", "0") == "1",
            )
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
                    worker_progress()
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


def analyzer_channel_ids(environ: Mapping[str, str]) -> tuple[int, ...]:
    """Channels whose messages may reach the live lane. Everything else stays paper-only."""
    raw = environ.get("ANALYZER_CHANNEL_IDS", "-1001252615519")
    ids = tuple(int(part.strip()) for part in raw.split(",") if part.strip().lstrip("-").isdigit())
    if not ids:
        raise ValueError("ANALYZER_CHANNEL_IDS must contain at least one channel id")
    return ids


def intake_settings(environ: Mapping[str, str]) -> TelegramSettings | None:
    """Return settings only when fully configured; missing config disables intake."""
    try:
        return TelegramSettings.from_mapping(environ)
    except ValueError:
        return None


@owned_worker_health("intake")
async def run_intake(
    environ: Mapping[str, str],
    *,
    client_factory: Any = build_telethon_client,
    repository: Any = None,
    connection_factory: Any = None,
    peer_id_of: Any = None,
) -> None:
    """Run the real Telethon intake, or remain inert when config is absent."""
    config = service_config("intake", environ)
    settings = intake_settings(environ)
    if settings is None:
        print(
            f"service=intake mode={config.mode} state=disabled reason=missing-telegram-config",
            flush=True,
        )
        while True:
            await asyncio.sleep(float(environ.get("WORKER_HEARTBEAT_SECONDS", "30")))
    client = client_factory(settings)
    if repository is None:
        import psycopg

        connection_factory = connection_factory or psycopg.connect
        repository = PostgresRawMessageRepository(connection_factory)
    forwarder = TelegramForwarder(client, settings, repository)
    await forwarder.attach()
    await client.start()
    print(f"service=intake mode={config.mode} state=ready", flush=True)
    # Safety net for the realtime-only listen path: a dropped update stream used to lose
    # every message in the blind window permanently (2026-09-27: the source's ENA signal).
    catchup_seconds = float(environ.get("TELEGRAM_CATCHUP_SECONDS", "60"))
    catchup_task: asyncio.Task[Any] | None = None
    if catchup_seconds > 0 and connection_factory is not None:
        from fatty_trader.intake.catchup import (
            build_cursor_lookup,
            run_catchup_loop,
            telegram_peer_id,
        )

        catchup_task = asyncio.create_task(
            run_catchup_loop(
                interval=catchup_seconds,
                client=client,
                forwarder=forwarder,
                channels=settings.channels,
                cursor_lookup=build_cursor_lookup(connection_factory),
                per_run_limit=int(environ.get("TELEGRAM_CATCHUP_LIMIT", "50")),
                peer_id_of=peer_id_of or telegram_peer_id,
            )
        )
        print(
            f"service=intake event=catchup-armed interval_seconds={catchup_seconds}",
            flush=True,
        )

    async def connected_progress() -> None:
        # Same event loop as Telethon: no detached thread can mask a stalled loop.
        while client.is_connected():
            worker_progress()
            await asyncio.sleep(10)

    health_task = asyncio.create_task(connected_progress())
    try:
        await client.run_until_disconnected()
    finally:
        health_task.cancel()
        await asyncio.gather(health_task, return_exceptions=True)
        if catchup_task is not None:
            catchup_task.cancel()


async def run_paper_kaka(environ: Mapping[str, str]) -> None:
    """Mirror the `Kaka trades` channel into the paper ledger. No venue access at all.

    Deliberately separate from the analyzer/dispatcher path: those write live dispatches,
    and a paper source must have no route into the money lane.
    """
    import json

    import psycopg
    from psycopg.rows import dict_row

    from fatty_trader.analyzer.image_analysis import (
        analyze_image_json,
        image_source_revision,
        signal_from_image_json,
    )
    from fatty_trader.analyzer.market_price import public_last_price
    from fatty_trader.kaka.parser import KakaEvent, KakaEventType
    from fatty_trader.kaka.worker import process_paper_batch

    image_analysis_enabled = environ.get("PAPER_KAKA_IMAGE_ANALYSIS", "0") == "1"
    digest_hour = int(environ.get("PAPER_KAKA_DIGEST_HOUR_UTC", "17"))

    def image_analyzer(path: str, *, message_id: int) -> Any:
        """Chart-only messages: reuse the analyzer's vision path, then map to a paper event."""
        # The worker's legacy callback carries only the path and message id. Read the
        # exact intake identity rather than inventing a revision from a message number.
        connection = psycopg.connect(row_factory=dict_row)
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT raw_text, revision_hash, media_sha256 FROM telegram_messages "
                "WHERE channel_id = %s AND message_id = %s AND media_path = %s",
                (channel_id, message_id, path),
            )
            source = cursor.fetchone()
        finally:
            connection.close()
        if source is None:
            return None
        text = str(source.get("raw_text") or "")
        revision = image_source_revision(
            image_path=path,
            channel_id=channel_id,
            message_id=message_id,
            text=text,
            canonical_revision=source.get("revision_hash"),
            media_sha256=source.get("media_sha256"),
        )
        runner = build_codex_runner(environ)
        data = analyze_image_json(
            text=text,
            message_id=message_id,
            image_path=path,
            runner=runner,
        )
        signal = signal_from_image_json(
            data,
            message_id=message_id,
            source_revision=revision,
            original_text=text,
        )
        if signal is None:
            return None
        return KakaEvent(
            KakaEventType.OPEN,
            symbol=f"{signal.pair_token}USDT",
            side="LONG" if signal.direction.value == "LONG" else "SHORT",
            entry=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profits[0] if signal.take_profits else None,
            message_id=message_id,
            raw=json.dumps(
                {
                    "kind": "image",
                    "source_revision": revision,
                    "media_sha256": source.get("media_sha256"),
                },
                separators=(",", ":"),
            ),
        )

    poll_seconds = float(environ.get("PAPER_KAKA_POLL_SECONDS", "20"))
    channel_id = int(environ.get("PAPER_KAKA_CHANNEL_ID", "-1003763643270"))
    limit = int(environ.get("PAPER_KAKA_BATCH_SIZE", "25"))
    print(
        f"service=paper-kaka state=ready channel_id={channel_id} poll_seconds={poll_seconds}",
        flush=True,
    )
    while True:
        try:
            counts = process_paper_batch(
                lambda: psycopg.connect(row_factory=dict_row),
                market_price_lookup=public_last_price,
                channel_id=channel_id,
                limit=limit,
                image_analyzer=image_analyzer if image_analysis_enabled else None,
            )
        except Exception as exc:  # noqa: BLE001 - keep the loop alive, surface the reason
            print(f"service=paper-kaka state=error error={exc!r}", flush=True)
            await asyncio.sleep(poll_seconds)
            continue
        if counts["messages"]:
            print(
                "service=paper-kaka " + " ".join(f"{key}={value}" for key, value in counts.items()),
                flush=True,
            )
        await _maybe_enqueue_digest(digest_hour)
        await asyncio.sleep(poll_seconds if counts["messages"] == 0 else 0)


async def _maybe_enqueue_digest(digest_hour_utc: int) -> None:
    """Queue the paper digest once per day (the outbox dedup key makes it idempotent)."""
    import psycopg
    from psycopg.rows import dict_row

    from fatty_trader.kaka.worker import build_digest_text, enqueue_digest

    now = datetime.now(UTC)
    if now.hour < digest_hour_utc:
        return
    day = now.date().isoformat()
    day_start = now - timedelta(hours=24)
    try:
        connection = psycopg.connect(row_factory=dict_row)
        try:
            cursor = connection.cursor()
            text = build_digest_text(cursor, day_start=day_start.isoformat())
            queued = enqueue_digest(cursor, dedup_key=f"kaka-paper-digest:{day}", text=text)
            connection.commit()
        finally:
            connection.close()
    except Exception as exc:  # noqa: BLE001 - a digest failure must not stop the lane
        print(f"service=paper-kaka state=digest-error error={exc!r}", flush=True)
        return
    if queued:
        print(f"service=paper-kaka state=digest-queued day={day}", flush=True)


def check_monitor_heartbeat(path: str, max_age: float | None) -> int:
    """Return 0 when the monitor heartbeat is fresh, non-zero when it is not.

    Used by the container healthcheck: configuration validation alone cannot tell a
    working monitor from one that stopped cycling hours ago.
    """
    if max_age is None or not math.isfinite(max_age) or max_age <= 0:
        print("check_failed=heartbeat-max-age-required", flush=True)
        return 2
    age = monitor_heartbeat_age(path)
    if not monitor_heartbeat_is_fresh(path, max_age):
        print(f"check_failed=monitor-stalled heartbeat_age={age} max_age={max_age}", flush=True)
        return 1
    age_label = "n/a" if age is None else f"{age:.3f}"
    print(f"check_ok=monitor-live heartbeat_age={age_label}", flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fatty trader isolated service runner")
    parser.add_argument("--service", required=True)
    parser.add_argument("--check", action="store_true")
    # Liveness probe used by the monitor healthcheck: with --check it also verifies
    # the heartbeat is fresh, so a process that stopped cycling can no longer be
    # reported healthy.
    parser.add_argument("--heartbeat-path", default=None)
    parser.add_argument("--heartbeat-max-age", type=float, default=None)
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
        if args.service in {"intake", "analyzer", "operator-bot"}:
            from fatty_trader.worker_health import check_worker_health

            return check_worker_health(args.service, os.environ)
        service_config(args.service, os.environ)
        if args.heartbeat_path is not None:
            return check_monitor_heartbeat(args.heartbeat_path, args.heartbeat_max_age)
        return 0
    asyncio.run(run_worker(args.service))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
