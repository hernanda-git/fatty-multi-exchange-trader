"""Bitget monitor: reconciles provider state, alerts, and latches on anomalies.

It owns no provider mutation methods. Anomalies become a durable kill-switch latch
when enforcement is on (LIVE lane); a persisted post-fill margin/leverage mismatch
is treated as a latchable anomaly so it blocks new entries across restarts.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from fatty_trader.exchanges.bitget.live import LiveIntentStoreProtocol
from fatty_trader.exchanges.bitget.native_close import bind_native_plan_close_from_fill
from fatty_trader.exchanges.bitget.provider_fill_reconciliation import (
    ProviderFillReconciliationError,
    reconcile_provider_exit,
)
from fatty_trader.exchanges.bitget.reconciliation import reconcile_unknown_intent
from fatty_trader.exchanges.bitget.reconciliation_live import confirm_native_stop_protection
from fatty_trader.execution.async_storage import _run_sync_storage
from fatty_trader.execution.protection import ProtectionReport, ProtectionState
from fatty_trader.storage.reconciliation import ReconciliationRepository

_ALERT_ONLY_PROTECTION_REASONS = frozenset({"missing-stop-loss", "missing-take-profit"})
# How far back a venue fill is still worth resolving against our own plans.
_NATIVE_CLOSE_LOOKBACK_SECONDS = 900

#: Latch reasons that describe a *transient* condition and are therefore released
#: automatically once the check passes again.
#:
#: `clock-skew-unavailable` is "the venue clock could not be read this cycle" — a network
#: blip, not a policy failure. Latched permanently it becomes an absorbing state: the
#: entry lane stays closed for days after a single timeout (observed 2026-10-18 19:02,
#: still latched the next day while the venue read -28 ms). Durable divergences
#: (reconciliation mismatches, kill-switch policy) are NOT in this set and never
#: self-clear.
_AUTO_RELEASABLE_KILL_SWITCH_REASONS = frozenset({"clock-skew-unavailable", "clock-skew-exceeded"})

#: Audit reference recorded on an automatic release.
_AUTO_RELEASE_REFERENCE = "auto:clock-check-recovered"

#: Durable outbox payload kind for a position whose venue protection is not confirmed.
#:
#: It deliberately has no bespoke Telegram formatter: the generic key/value renderer
#: then prints every field, which is what makes the alert actionable on a phone. A
#: reason-only card ("missing-stop-loss") is not enough for the owner to act.
_PROTECTION_ALERT_KIND = "protection-alert"

#: The one line that says what the bot will NOT do about this position.
_PROTECTION_ALERT_ACTION = (
    "bot will not close this position; check the exchange side and manage it manually"
)

#: Marker the execution adapter appends to its degraded verdict.
_LOCALLY_MANAGED_MARKER = "-locally-managed"

#: Monitor-process truth about the local price-monitor lane.
_STREAM_ENABLED_ENV = "BITGET_PROTECTION_STREAM_ENABLED"
_STREAM_MUTATIONS_ENV = "BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED"
_STREAM_STALE_SECONDS_ENV = "BITGET_PROTECTION_STREAM_STALE_SECONDS"
_DEFAULT_STREAM_STALE_SECONDS = 5.0

_ENTRY_PRICE_KEYS = ("openPriceAvg", "averageOpenPrice", "avgPrice", "openPrice")
_STOP_LOSS_KEYS = ("stopLoss", "stopLossPrice")
_TAKE_PROFIT_KEYS = ("takeProfit", "takeProfitPrice")
_EPOCH_KEYS = ("cTime", "ctime", "createdTime")


class BitgetMonitorClient(Protocol):
    async def get_all_positions(self) -> Any: ...
    async def get_pending_orders(self) -> Any: ...
    async def get_pending_plan_orders(self, symbol: str) -> Any: ...
    async def get_single_position(self, symbol: str) -> Any: ...
    async def get_order_detail(
        self, symbol: str, *, client_oid: str | None = None, order_id: str | None = None
    ) -> Any: ...
    async def get_fills(self, symbol: str | None = None) -> Any: ...
    async def get_clock_skew_ms(self) -> int: ...


@dataclass(frozen=True)
class MonitorReport:
    status: str
    reasons: tuple[str, ...] = ()
    provider_exits_reconciled: int = 0
    latched_reason: str | None = None


def _enabled(environ_key: str) -> bool:
    """Read a 0/1 switch from the monitor process environment."""
    return os.environ.get(environ_key, "0").strip().lower() == "1"


def _first_text(source: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _position_direction(position: Mapping[str, Any]) -> str:
    side = str(position.get("holdSide", position.get("side", ""))).strip().lower()
    return {"long": "LONG", "short": "SHORT", "buy": "LONG", "sell": "SHORT"}.get(side, "UNKNOWN")


def _local_lane_rows() -> list[dict[str, Any]]:
    """Read durable local-management rows from the REST lane's own table."""
    from fatty_trader.execution.bitget_fallback_protection import load_active

    return list(load_active())


