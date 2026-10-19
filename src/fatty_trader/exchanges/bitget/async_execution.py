"""Async Bitget entry execution with intent-first, GET-only reconciliation."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue
from fatty_trader.exchanges.bitget.client import BitgetApiError, BitgetUnknownResultError
from fatty_trader.exchanges.bitget.live import (
    LiveIntentRecord,
    LiveIntentStoreProtocol,
    LiveOrderStatus,
    classify_live_order,
    normalize_fill,
    summarize_fills,
)
from fatty_trader.exchanges.bitget.protection_capability import (
    BitgetProtectionCapability,
    NativeProtectionState,
    StreamState,
)
from fatty_trader.exchanges.bitget.read_model import read_position_state
from fatty_trader.exchanges.bitget.reconciliation import classify_missing_detail
from fatty_trader.exchanges.bitget.reconciliation_live import (
    NativeProtectionExpectation,
    confirm_native_protection,
)
from fatty_trader.execution.protection import ProtectionPlan, ProtectionReport, ProtectionState
from fatty_trader.storage.live_intents import build_emergency_close_intent


class AsyncBitgetExecutionClient(Protocol):
    async def get_account(self, symbol: str) -> Any: ...

    async def place_entry_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: str,
        client_oid: str,
        order_type: str = "market",
        price: str | None = None,
    ) -> dict[str, Any]: ...

    async def get_order_detail(self, symbol: str, *, client_oid: str) -> Any: ...

    async def get_fills(self, symbol: str) -> Any: ...

    async def get_single_position(self, symbol: str) -> Any: ...

    async def get_pending_orders(self, symbol: str) -> Any: ...

    async def cancel_entry_order(self, *, symbol: str, client_oid: str) -> Any: ...

    async def get_pending_plan_orders(self, symbol: str) -> Any: ...

    async def place_position_tpsl(
        self,
        *,
        symbol: str,
        hold_side: str,
        quantity: str,
        stop_loss: str | None,
        stop_loss_execute_price: str | None,
        take_profit: str | None,
        take_profit_execute_price: str | None,
        stop_loss_client_oid: str | None,
        take_profit_client_oid: str | None,
        omit_market_execute_prices: bool = False,
    ) -> list[dict[str, Any]]: ...

    async def place_market_close(
        self, *, symbol: str, side: str, quantity: str, client_oid: str
    ) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


class BitgetEntryVeto(RuntimeError):
    """A winning durable claim was refused before any ENTRY POST began."""

    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome


@dataclass(frozen=True)
class AsyncExecutionResult:
    client_oid: str
    status: LiveOrderStatus
    filled_qty: Decimal
    avg_price: Decimal | None
    fee: Decimal
    provider_order_id: str | None
    provider_fill_ids: tuple[str, ...]
    provider_fills: tuple[dict[str, Any], ...] = ()
    provider_terminal: bool = False


@dataclass(frozen=True)
class AsyncProtectionResult:
    state: ProtectionState
    observed_quantity: Decimal
    reason: str | None = None
    emergency_close_oid: str | None = None


@dataclass(frozen=True)
class PostFillReconciliation:
    """Append-only comparison of durable admission against provider position truth."""

    exchange: str
    client_order_id: str
    symbol: str
    planned_leverage: int
    planned_margin_usdt: Decimal
    planned_notional_usdt: Decimal | None
    observed_leverage: Decimal | None
    observed_margin_mode: str | None
    observed_quantity: Decimal | None
    observed_entry_price: Decimal | None
    observed_mark_price: Decimal | None
    observed_margin_usdt: Decimal | None
    status: str
    reason: str | None
    observed_at: datetime


def _matching_fills(
    fills: list[dict[str, Any]], *, client_oid: str, provider_order_id: str | None
) -> list[dict[str, Any]]:
    """Use only fills explicitly tied to this durable entry intent."""
    matching: list[dict[str, Any]] = []
    for fill in fills:
        fill_client_oid = fill.get("clientOid", fill.get("client_oid"))
        fill_order_id = fill.get("orderId", fill.get("order_id"))
        if fill_client_oid == client_oid or (
            provider_order_id is not None and str(fill_order_id) == provider_order_id
        ):
            matching.append(fill)
    return matching


def _detail_decimal(detail: dict[str, Any], *keys: str) -> Decimal | None:
    for key in keys:
        raw = detail.get(key)
        if raw is None:
            continue
        try:
            value = Decimal(str(raw))
        except (ArithmeticError, TypeError, ValueError):
            continue
        if value.is_finite() and value > 0:
            return value
    return None


def _placement_plan_id(placement: list[dict[str, Any]], *, client_oid: str, leg: str) -> str | None:
    """Find the provider plan ID paired with one leg's returned client OID."""
    oid_field = f"{leg}ClientOid"
    for row in placement:
        returned_oid = row.get(oid_field, row.get("clientOid"))
        provider_id = row.get("orderId", row.get("planOrderId"))
        if (
            returned_oid is not None
            and str(returned_oid) == client_oid
            and provider_id is not None
            and str(provider_id).strip()
        ):
            return str(provider_id).strip()
    return None


