"""Async Bitget entry execution with intent-first, GET-only reconciliation."""

from __future__ import annotations

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
        self, *, symbol: str, side: str, quantity: str, client_oid: str
    ) -> dict[str, Any]: ...

    async def get_order_detail(self, symbol: str, *, client_oid: str) -> Any: ...

    async def get_fills(self, symbol: str) -> Any: ...

    async def get_single_position(self, symbol: str) -> Any: ...

    async def get_pending_orders(self, symbol: str) -> Any: ...

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
    ) -> list[dict[str, Any]]: ...

    async def place_market_close(
        self, *, symbol: str, side: str, quantity: str, client_oid: str
    ) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


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
        if value > 0:
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
        environment: str = "DEMO",
    ) -> None:
        self._client = client
        self._venue = venue
        self._degraded = False
        self._capability_repository = capability_repository
        self._reconciliation_repository = reconciliation_repository
        self._entry_admission_latch = entry_admission_latch
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
        try:
            submitted = await self._client.place_entry_order(
                symbol=intent.symbol,
                side=intent.side,
                quantity=str(intent.requested_qty),
                client_oid=intent.client_oid,
            )
        except (BitgetUnknownResultError, TimeoutError):
            result = await self.reconcile_intent(intent)
        else:
            result = await self.reconcile_intent(intent, submitted)
        await self._reconcile_post_fill(intent, result)
        return result

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
        if filled_qty <= 0 and detail_filled_qty is not None:
            filled_qty = detail_filled_qty
            avg_price = _detail_decimal(detail, "avgPrice", "priceAvg", "averagePrice")
        status = classify_live_order(detail, typed_fills)
        if status is LiveOrderStatus.ACCEPTED and typed_fills:
            status = LiveOrderStatus.FILLED
            filled_qty = intent.requested_qty
        return AsyncExecutionResult(
            client_oid=intent.client_oid,
            status=status,
            filled_qty=filled_qty,
            avg_price=avg_price,
            fee=fee,
            provider_order_id=str(provider_order_id) if provider_order_id is not None else None,
            provider_fill_ids=fill_ids,
            provider_fills=tuple(typed_fills),
        )

    async def protect_filled_position(
        self,
        intent: LiveIntentRecord,
        plan: ProtectionPlan,
        store: LiveIntentStoreProtocol,
    ) -> AsyncProtectionResult:
        """Install and read back native protection before accepting a filled position.

        Any unconfirmed result latches the adapter degraded. Containment is intent-first
        and deterministic, so an unknown close POST is never retried blindly.
        """
        filled_quantity = intent.filled_qty
        if filled_quantity <= 0:
            self._degraded = True
            return AsyncProtectionResult(ProtectionState.FAILED, Decimal("0"), "no-filled-quantity")
        if plan.quantity != filled_quantity:
            self._degraded = True
            return await self._contain(
                intent,
                store,
                ProtectionReport(
                    ProtectionState.FAILED, filled_quantity, "protection-quantity-mismatch"
                ),
            )
        try:
            stop_loss_client_oid = f"{intent.client_oid}-sl"
            take_profit = plan.take_profits[0] if plan.take_profits else None
            take_profit_client_oid = f"{intent.client_oid}-tp" if take_profit is not None else None
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
            # Some symbols (e.g. GRASSUSDT) don't support native SL/TP placement (43011).
            # Register for bot-managed fallback TP/SL monitoring instead of emergency-closing.
            if "43011" in str(exc):
                self._persist_capability(
                    plan.symbol,
                    native_state=NativeProtectionState.UNSUPPORTED,
                    last_error="native-protection-unsupported",
                    fallback_allowed=True,
                )
                try:
                    from fatty_trader.execution.bitget_fallback_protection import register_fallback

                    if intent.avg_price is None or intent.avg_price <= 0:
                        self._degraded = True
                        return AsyncProtectionResult(
                            ProtectionState.DEGRADED,
                            filled_quantity,
                            "fallback-entry-price-unavailable",
                        )
                    register_fallback(
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
                return AsyncProtectionResult(
                    ProtectionState.BOT_FALLBACK,
                    filled_quantity,
                    "native-protection-unsupported-fallback-registered",
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
        self._persist_capability(
            plan.symbol,
            native_state=NativeProtectionState.FAILED,
            last_error=report.reason or "native-protection-unconfirmed",
        )
        self._degraded = True
        return await self._contain(intent, store, report)

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