def _symbol_capability(symbol: str, environment: str | None = None) -> Any | None:
    """Read the durable protection-capability observation for one symbol."""
    import psycopg

    from fatty_trader.storage.protection_capabilities import (
        PostgresProtectionCapabilityRepository,
    )

    scope = (environment or os.environ.get("BITGET_MODE", "")).strip().upper()
    if scope not in {"DEMO", "LIVE"}:
        return None
    return PostgresProtectionCapabilityRepository(psycopg.connect).get("bitget", scope, symbol)


def _stream_health(capability: Any | None, *, read_ok: bool) -> dict[str, Any]:
    """Describe whether the stream trigger is armed and currently fresh.

    The stream mutates only while its own switch is on and its mark feed is fresh;
    a connected-but-stale socket still cannot close a locally-managed position.
    """
    enabled = _enabled(_STREAM_ENABLED_ENV)
    health: dict[str, Any] = {
        "stream_enabled": enabled,
        "stream_mutations_enabled": _enabled(_STREAM_MUTATIONS_ENV),
        "stream_state": "DISABLED" if not enabled else "UNKNOWN",
        "stream_fresh": None,
        "stream_last_event_seconds": None,
    }
    if capability is None:
        if enabled and read_ok:
            health["stream_state"] = "NOT-REGISTERED"
        return health
    state = str(getattr(capability, "stream_state", "UNKNOWN")).upper()
    health["stream_state"] = state
    last_event = getattr(capability, "last_stream_at", None)
    if not isinstance(last_event, datetime):
        return health
    now = datetime.now(UTC)
    if last_event.tzinfo is None:
        return health
    age = (now - last_event).total_seconds()
    health["stream_last_event_seconds"] = round(age, 3)
    try:
        raw_stale = os.environ.get(_STREAM_STALE_SECONDS_ENV, "").strip()
        stale_after = float(raw_stale) if raw_stale else _DEFAULT_STREAM_STALE_SECONDS
    except ValueError:
        stale_after = _DEFAULT_STREAM_STALE_SECONDS
    health["stream_fresh"] = state == "HEALTHY" and 0 <= age <= stale_after
    return health