class AsyncBitgetExecution:
    """Production async execution adapter; POST is followed only by read-back GETs."""

    def __init__(
        self,
        client: AsyncBitgetExecutionClient,
        venue: AsyncBitgetVenue,
        *,
        capability_repository: Any | None = None,
        reconciliation_repository: Any | None = None,
        entry_admission_latch: Any | None = None,
        fallback_protection_enabled: bool = False,
        emergency_containment_enabled: bool | None = None,
        environment: str = "DEMO",
        protection_grace_seconds: float = 0.0,
        protection_grace_interval_seconds: float = 3.0,
    ) -> None:
        self._client = client
        self._venue = venue
        self._degraded = False
        self._capability_repository = capability_repository
        self._reconciliation_repository = reconciliation_repository
        self._entry_admission_latch = entry_admission_latch
        # Bot-managed TP/SL is only real protection when the monitor that would
        # execute it is actually allowed to mutate. Registering a fallback row for
        # a symbol that rejects native SL/TP while that monitor is gated off leaves
        # the position naked, so the fallback path must fail closed instead.
        self._fallback_protection_enabled = fallback_protection_enabled
        # Emergency containment (market-closing a position whose venue SL/TP could not
        # be confirmed) is an explicit opt-in, default off. A filled position is never
        # closed implicitly for unconfirmed protection; the default path registers it
        # for local price management instead. Read from the environment here so the
        # wiring cannot silently enable the dangerous path.
        self._emergency_containment_enabled = (
            os.environ.get("BITGET_EMERGENCY_CONTAINMENT_ENABLED", "0").strip() == "1"
            if emergency_containment_enabled is None
            else emergency_containment_enabled
        )
        # Bounded, read-only grace before containment. Zero keeps library/test
        # behaviour unchanged; production wires a real budget from the environment so
        # a trade is never closed before the venue has had time to confirm the plans
        # it accepted (two live trades were washed out by that race in October 2026).
        self._protection_grace_seconds = max(0.0, protection_grace_seconds)
        self._protection_grace_interval_seconds = max(0.1, protection_grace_interval_seconds)
        self._environment = environment.strip().upper()
        if self._environment not in {"DEMO", "LIVE"}:
            raise ValueError("Bitget execution environment must be DEMO or LIVE")

    @property
    def degraded(self) -> bool:
        """Whether an unsafe fill has halted this adapter from additional entries."""
        return self._degraded

    async def aclose(self) -> None:
        """Close the owned async transport exactly once through its client boundary."""
        await self._client.aclose()

    async def submit_entry(self, intent: LiveIntentRecord) -> AsyncExecutionResult:
        return await self._submit_entry(intent)

    async def submit_entry_guarded(
        self, intent: LiveIntentRecord, final_entry_check: Callable[[], None]
    ) -> AsyncExecutionResult:
        """Revalidate source and durable admission after awaited provider setup."""
        return await self._submit_entry(intent, final_entry_check=final_entry_check)

    async def _submit_entry(
        self, intent: LiveIntentRecord, *, final_entry_check: Callable[[], None] | None = None
    ) -> AsyncExecutionResult:
        if self._degraded:
            raise RuntimeError("Bitget execution is degraded; additional dispatches are blocked")
        if intent.role == "ENTRY" and (
            intent.planned_leverage is None
            or intent.margin_mode != "ISOLATED"
            or intent.planned_margin_usdt is None
            or intent.balance_snapshot_id is None
            or intent.margin_reservation_id is None
        ):
            raise ValueError("Bitget entry lacks durable isolated-margin admission evidence")
        # The dispatcher has already validated quantity against the single fresh
        # admission snapshot. Do not perform a second account/balance preflight here:
        # it would re-open the TOCTOU window after durable reservation.
        if intent.role == "ENTRY":
            planned_leverage = intent.planned_leverage
            assert planned_leverage is not None
            await self._venue.ensure_leverage(intent.symbol, planned_leverage)
        if final_entry_check is not None:
            # No await may intervene between this winning-claim check and POST.
            final_entry_check()
        try:
            kwargs: dict[str, Any] = dict(
                symbol=intent.symbol,
                side=intent.side,
                quantity=str(intent.requested_qty),
                client_oid=intent.client_oid,
            )
            if intent.order_type == "limit":
                kwargs.update(order_type="limit", price=str(intent.limit_price))
            submitted = await self._client.place_entry_order(**kwargs)
        except (BitgetUnknownResultError, TimeoutError):
            result = await self.reconcile_intent(intent)
        else:
            result = await self.reconcile_intent(intent, submitted)
        await self._reconcile_post_fill(intent, result)
        return result

    async def cancel_pending_entry(
        self, intent: LiveIntentRecord, claim_cancel_post: Callable[[], bool]
    ) -> AsyncExecutionResult:
        """The caller owns the durable cancel fence; ambiguous POST gets GETs only."""
        before = await self.reconcile_intent(intent)
        if before.provider_terminal:
            return before
        if before.status is LiveOrderStatus.UNKNOWN:
            return before
        if not claim_cancel_post():
            return before
        with contextlib.suppress(BitgetUnknownResultError, TimeoutError, BitgetApiError):
            await self._client.cancel_entry_order(
                symbol=intent.symbol, client_oid=intent.client_oid
            )
        return await self.reconcile_intent(intent)

    async def reconcile_post_fill(
        self, intent: LiveIntentRecord, result: AsyncExecutionResult
    ) -> None:
        """Apply the same provider observation to GET-only replayed fills."""
        await self._reconcile_post_fill(intent, result)

    async def _reconcile_post_fill(
        self, intent: LiveIntentRecord, result: AsyncExecutionResult
    ) -> None:
        if (
            result.status not in {LiveOrderStatus.FILLED, LiveOrderStatus.PARTIAL}
            or intent.role != "ENTRY"
            or intent.entry_leg is not None
        ):
            return
        if intent.planned_leverage is None or intent.planned_margin_usdt is None:
            return
        try:
            position = await read_position_state(self._client, intent.symbol)
            if position is None:
                observation = PostFillReconciliation(
                    intent.exchange,
                    intent.client_oid,
                    intent.symbol,
                    intent.planned_leverage,
                    intent.planned_margin_usdt,
                    intent.planned_notional_usdt,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "unavailable",
                    "position-unavailable",
                    datetime.now(UTC),
                )
            else:
                mode = position.margin_mode.upper()
                if (
                    position.leverage != Decimal(intent.planned_leverage)
                    or mode != intent.margin_mode
                ):
                    status, reason = "mismatch", "leverage-or-margin-mode-mismatch"
                elif position.margin_usdt is None:
                    status, reason = "unavailable", "provider-margin-unavailable"
                elif intent.dispatch_id is not None and position.quantity != intent.filled_qty:
                    status, reason = "mismatch", "split-position-quantity-mismatch"
                elif (
                    intent.dispatch_id is not None
                    and position.margin_usdt <= intent.planned_margin_usdt + Decimal("0.01")
                ):
                    status, reason = "within_tolerance", "split-filled-margin-within-reserved-cap"
                elif position.margin_usdt == intent.planned_margin_usdt:
                    status, reason = "matched", None
                elif abs(position.margin_usdt - intent.planned_margin_usdt) <= Decimal("0.01"):
                    status, reason = "within_tolerance", "margin-rounding-or-fee-tolerance"
                else:
                    status, reason = "mismatch", "margin-mismatch"
                observation = PostFillReconciliation(
                    intent.exchange,
                    intent.client_oid,
                    intent.symbol,
                    intent.planned_leverage,
                    intent.planned_margin_usdt,
                    intent.planned_notional_usdt,
                    position.leverage,
                    mode,
                    position.quantity,
                    position.entry_price,
                    position.mark_price,
                    position.margin_usdt,
                    status,
                    reason,
                    datetime.now(UTC),
                )
        except Exception as exc:
            observation = PostFillReconciliation(
                intent.exchange,
                intent.client_oid,
                intent.symbol,
                intent.planned_leverage,
                intent.planned_margin_usdt,
                intent.planned_notional_usdt,
                None,
                None,
                None,
                None,
                None,
                None,
                "unavailable",
                f"provider-position-read-failed:{type(exc).__name__}",
                datetime.now(UTC),
            )
        record = getattr(self._reconciliation_repository, "record", None)
        if callable(record):
            record(observation)
        if observation.status == "mismatch":
            self._degraded = True
            latch = getattr(self._entry_admission_latch, "latch_kill_switch", None)
            if callable(latch):
                latch("bitget", "post-fill-margin-or-leverage-mismatch")

    async def reconcile_intent(
        self, intent: LiveIntentRecord, submitted: dict[str, Any] | None = None
    ) -> AsyncExecutionResult:
        detail_not_found = False
        try:
            detail = await self._client.get_order_detail(
                intent.symbol, client_oid=intent.client_oid
            )
        except BitgetApiError as exc:
            if "40109" in str(exc) or "cannot be found" in str(exc):
                detail = {}
                detail_not_found = True
            else:
                raise
        if detail_not_found:
            provider_order_id = (
                (submitted or {}).get("orderId")
                or (submitted or {}).get("providerOrderId")
                or intent.provider_order_id
            )
            outcome = await classify_missing_detail(
                intent,
                read_fills=self._client.get_fills,
                read_position=self._client.get_single_position,
                read_pending_orders=self._client.get_pending_orders,
                provider_order_id=(
                    str(provider_order_id) if provider_order_id is not None else None
                ),
                missing_order_confirmed=True,
            )
            return AsyncExecutionResult(
                client_oid=intent.client_oid,
                status=outcome.status,
                filled_qty=outcome.filled_qty,
                avg_price=outcome.avg_price,
                fee=outcome.fee,
                provider_order_id=outcome.provider_order_id,
                provider_fill_ids=outcome.provider_fill_ids,
                provider_fills=outcome.provider_fills,
            )
        fills = await self._client.get_fills(intent.symbol)
        if isinstance(fills, dict):
            fills = fills.get("fillList", [])
        if not isinstance(detail, dict):
            raise ValueError("Bitget order detail response must be an object")
        if not isinstance(fills, list) or not all(isinstance(fill, dict) for fill in fills):
            raise ValueError("Bitget fills response must be a list of objects")
        provider_order_id = detail.get("orderId") or detail.get("providerOrderId")
        if provider_order_id is None and submitted is not None:
            provider_order_id = submitted.get("orderId") or submitted.get("providerOrderId")
        if provider_order_id is None:
            provider_order_id = intent.provider_order_id
        matching_fills = _matching_fills(
            [dict(fill) for fill in fills],
            client_oid=intent.client_oid,
            provider_order_id=str(provider_order_id) if provider_order_id is not None else None,
        )
        typed_fills = [normalize_fill(fill) for fill in matching_fills]
        filled_qty, avg_price, fee, fill_ids = summarize_fills(typed_fills)
        if not detail:
            if filled_qty >= intent.requested_qty:
                status = LiveOrderStatus.FILLED
            elif filled_qty > 0:
                status = LiveOrderStatus.PARTIAL
            else:
                status = (
                    LiveOrderStatus.REJECTED
                    if detail_not_found
                    else LiveOrderStatus.ACCEPTED
                    if submitted is not None
                    else LiveOrderStatus.UNKNOWN
                )
            return AsyncExecutionResult(
                client_oid=intent.client_oid,
                status=status,
                filled_qty=filled_qty,
                avg_price=avg_price,
                fee=fee,
                provider_order_id=str(provider_order_id) if provider_order_id else None,
                provider_fill_ids=fill_ids,
                provider_fills=tuple(typed_fills),
            )
        detail_filled_qty = _detail_decimal(detail, "filledQty", "filledSize", "baseVolume")
        if detail_filled_qty is not None and detail_filled_qty > filled_qty:
            filled_qty = detail_filled_qty
            avg_price = _detail_decimal(detail, "avgPrice", "priceAvg", "averagePrice")
        status = classify_live_order(detail, typed_fills)
        terminal = str(detail.get("state", detail.get("status", ""))).lower() in {
            "filled",
            "full-fill",
            "full_fill",
            "cancelled",
            "canceled",
            "rejected",
            "expired",
        }
        if intent.order_type == "limit" and (
            str(detail.get("clientOid", "")) != intent.client_oid
            or str(detail.get("symbol", "")).upper() != intent.symbol
            or str(detail.get("side", "")).upper() != intent.side
            or str(detail.get("orderType", "")).lower() != "limit"
            or str(detail.get("reduceOnly", "")).upper() not in {"NO", "FALSE"}
            or Decimal(str(detail.get("price", "0"))) != intent.limit_price
            or Decimal(str(detail.get("size", "0"))) != intent.requested_qty
            or filled_qty > intent.requested_qty
        ):
            raise ValueError("Bitget waiting entry ownership mismatch")
        return AsyncExecutionResult(
            client_oid=intent.client_oid,
            status=status,
            filled_qty=filled_qty,
            avg_price=avg_price,
            fee=fee,
            provider_order_id=str(provider_order_id) if provider_order_id is not None else None,
            provider_fill_ids=fill_ids,
            provider_fills=tuple(typed_fills),
            provider_terminal=terminal,
        )

    async def protect_filled_position(
        self,
        intent: LiveIntentRecord,
        plan: ProtectionPlan,
        store: LiveIntentStoreProtocol,
    ) -> AsyncProtectionResult:
        """Install and read back native protection before accepting a filled position.

        Any unconfirmed result latches the adapter degraded. Emergency containment is
        an explicit, default-off opt-in: by default an unprotected fill is registered
        for local price management (or escalated) and never closed, so a confirmation
        race can wash out neither a protected nor an unprotected live position.
        """
        filled_quantity = intent.filled_qty
        if filled_quantity <= 0:
            self._degraded = True
            return AsyncProtectionResult(ProtectionState.FAILED, Decimal("0"), "no-filled-quantity")
        if plan.quantity != filled_quantity:
            self._degraded = True
            return await self._contain_or_local_management(
                intent,
                plan,
                store,
                ProtectionReport(
                    ProtectionState.FAILED, filled_quantity, "protection-quantity-mismatch"
                ),
            )
        try:
            stop_loss_client_oid = f"{intent.client_oid}-sl"
            take_profit = plan.take_profits[0] if plan.take_profits else None
            take_profit_client_oid = f"{intent.client_oid}-tp" if take_profit is not None else None
            protection_intents = tuple(
                LiveIntentRecord(
                    intent.exchange,
                    oid,
                    plan.symbol,
                    "SELL" if intent.side == "BUY" else "BUY",
                    role,
                    "requested",
                    filled_quantity,
                    planned_stop_loss=plan.stop_loss,
                    planned_take_profits=tuple(plan.take_profits),
                    protection_root_oid=intent.client_oid,
                )
                for oid, role in ((stop_loss_client_oid, "SL"), (take_profit_client_oid, "TP"))
                if oid is not None
            )
            if not store.claim_protection(protection_intents):
                # Existing durable placement claims cannot authorize a second POST.
                expectation = NativeProtectionExpectation(
                    symbol=plan.symbol,
                    hold_side="buy" if plan.direction.value == "LONG" else "sell",
                    quantity=filled_quantity,
                    stop_loss=plan.stop_loss,
                    take_profit=take_profit,
                    stop_loss_client_oid=stop_loss_client_oid,
                    take_profit_client_oid=take_profit_client_oid,
                )
                report = await confirm_native_protection(
                    lambda: self._client.get_single_position(plan.symbol),
                    lambda: self._client.get_pending_plan_orders(plan.symbol),
                    expectation=expectation,
                )
                return AsyncProtectionResult(report.state, report.observed_quantity, report.reason)
            placement = await self._client.place_position_tpsl(
                symbol=plan.symbol,
                hold_side="buy" if plan.direction.value == "LONG" else "sell",
                quantity=str(filled_quantity),
                stop_loss=str(plan.stop_loss),
                stop_loss_execute_price="0",
                take_profit=str(take_profit) if take_profit is not None else None,
                take_profit_execute_price="0" if take_profit is not None else None,
                stop_loss_client_oid=stop_loss_client_oid,
                take_profit_client_oid=take_profit_client_oid,
                omit_market_execute_prices=True,
            )
            if not isinstance(placement, list) or not all(
                isinstance(row, dict) for row in placement
            ):
                raise BitgetApiError("Bitget protection placement response is invalid")
            stop_loss_provider_order_id = _placement_plan_id(
                placement, client_oid=stop_loss_client_oid, leg="stopLoss"
            )
            take_profit_provider_order_id = (
                _placement_plan_id(placement, client_oid=take_profit_client_oid, leg="stopSurplus")
                if take_profit_client_oid is not None
                else None
            )
            if stop_loss_provider_order_id is None or (
                take_profit is not None and take_profit_provider_order_id is None
            ):
                raise BitgetApiError("Bitget protection placement IDs are incomplete")
            for record in protection_intents:
                record.provider_order_id = (
                    stop_loss_provider_order_id
                    if record.role == "SL"
                    else take_profit_provider_order_id
                )
                record.state = "acknowledged"
                store.update(record)
            expectation = NativeProtectionExpectation(
                symbol=plan.symbol,
                hold_side="buy" if plan.direction.value == "LONG" else "sell",
                quantity=filled_quantity,
                stop_loss=plan.stop_loss,
                take_profit=take_profit,
                stop_loss_client_oid=stop_loss_client_oid,
                take_profit_client_oid=take_profit_client_oid,
                stop_loss_provider_order_id=stop_loss_provider_order_id,
                take_profit_provider_order_id=take_profit_provider_order_id,
            )
            report = await confirm_native_protection(
                lambda: self._client.get_single_position(plan.symbol),
                lambda: self._client.get_pending_plan_orders(plan.symbol),
                expectation=expectation,
            )
        except Exception as exc:
            # 43011 alone is parameter validation, not symbol-capability evidence.
            # Require an explicit unsupported-symbol message before recording UNSUPPORTED.
            provider_message = str(exc).lower()
            if (
                isinstance(exc, BitgetApiError)
                and "43011" in str(exc)
                and (
                    "symbol does not support" in provider_message
                    or "symbol is not supported" in provider_message
                )
            ):
                self._persist_capability(
                    plan.symbol,
                    native_state=NativeProtectionState.UNSUPPORTED,
                    last_error="native-protection-unsupported",
                    fallback_allowed=True,
                )
                try:
                    from fatty_trader.execution.bitget_fallback_protection import register_fallback

                    if not self._fallback_protection_enabled:
                        self._persist_capability(
                            plan.symbol,
                            native_state=NativeProtectionState.UNSUPPORTED,
                            last_error="fallback-monitor-mutations-disabled",
                            fallback_allowed=False,
                        )
                        self._degraded = True
                        return AsyncProtectionResult(
                            ProtectionState.DEGRADED,
                            filled_quantity,
                            "fallback-monitor-mutations-disabled",
                        )
                    if intent.avg_price is None or intent.avg_price <= 0:
                        self._degraded = True
                        return AsyncProtectionResult(
                            ProtectionState.DEGRADED,
                            filled_quantity,
                            "fallback-entry-price-unavailable",
                        )
                    epoch, environment = await self._fallback_identity(intent, plan, store)
                    register_fallback(
                        provider_position_epoch=epoch,
                        environment=environment,
                        exchange=intent.exchange,
                        symbol=intent.symbol,
                        direction=plan.direction.value,
                        entry_price=intent.avg_price,
                        stop_loss=plan.stop_loss,
                        take_profits=list(plan.take_profits),
                        quantity=filled_quantity,
                        position_key=intent.client_oid,
                    )
                except Exception:
                    self._degraded = True
                    return AsyncProtectionResult(
                        ProtectionState.DEGRADED,
                        filled_quantity,
                        "fallback-registration-failed",
                    )
                # Canonical ownership and persistence are not proof of a healthy,
                # runtime-verified enforcer. Registration remains observe-only.
                self._degraded = True
                return AsyncProtectionResult(
                    ProtectionState.DEGRADED,
                    filled_quantity,
                    "native-protection-unsupported-fallback-registered-not-enforcing",
                )
            if isinstance(exc, BitgetApiError) and exc.code == "43011":
                # Parameter rejection requires operator escalation, not a retry,
                # fallback registration, emergency close, or global kill-switch latch.
                self._persist_capability(
                    plan.symbol,
                    native_state=NativeProtectionState.FAILED,
                    last_error="native-protection-parameter-rejected",
                    fallback_allowed=False,
                )
                self._degraded = True
                return AsyncProtectionResult(
                    ProtectionState.DEGRADED,
                    filled_quantity,
                    "native-protection-parameter-rejected",
                )
            report = ProtectionReport(
                ProtectionState.FAILED, Decimal("0"), "protection-submit-failed"
            )
        if report.state is ProtectionState.VENUE_PROTECTED:
            self._persist_capability(
                plan.symbol,
                native_state=NativeProtectionState.VERIFIED,
                last_error=None,
            )
            return AsyncProtectionResult(report.state, report.observed_quantity, report.reason)
        report, grace_attempts = await self._await_native_protection(intent, plan, store, report)
        if report.state is ProtectionState.VENUE_PROTECTED:
            self._persist_capability(
                plan.symbol,
                native_state=NativeProtectionState.VERIFIED,
                last_error=None,
            )
            return AsyncProtectionResult(
                report.state,
                report.observed_quantity,
                f"{report.reason}-after-grace:{grace_attempts}",
            )
        self._persist_capability(
            plan.symbol,
            native_state=NativeProtectionState.FAILED,
            last_error=f"{report.reason or 'native-protection-unconfirmed'}|grace:{grace_attempts}",
        )
        self._degraded = True
        return await self._contain_or_local_management(intent, plan, store, report)

    async def _contain_or_local_management(
        self,
        intent: LiveIntentRecord,
        plan: ProtectionPlan,
        store: LiveIntentStoreProtocol,
        report: ProtectionReport,
    ) -> AsyncProtectionResult:
        """Never close a filled position whose venue protection is unconfirmed.

        Emergency containment is an explicit, default-off opt-in. The default verdict
        marks the position locally managed: register it with the fallback price monitor
        so the local lane closes it at the signal stop once the live price crosses it,
        or, when that lane is unavailable or its identity proof fails, escalate without
        closing. Every non-closing outcome carries the durable ``-locally-managed``
        marker; registration failure is contained and reported, never raised.
        """
        if self._emergency_containment_enabled:
            return await self._contain(intent, store, report)
        base = report.reason or "native-protection-unconfirmed"
        if not self._fallback_protection_enabled:
            return AsyncProtectionResult(
                ProtectionState.DEGRADED,
                report.observed_quantity,
                f"{base}:fallback-monitor-mutations-disabled-locally-managed",
            )
        if intent.avg_price is None or intent.avg_price <= 0:
            return AsyncProtectionResult(
                ProtectionState.DEGRADED,
                report.observed_quantity,
                f"{base}:fallback-entry-price-unavailable-locally-managed",
            )
        try:
            from fatty_trader.execution.bitget_fallback_protection import register_fallback

            epoch, environment = await self._fallback_identity(intent, plan, store)
            register_fallback(
                provider_position_epoch=epoch,
                environment=environment,
                exchange=intent.exchange,
                symbol=intent.symbol,
                direction=plan.direction.value,
                entry_price=intent.avg_price,
                stop_loss=plan.stop_loss,
                take_profits=list(plan.take_profits),
                quantity=intent.filled_qty,
                position_key=intent.client_oid,
            )
        except Exception as exc:
            return AsyncProtectionResult(
                ProtectionState.DEGRADED,
                report.observed_quantity,
                f"{base}:fallback-registration-failed:{type(exc).__name__}-locally-managed",
            )
        return AsyncProtectionResult(
            ProtectionState.DEGRADED, report.observed_quantity, f"{base}-locally-managed"
        )

    async def _fallback_identity(
        self, intent: LiveIntentRecord, plan: ProtectionPlan, store: LiveIntentStoreProtocol
    ) -> tuple[str, str]:
        """Prove the known entry owns this exact provider position lifetime.

        A quantity/side match alone can adopt a replacement or manual position.
        Require actual provider fills and the opening fill's exact canonical cTime.
        Missing or ambiguous evidence leaves the fallback unregistered/degraded.
        """
        import re

        environment = getattr(self._client, "environment", None)
        known = store.get(intent.client_oid)
        if (
            environment not in {"DEMO", "LIVE"}
            or environment != self._environment
            or known != intent
            or intent.role != "ENTRY"
            or intent.state not in {"filled", "partially_filled"}
            or not intent.provider_order_id
            or intent.symbol != plan.symbol
            or intent.side != ("BUY" if plan.direction.value == "LONG" else "SELL")
        ):
            raise ValueError("fallback-entry-identity-unproven")
        rows = await self._client.get_single_position(intent.symbol)
        if not isinstance(rows, list):
            raise ValueError("fallback-position-unproven")
        active = [r for r in rows if Decimal(str(r.get("total", "0"))) > 0]
        if len(active) != 1:
            raise ValueError("fallback-position-ambiguous")
        position = active[0]
        epoch = position.get("cTime")
        side = "long" if plan.direction.value == "LONG" else "short"
        if (
            not isinstance(epoch, str)
            or re.fullmatch(r"[1-9][0-9]*", epoch) is None
            or position.get("symbol") != intent.symbol
            or position.get("holdSide") != side
            or Decimal(str(position["total"])) != intent.filled_qty
        ):
            raise ValueError("fallback-position-identity-unproven")
        fills = await self._client.get_fills(intent.symbol)
        if isinstance(fills, dict):
            fills = fills.get("fillList")
        if not isinstance(fills, list):
            raise ValueError("fallback-fill-evidence-unavailable")
        owned = [f for f in fills if f.get("orderId") == intent.provider_order_id]
        if not owned or any(
            f.get("symbol") != intent.symbol
            or f.get("clientOid") not in {None, intent.client_oid}
            or f.get("side") != intent.side.lower()
            or f.get("tradeSide") != "open"
            or not f.get("tradeId")
            or not isinstance(f.get("cTime"), str)
            or re.fullmatch(r"[1-9][0-9]*", f["cTime"]) is None
            or Decimal(str(f.get("baseVolume", "0"))) <= 0
            for f in owned
        ):
            raise ValueError("fallback-fill-identity-unproven")
        ids = [f["tradeId"] for f in owned]
        if (
            len(set(ids)) != len(ids)
            or min(int(f["cTime"]) for f in owned) != int(epoch)
            or sum((Decimal(str(f["baseVolume"])) for f in owned), Decimal("0"))
            != intent.filled_qty
        ):
            raise ValueError("fallback-entry-position-epoch-mismatch")
        return epoch, environment

    def _persist_capability(
        self,
        symbol: str,
        *,
        native_state: NativeProtectionState,
        last_error: str | None,
        fallback_allowed: bool | None = None,
    ) -> None:
        repository = self._capability_repository
        if repository is None:
            return
        get = getattr(repository, "get", None)
        upsert = getattr(repository, "upsert", None)
        if not callable(get) or not callable(upsert):
            return
        current: Any = get("bitget", self._environment, symbol)
        upsert(
            BitgetProtectionCapability(
                exchange="bitget",
                environment=self._environment,
                symbol=symbol,
                position_mode=current.position_mode if current is not None else "one_way_mode",
                margin_mode=current.margin_mode if current is not None else "isolated",
                native_state=native_state,
                fallback_allowed=(
                    fallback_allowed
                    if fallback_allowed is not None
                    else current.fallback_allowed
                    if current is not None
                    else False
                ),
                payload_profile=(
                    current.payload_profile if current is not None else "classic-v2-position"
                ),
                last_verified_at=(
                    datetime.now(UTC)
                    if native_state is NativeProtectionState.VERIFIED
                    else current.last_verified_at
                    if current is not None
                    else None
                ),
                last_error=last_error,
                stream_state=current.stream_state if current is not None else StreamState.DISABLED,
                last_stream_at=current.last_stream_at if current is not None else None,
            )
        )

    async def _await_native_protection(
        self,
        intent: LiveIntentRecord,
        plan: ProtectionPlan,
        store: LiveIntentStoreProtocol,
        first: ProtectionReport,
    ) -> tuple[ProtectionReport, int]:
        """Re-read (never re-place) until the venue confirms, within a bounded budget.

        The venue accepts plan orders and makes them readable a moment later; containing
        on the first readback closed two live trades (WLD and NEAR) seconds after their
        entries filled. This loop is read-only, stops early when the position is flat,
        and returns the last report plus the number of re-reads so any escalation carries
        its evidence instead of a race.
        """
        budget = self._protection_grace_seconds
        if budget <= 0 or intent.filled_qty <= 0:
            return first, 0
        records: dict[str, Any] = {}
        protection_intents = getattr(store, "protection_intents", None)
        if callable(protection_intents):
            records = {record.role: record for record in protection_intents(intent.client_oid)}
        expectation = NativeProtectionExpectation(
            symbol=plan.symbol,
            hold_side="buy" if plan.direction.value == "LONG" else "sell",
            quantity=intent.filled_qty,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profits[0] if plan.take_profits else None,
            stop_loss_client_oid=f"{intent.client_oid}-sl",
            take_profit_client_oid=f"{intent.client_oid}-tp" if plan.take_profits else None,
            stop_loss_provider_order_id=getattr(records.get("SL"), "provider_order_id", None),
            take_profit_provider_order_id=getattr(records.get("TP"), "provider_order_id", None),
        )
        deadline = time.monotonic() + budget
        attempts = 0
        report = first
        while time.monotonic() < deadline:
            await asyncio.sleep(self._protection_grace_interval_seconds + random.uniform(0, 0.3))
            attempts += 1
            try:
                positions = await self._client.get_single_position(plan.symbol)
            except Exception as exc:
                report = ProtectionReport(
                    ProtectionState.FAILED,
                    first.observed_quantity,
                    f"grace-read-error:{type(exc).__name__}",
                )
                continue
            if _open_position_quantity(positions) == 0:
                # Nothing left to protect: containment would only report the flat position.
                print(
                    json.dumps(
                        {
                            "event": "native-protection-grace-flat",
                            "symbol": plan.symbol,
                            "attempts": attempts,
                        }
                    ),
                    flush=True,
                )
                break
            try:
                report = await confirm_native_protection(
                    lambda: self._client.get_single_position(plan.symbol),
                    lambda: self._client.get_pending_plan_orders(plan.symbol),
                    expectation=expectation,
                )
            except Exception as exc:
                report = ProtectionReport(
                    ProtectionState.FAILED,
                    first.observed_quantity,
                    f"grace-read-error:{type(exc).__name__}",
                )
                continue
            if report.state is ProtectionState.VENUE_PROTECTED:
                print(
                    json.dumps(
                        {
                            "event": "native-protection-verified-late",
                            "symbol": plan.symbol,
                            "attempts": attempts,
                            "grace_seconds": budget,
                            "reason": report.reason,
                        }
                    ),
                    flush=True,
                )
                break
        return report, attempts

    async def _contain(
        self,
        entry: LiveIntentRecord,
        store: LiveIntentStoreProtocol,
        report: ProtectionReport,
    ) -> AsyncProtectionResult:
        """Persist then submit at most one emergency close, never for an observed flat account."""
        if report.reason == "position-not-open":
            return AsyncProtectionResult(report.state, report.observed_quantity, report.reason)
        try:
            positions = await self._client.get_single_position(entry.symbol)
            open_quantity = _open_position_quantity(positions)
        except Exception:
            return AsyncProtectionResult(
                report.state, report.observed_quantity, "position-read-failed"
            )
        if open_quantity is None:
            return AsyncProtectionResult(
                report.state, report.observed_quantity, "position-read-invalid"
            )
        if open_quantity <= 0:
            return AsyncProtectionResult(report.state, Decimal("0"), "position-already-flat")
        close_quantity = min(entry.filled_qty, open_quantity)
        if close_quantity <= 0:
            return AsyncProtectionResult(
                report.state, report.observed_quantity, "close-quantity-invalid"
            )
        close_intent = build_emergency_close_intent(entry, close_quantity)
        claim = getattr(store, "claim", None)
        if callable(claim):
            if not claim(close_intent):
                return AsyncProtectionResult(
                    report.state, report.observed_quantity, report.reason, close_intent.client_oid
                )
        else:
            # Keep compatibility with legacy test stores, but production stores must expose claim.
            existing = store.get(close_intent.client_oid)
            if existing is not None:
                return AsyncProtectionResult(
                    report.state, report.observed_quantity, report.reason, close_intent.client_oid
                )
            store.save(close_intent)
        try:
            submitted = await self._client.place_market_close(
                symbol=close_intent.symbol,
                side=close_intent.side,
                quantity=str(close_intent.requested_qty),
                client_oid=close_intent.client_oid,
            )
        except (BitgetUnknownResultError, TimeoutError):
            close_intent.state = "unknown"
            store.update(close_intent)
            try:
                result = await self.reconcile_intent(close_intent)
                _persist_intent_result(close_intent, result, store)
            except Exception:
                pass
        else:
            provider_order_id = submitted.get("orderId")
            close_intent.provider_order_id = (
                str(provider_order_id) if provider_order_id is not None else None
            )
            close_intent.state = "submitted"
            store.update(close_intent)
            # Market close orders fill instantly — reconcile immediately so DB reflects reality
            try:
                result = await self.reconcile_intent(close_intent, submitted)
                _persist_intent_result(close_intent, result, store)
            except Exception:
                pass  # best-effort; stale state is better than lost state
        return AsyncProtectionResult(
            report.state, report.observed_quantity, report.reason, close_intent.client_oid
        )


def _open_position_quantity(value: Any) -> Decimal | None:
    if isinstance(value, dict):
        value = value.get("data", value.get("positionList", []))
    if isinstance(value, list) and not value:
        return Decimal("0")
    if not isinstance(value, list):
        return None
    total = Decimal("0")
    found = False
    for row in value:
        if not isinstance(row, dict):
            continue
        raw = next(
            (row[field] for field in ("total", "size", "quantity") if field in row),
            None,
        )
        if raw is None or str(raw).strip() == "":
            continue
        try:
            quantity = Decimal(str(raw))
        except (ArithmeticError, TypeError, ValueError):
            continue
        if not quantity.is_finite():
            continue
        found = True
        total += abs(quantity)
    return total if found else None


def _persist_intent_result(
    intent: LiveIntentRecord,
    result: AsyncExecutionResult,
    store: LiveIntentStoreProtocol,
) -> None:
    intent.state = {
        LiveOrderStatus.ACCEPTED: "acknowledged",
        LiveOrderStatus.PARTIAL: "partially_filled",
        LiveOrderStatus.FILLED: "filled",
        LiveOrderStatus.REJECTED: "rejected",
        LiveOrderStatus.UNKNOWN: "unknown",
    }[result.status]
    intent.filled_qty = result.filled_qty
    intent.avg_price = result.avg_price
    intent.fee = result.fee
    intent.provider_order_id = result.provider_order_id or intent.provider_order_id
    intent.provider_fill_ids = result.provider_fill_ids
    intent.provider_fills = result.provider_fills
    store.update(intent)