def _protection_alert_payload(
    *,
    scope: str,
    position: Mapping[str, Any],
    quantity: Decimal,
    report: ProtectionReport,
    reason: str,
    lane_row: Mapping[str, Any] | None,
    lane_read_ok: bool,
    rest_mutations_enabled: bool,
    capability: Any | None,
    capability_read_ok: bool,
) -> dict[str, Any]:
    """Build the flat, human-and-agent readable payload for one naked position.

    Flat keys on purpose: the generic notification renderer prints one line per key,
    so nothing is lost on a phone and nothing needs a bespoke formatter.
    """
    symbol = str(position.get("symbol") or "").strip().upper()
    epoch = _first_text(position, _EPOCH_KEYS) or (
        str(lane_row.get("provider_position_epoch")) if lane_row else None
    )
    position_key = str(lane_row.get("position_key")) if lane_row else None
    stop_loss = (
        _first_text(lane_row, ("stop_loss",))
        if lane_row
        else _first_text(position, _STOP_LOSS_KEYS)
    )
    take_profits: list[str] = []
    if lane_row:
        raw = lane_row.get("take_profits")
        if isinstance(raw, (list, tuple)):
            take_profits = [str(item) for item in raw]
        elif raw is not None:
            take_profits = [str(raw)]
    else:
        single = _first_text(position, _TAKE_PROFIT_KEYS)
        if single is not None:
            take_profits = [single]
    locally_managed = lane_row is not None
    last_error = str(getattr(capability, "last_error", "") or "").strip() or None
    locally_managed_verdict = locally_managed or (
        last_error is not None and last_error.endswith(_LOCALLY_MANAGED_MARKER)
    )
    verdict = reason
    if locally_managed_verdict and not verdict.endswith(_LOCALLY_MANAGED_MARKER):
        verdict = f"{verdict}{_LOCALLY_MANAGED_MARKER}"
    lane_state = (
        str(lane_row.get("state"))
        if lane_row
        else ("unreadable" if not lane_read_ok else "unregistered")
    )
    health = _stream_health(capability, read_ok=capability_read_ok)
    return {
        "kind": _PROTECTION_ALERT_KIND,
        "reason": reason,
        "verdict": verdict,
        "capability_readback_reason": last_error or "unavailable",
        "scope": scope,
        "symbol": symbol,
        "direction": _position_direction(position),
        "quantity": str(quantity),
        "entry_price": _first_text(position, _ENTRY_PRICE_KEYS) or "unavailable",
        "stop_loss": stop_loss or "unavailable",
        "take_profits": take_profits,
        "position_key": position_key,
        "provider_position_epoch": epoch,
        "locally_managed": locally_managed,
        "local_lane_state": lane_state,
        "local_lane_rest_mutations_enabled": rest_mutations_enabled,
        "local_lane_stream_enabled": health["stream_enabled"],
        "local_lane_stream_mutations_enabled": health["stream_mutations_enabled"],
        "local_lane_stream_state": health["stream_state"],
        "local_lane_stream_fresh": health["stream_fresh"],
        "local_lane_stream_last_event_seconds": health["stream_last_event_seconds"],
        "local_lane_registration_read_ok": lane_read_ok,
        "protection_state": str(getattr(report.state, "value", report.state)),
        "observed_quantity": str(report.observed_quantity),
        "action": _PROTECTION_ALERT_ACTION,
    }


class BitgetMonitor:
    """Observe and reconcile only; it contains no provider mutation methods."""

    def __init__(
        self,
        client: BitgetMonitorClient,
        repository: ReconciliationRepository,
        *,
        scope: str = "bitget",
        max_clock_skew_ms: int = 10_000,
        enforce_kill_switch: bool = True,
        fallback_mutations_enabled: bool = False,
        live_intent_store: LiveIntentStoreProtocol | None = None,
    ) -> None:
        if max_clock_skew_ms < 0:
            raise ValueError("max_clock_skew_ms must be non-negative")
        self._client = client
        self._repository = repository
        self._scope = scope
        self._max_clock_skew_ms = max_clock_skew_ms
        self._enforce_kill_switch = enforce_kill_switch
        self._fallback_mutations_enabled = fallback_mutations_enabled
        self._live_intent_store = live_intent_store

    async def run_once(self) -> MonitorReport:
        reasons: list[str] = []
        await self._reconcile_unresolved_intents(reasons)
        provider_exits_reconciled = await self._reconcile_provider_exits(reasons)
        positions = await self._read_rows(
            self._client.get_all_positions, "provider-positions-invalid", reasons
        )
        orders = await self._read_rows(
            self._client.get_pending_orders, "provider-orders-invalid", reasons
        )
        await self._check_positions(positions, reasons)
        await self._check_orders(orders, reasons)
        # A post-fill margin/leverage mismatch is durable evidence that a position is
        # not what the plan promised. The adapter that recorded it cannot block a
        # replacement worker, so the latch lives here: it survives restarts and blocks
        # new entries until an operator releases the switch with an approval reference.
        if await _run_sync_storage(self._repository.has_unhandled_post_fill_mismatch, self._scope):
            reasons.append("post-fill-margin-or-leverage-mismatch")
        # Bot-managed TP/SL fallback for symbols that reject native SL/TP (43011)
        await self._run_fallback_monitor(reasons)
        lifecycle = getattr(self._live_intent_store, "verified_close_lifecycle", None)
        if lifecycle is not None:
            try:
                await lifecycle.reconcile(self._client)
            except Exception:
                # Retain reservations and surface read/DB failures; never infer release.
                reasons.append("verified-close-evidence-unavailable")
        try:
            skew = await self._client.get_clock_skew_ms()
        except Exception:
            reasons.append("clock-skew-unavailable")
        else:
            if abs(skew) > self._max_clock_skew_ms:
                reasons.append("clock-skew-exceeded")
        unique_reasons = tuple(dict.fromkeys(reasons))
        if unique_reasons:
            latchable_reasons = tuple(
                reason
                for reason in unique_reasons
                if reason not in _ALERT_ONLY_PROTECTION_REASONS
                and not reason.startswith(("fallback-sl_hit:", "fallback-tp_hit:"))
            )
            if not latchable_reasons:
                return MonitorReport("degraded", unique_reasons, provider_exits_reconciled)
            if not self._enforce_kill_switch:
                return MonitorReport("degraded", unique_reasons, provider_exits_reconciled)
            for reason in latchable_reasons:
                await _run_sync_storage(self._repository.latch_kill_switch, self._scope, reason)
            return MonitorReport(
                "kill-switch-latched", latchable_reasons, provider_exits_reconciled
            )
        if self._enforce_kill_switch and await _run_sync_storage(
            self._repository.kill_switch_active, self._scope
        ):
            latched_reason = await _run_sync_storage(
                self._repository.kill_switch_reason, self._scope
            )
            if latched_reason in _AUTO_RELEASABLE_KILL_SWITCH_REASONS:
                # This cycle found nothing latchable, so the transient clock condition is
                # over: release it instead of leaving the entry lane closed forever.
                await _run_sync_storage(
                    self._repository.release_kill_switch, self._scope, _AUTO_RELEASE_REFERENCE
                )
                logging.getLogger(__name__).info(
                    "Bitget monitor released transient kill switch: "
                    "scope=%s reason=%s reference=%s",
                    self._scope,
                    latched_reason,
                    _AUTO_RELEASE_REFERENCE,
                )
            else:
                return MonitorReport(
                    "kill-switch-latched",
                    provider_exits_reconciled=provider_exits_reconciled,
                    latched_reason=latched_reason,
                )
        return MonitorReport("ok", provider_exits_reconciled=provider_exits_reconciled)

    async def _run_fallback_monitor(self, reasons: list[str]) -> None:
        """Run bot-managed TP/SL only behind a separate explicit mutation gate."""
        # Entry admission latches do not shut down existing reduce-only stops.
        # Operator-directed protection shutdown remains the explicit mutation gate.
        if not self._fallback_mutations_enabled:
            return
        try:
            from fatty_trader.execution.bitget_fallback_protection import (
                run_fallback_monitor_async,
            )

            triggered = await run_fallback_monitor_async(
                self._client, environment=getattr(self._client, "environment", None)
            )
            for t in triggered:
                reasons.append(f"fallback-{t['reason']}:{t['symbol']}")
        except Exception as exc:
            reasons.append(f"fallback-monitor-error:{exc}")

    async def _reconcile_unresolved_intents(self, reasons: list[str]) -> None:
        intents = await _run_sync_storage(self._repository.unresolved_intents, self._scope)
        for intent in intents:
            try:
                reconciled = await reconcile_unknown_intent(
                    intent,
                    read_order_detail=lambda symbol, oid: self._client.get_order_detail(
                        symbol, client_oid=oid
                    ),
                    read_fills=self._client.get_fills,
                    read_position=lambda symbol: self._client.get_single_position(symbol),
                    read_pending_orders=lambda _symbol: self._client.get_pending_orders(),
                )
                await _run_sync_storage(self._repository.update_intent, reconciled)
            except Exception:
                reasons.append(f"unknown-intent-unreconciled:{intent.client_oid}")

    async def _reconcile_provider_exits(self, reasons: list[str]) -> int:
        """Persist unmatched provider exit fills without treating entries as exits."""
        store = self._live_intent_store
        if store is None:
            return 0
        try:
            raw_fills = await self._client.get_fills(None)
        except Exception as exc:
            # Never print provider bodies or arbitrary exception text (may contain secrets).
            code = str(getattr(exc, "code", ""))
            http_status = getattr(exc, "http_status", None)
            logging.getLogger(__name__).warning(
                "reason=provider-fills-read-failed exception=%s code=%s http_status=%s",
                type(exc).__name__,
                code if code.isdecimal() else "unavailable",
                http_status if isinstance(http_status, int) else "unavailable",
            )
            reasons.append("provider-fills-read-failed")
            return 0
        if isinstance(raw_fills, dict):
            raw_fills = raw_fills.get("fillList")
        if not isinstance(raw_fills, list) or not all(isinstance(fill, dict) for fill in raw_fills):
            logging.getLogger(__name__).warning(
                "reason=provider-fills-invalid shape=%s", type(raw_fills).__name__
            )
            reasons.append("provider-fills-invalid")
            return 0
        reconciled = 0
        for fill in raw_fills:
            if await self._bind_native_plan_close(fill):
                # This fill is our own protection plan executing. It belongs to the
                # entry that owns the plan, not to the generic provider-exit path.
                reconciled += 1
                continue
            if not _looks_like_external_exit(fill):
                continue
            try:
                result = await _run_sync_storage(reconcile_provider_exit, store, fill)
            except ProviderFillReconciliationError:
                reasons.append("provider-fill-reconciliation-error")
                continue
            if result.created:
                reconciled += 1
        return reconciled

    async def _bind_native_plan_close(self, fill: Mapping[str, Any]) -> bool:
        """Bind a venue fill that executed one of our own protection plans.

        The venue fill carries no client order id, so the executed order is read
        once: its client order id is the provider id the venue issued for our
        plan. Only recent fills are examined, so a cycle never reads the whole
        venue history.
        """
        store = self._live_intent_store
        if store is None:
            return False
        owner = getattr(store, "protection_intent_by_provider_order_id", None)
        if not callable(owner):
            return False
        if not _is_recent_fill(fill, _NATIVE_CLOSE_LOOKBACK_SECONDS):
            return False
        try:
            return bool(
                await bind_native_plan_close_from_fill(
                    store=store,
                    fill=fill,
                    read_order_detail=lambda symbol, order_id: self._client.get_order_detail(
                        symbol, order_id=order_id
                    ),
                    plan_owner=lambda client_oid: owner(self._scope, client_oid),
                )
            )
        except Exception:
            return False

    async def _read_rows(
        self,
        reader: Callable[[], Awaitable[Any]],
        invalid_reason: str,
        reasons: list[str],
    ) -> list[dict[str, Any]]:
        try:
            result = await reader()
        except Exception as exc:
            read_reason = invalid_reason.removesuffix("invalid") + "read-failed"
            code = str(getattr(exc, "code", ""))
            logging.getLogger(__name__).warning(
                "reason=%s exception=%s code=%s",
                read_reason,
                type(exc).__name__,
                code if code.isdecimal() else "unavailable",
            )
            reasons.append(read_reason)
            return []
        if isinstance(result, dict) and set(result) <= {"entrustedList", "endId"}:
            result = result.get("entrustedList") or []
        if not isinstance(result, list) or not all(isinstance(row, dict) for row in result):
            logging.getLogger(__name__).warning(
                "reason=%s shape=%s", invalid_reason, type(result).__name__
            )
            reasons.append(invalid_reason)
            return []
        return result

    async def _check_positions(self, positions: list[dict[str, Any]], reasons: list[str]) -> None:
        expected_symbols = await _run_sync_storage(
            self._repository.expected_position_symbols, self._scope
        )
        for position in positions:
            quantity = _open_quantity(position)
            if quantity is None:
                reasons.append("provider-position-invalid")
                continue
            if quantity == 0:
                continue
            symbol = position.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                reasons.append("provider-position-invalid")
                continue
            if symbol not in expected_symbols:
                reasons.append(f"unexpected-position:{symbol}")
                continue

            async def read_position(target: Any = symbol) -> Any:
                return await self._client.get_single_position(target)

            async def read_pending_plans(target: Any = symbol) -> Any:
                return await self._client.get_pending_plan_orders(target)

            report = await confirm_native_stop_protection(
                read_position,
                read_pending_plans,
                symbol=symbol,
                expected_quantity=quantity,
            )
            if report.state is not ProtectionState.VENUE_PROTECTED:
                reasons.append(report.reason or "native-protection-unconfirmed")
                await self._alert_unconfirmed_protection(position, quantity, report)

    async def _alert_unconfirmed_protection(
        self,
        position: Mapping[str, Any],
        quantity: Decimal,
        report: ProtectionReport,
    ) -> None:
        """Alert once per position epoch; never close and never latch this position.

        A naked position stays open by owner policy: we cannot prove the venue's stop,
        so the local price monitors are the only automatic exit and a human owns the
        rest. Emitting the same alert twice for one epoch is prevented by the outbox
        dedup key, exactly like the kill-switch latch dedups its own notification.
        """
        symbol = str(position.get("symbol") or "").strip().upper()
        reason = report.reason or "native-protection-unconfirmed"
        lane_row, lane_read_ok = await self._local_management_row(symbol)
        capability, capability_read_ok = await self._protection_capability(symbol)
        adapter_verdict = str(getattr(capability, "last_error", "") or "").endswith(
            _LOCALLY_MANAGED_MARKER
        )
        if (
            reason not in _ALERT_ONLY_PROTECTION_REASONS
            and lane_row is None
            and not adapter_verdict
            and not reason.endswith(_LOCALLY_MANAGED_MARKER)
        ):
            # Any other readback already notifies through the kill-switch latch.
            return
        payload = _protection_alert_payload(
            scope=self._scope,
            position=position,
            quantity=quantity,
            report=report,
            reason=reason,
            lane_row=lane_row,
            lane_read_ok=lane_read_ok,
            rest_mutations_enabled=self._fallback_mutations_enabled,
            capability=capability,
            capability_read_ok=capability_read_ok,
        )
        dedup_key = (
            f"protection-alert:{self._scope}:{symbol}:"
            f"{payload['provider_position_epoch'] or payload['position_key'] or 'unknown'}:{reason}"
        )
        alert = getattr(self._repository, "alert_protection_unconfirmed", None)
        if not callable(alert):
            # Never silent: the structured line carries the whole payload.
            logging.getLogger(__name__).error(
                "reason=protection-alert-sink-unavailable payload=%s",
                json.dumps(payload, sort_keys=True, default=str),
            )
            return
        try:
            await _run_sync_storage(alert, self._scope, dedup_key, payload)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "reason=protection-alert-write-failed symbol=%s exception=%s",
                symbol,
                type(exc).__name__,
            )

    async def _local_management_row(self, symbol: str) -> tuple[dict[str, Any] | None, bool]:
        """Return the durable local-management row and whether the read worked."""
        try:
            rows = await _run_sync_storage(_local_lane_rows)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "reason=local-lane-read-failed symbol=%s exception=%s", symbol, type(exc).__name__
            )
            return None, False
        for row in rows:
            if str(row.get("symbol", "")).strip().upper() == symbol:
                return row, True
        return None, True

    async def _protection_capability(self, symbol: str) -> tuple[Any | None, bool]:
        """Return the durable capability observation and whether the read worked."""
        environment = getattr(self._client, "environment", None)
        try:
            capability = await _run_sync_storage(_symbol_capability, symbol, environment)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "reason=protection-capability-read-failed symbol=%s exception=%s",
                symbol,
                type(exc).__name__,
            )
            return None, False
        return capability, True

    async def _check_orders(self, orders: list[dict[str, Any]], reasons: list[str]) -> None:
        intents = await _run_sync_storage(self._repository.unresolved_intents, self._scope)
        known = {intent.client_oid for intent in intents}
        for order in orders:
            oid = order.get("clientOid", order.get("client_oid"))
            if not isinstance(oid, str) or oid not in known:
                reasons.append(f"unexpected-order:{oid if isinstance(oid, str) else 'unknown'}")


def _open_quantity(position: dict[str, Any]) -> Decimal | None:
    try:
        quantity = Decimal(str(position.get("total", position.get("size", "0"))))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return quantity if quantity.is_finite() and quantity >= 0 else None


def _looks_like_external_exit(fill: dict[str, Any]) -> bool:
    """Require provider evidence that an unmatched fill is an exit."""
    if any(fill.get(field) for field in ("clientOid", "client_oid", "clientOrderId")):
        return False
    source = str(fill.get("enterPointSource", fill.get("orderSource", ""))).strip().upper()
    trade_side = str(fill.get("tradeSide", "")).strip().lower()
    reduce_only = str(fill.get("reduceOnly", "")).strip().upper()
    return source == "SYS" or trade_side.startswith("burst_") or reduce_only in {"YES", "TRUE"}


def _is_recent_fill(fill: Mapping[str, Any], lookback_seconds: int) -> bool:
    """Whether a venue fill is recent enough to be worth resolving against our plans."""
    raw = fill.get("cTime", fill.get("fillTime", fill.get("uTime")))
    if raw is None:
        return False
    try:
        stamp = int(str(raw))
    except (TypeError, ValueError):
        return False
    if stamp > 10_000_000_000:
        stamp //= 1000
    if stamp <= 0:
        return False
    return (datetime.now(UTC).timestamp() - stamp) <= lookback_seconds
