"""Fail-closed Bitget dispatch orchestration over durable claimed work."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from inspect import isawaitable
from typing import Any, Protocol, cast
from uuid import UUID

from fatty_trader.domain.enums import Direction
from fatty_trader.domain.models import CanonicalSignal, InstrumentSpec, VenueRiskConfig
from fatty_trader.execution.bitget_admission import BitgetEntrySubmission
from fatty_trader.execution.bitget_dispatch_repository import BitgetDispatch
from fatty_trader.execution.entry_routing import EntryRoute, LimitEntryContext, route_entry
from fatty_trader.risk.sizing import minimum_safe_plan


class DispatchRepository(Protocol):
    def claim(self, worker_id: str, lease_seconds: int) -> BitgetDispatch | None: ...
    def transition(
        self,
        dispatch_id: UUID,
        *,
        expected_state: str,
        target_state: str,
        reason: str | None = None,
    ) -> None: ...

    def alert(self, dispatch_id: UUID, reason: str) -> None: ...
    def reserve_canary_entry(self, dispatch_id: UUID, exchange: str, max_orders: int) -> bool: ...
    def release_canary_entry(self, dispatch_id: UUID, exchange: str) -> None: ...


class KillSwitch(Protocol):
    def is_active(self, scope: str) -> bool: ...


class EntryExecution(Protocol):
    async def submit_entry(
        self, dispatch: BitgetDispatch, submission: BitgetEntrySubmission
    ) -> str: ...


class RoutedEntryExecution(Protocol):
    async def submit_entry_route(
        self, dispatch: BitgetDispatch, quantity: Decimal, route: EntryRoute
    ) -> str: ...


@dataclass(frozen=True)
class DispatchGate:
    """Per-venue gate. Defaults closed and is separate from global DEMO mode."""

    execution_enabled: bool = False
    canary_max_orders: int = 0
    canary_symbol: str | None = None


@dataclass(frozen=True)
class BitgetAdmission:
    """The only production handoff from fresh balance admission to execution."""

    submission: BitgetEntrySubmission


Preflight = (
    Callable[[BitgetDispatch], BitgetAdmission | tuple[InstrumentSpec, VenueRiskConfig]]
    | Callable[
        [BitgetDispatch], Awaitable[BitgetAdmission | tuple[InstrumentSpec, VenueRiskConfig]]
    ]
)
ProtectionAdmission = (
    Callable[[str], tuple[bool, str]] | Callable[[str], Awaitable[tuple[bool, str]]]
)
EntryRoutingContext = (
    Callable[[str], "EntryRoutingSnapshot"] | Callable[[str], Awaitable["EntryRoutingSnapshot"]]
)


@dataclass(frozen=True)
class EntryRoutingSnapshot:
    """Read-only provider evidence used for one context-aware route decision."""

    market_price: Decimal
    limit_context: LimitEntryContext | None
    late_threshold_pct: Decimal = Decimal("0.005")
    near_limit_threshold_pct: Decimal | None = None


class BitgetDispatcher:
    """Claim, validate, and submit only after an explicit per-venue cutover."""

    def __init__(
        self,
        repository: DispatchRepository,
        *,
        gate: DispatchGate | None = None,
        execution: EntryExecution | None = None,
        preflight: Preflight,
        kill_switch: KillSwitch | None = None,
        protection_admission: ProtectionAdmission | None = None,
        entry_routing: EntryRoutingContext | None = None,
    ) -> None:
        self._repository = repository
        self._kill_switch = kill_switch
        self._gate = gate or DispatchGate()
        self._execution = execution
        self._preflight = preflight
        self._protection_admission = protection_admission
        self._entry_routing = entry_routing

    async def run_once(self, worker_id: str, lease_seconds: int) -> str:
        dispatch = self._repository.claim(worker_id, lease_seconds)
        if dispatch is None:
            return "idle"
        dispatch = replace(dispatch, pair_token=_bitget_symbol(dispatch.pair_token))
        if self._kill_switch is not None and self._kill_switch.is_active("bitget"):
            self._reject(dispatch, "kill-switch-latched")
            return "kill-switch-latched"
        if not self._gate.execution_enabled:
            self._reject(dispatch, "cutover-gated")
            return "cutover-gated"
        if (
            self._gate.canary_max_orders > 0
            and self._gate.canary_symbol is not None
            and dispatch.pair_token != self._gate.canary_symbol
        ):
            self._reject(dispatch, "canary-symbol-mismatch")
            return "rejected"
        if self._protection_admission is not None:
            try:
                admission = self._protection_admission(dispatch.pair_token)
                if isawaitable(admission):
                    admission = await admission
                allowed, reason = admission
                if not isinstance(allowed, bool) or not isinstance(reason, str):
                    raise ValueError("protection admission response is invalid")
            except Exception as exc:
                self._reject(dispatch, f"protection-admission-error:{type(exc).__name__}")
                return "rejected"
            if not allowed:
                self._reject(dispatch, reason.strip() or "protection-admission-denied")
                return "rejected"
        try:
            CanonicalSignal(
                source_message_id=1,
                source_revision="0" * 64,
                pair_token=dispatch.pair_token,
                direction=Direction(dispatch.direction),
                entry_price=dispatch.entry_price,
                stop_loss=dispatch.stop_loss,
                take_profits=dispatch.take_profits,
            )
        except ValueError as exc:
            self._reject(dispatch, _reason(exc))
            return "rejected"
        self._transition(dispatch, "QUEUED", "PREFLIGHT")
        try:
            preflight = await _resolve_preflight(self._preflight, dispatch)
            if isinstance(preflight, BitgetAdmission):
                submission = preflight.submission
                plan_quantity = submission.quantity
            else:
                spec, risk = preflight
                plan = minimum_safe_plan(
                    spec=spec, config=risk, reference_price=dispatch.entry_price
                )
                submission = None
                plan_quantity = plan.quantity
        except Exception as exc:
            self._reject_from(dispatch, "PREFLIGHT", _reason(exc))
            return "rejected"
        route: EntryRoute | None = None
        if self._entry_routing is not None:
            try:
                snapshot = self._entry_routing(dispatch.pair_token)
                if isawaitable(snapshot):
                    snapshot = await snapshot
                if not isinstance(snapshot, EntryRoutingSnapshot):
                    raise ValueError("entry routing snapshot is invalid")
                route = route_entry(
                    direction=Direction(dispatch.direction),
                    signal_entry=dispatch.entry_price,
                    market_price=snapshot.market_price,
                    total_quantity=plan_quantity,
                    late_threshold_pct=snapshot.late_threshold_pct,
                    near_limit_threshold_pct=snapshot.near_limit_threshold_pct,
                    limit_context=snapshot.limit_context,
                )
            except Exception as exc:
                self._release_admission(submission)
                self._reject_from(
                    dispatch, "PREFLIGHT", f"entry-routing-error:{type(exc).__name__}"
                )
                return "rejected"
        self._transition(dispatch, "PREFLIGHT", "SIZED")
        self._transition(dispatch, "SIZED", "VALIDATED")
        if self._execution is None:
            self._release_admission(submission)
            self._reject_from(dispatch, "VALIDATED", "missing-execution-client")
            return "rejected"
        if route is not None and not callable(getattr(self._execution, "submit_entry_route", None)):
            self._release_admission(submission)
            self._reject_from(dispatch, "VALIDATED", "entry-routing-unsupported")
            return "rejected"
        if self._gate.canary_max_orders > 0 and not self._repository.reserve_canary_entry(
            dispatch.id, "bitget", self._gate.canary_max_orders
        ):
            self._release_admission(submission)
            self._reject_from(dispatch, "VALIDATED", "canary-order-cap-reached")
            return "rejected"
        self._transition(dispatch, "VALIDATED", "SUBMITTING")
        try:
            if route is None:
                if submission is None:
                    # Compatibility only: the production service always returns BitgetAdmission.
                    status = await cast(Any, self._execution).submit_entry(dispatch, plan_quantity)
                else:
                    status = await self._execution.submit_entry(dispatch, submission)
            else:
                routed_submit = cast(RoutedEntryExecution, self._execution).submit_entry_route
                status = await routed_submit(dispatch, plan_quantity, route)
        except TimeoutError:
            self._transition(dispatch, "SUBMITTING", "UNKNOWN", "provider-unknown")
            return "unknown"
        except Exception as exc:
            reason = f"provider-readback-error:{type(exc).__name__}"
            self._transition(dispatch, "SUBMITTING", "UNKNOWN", reason)
            self._repository.alert(dispatch.id, reason)
            self._repository.release_canary_entry(dispatch.id, "bitget")
            return "unknown"
        target = {
            "ACKNOWLEDGED": "ACKNOWLEDGED",
            "FILLED": "FILLED",
            "FILLED_FALLBACK": "FILLED",
            "PARTIAL": "PARTIALLY_FILLED",
            "REJECTED": "REJECTED",
        }.get(status)
        if target is None:
            self._transition(dispatch, "SUBMITTING", "UNKNOWN", "provider-unknown")
            return "unknown"
        self._transition(
            dispatch,
            "SUBMITTING",
            target,
            "fallback-protection-active" if status == "FILLED_FALLBACK" else None,
        )
        return {
            "ACKNOWLEDGED": "acknowledged",
            "FILLED": "filled",
            "PARTIALLY_FILLED": "partial",
            "REJECTED": "rejected",
        }[target]

    def _release_admission(self, submission: BitgetEntrySubmission | None) -> None:
        """Return margin only for a local rejection proven to precede provider POST."""
        if submission is None or self._execution is None:
            return
        release = getattr(self._execution, "release_reservation", None)
        if not callable(release):
            return
        try:
            release(submission)
        except Exception as exc:
            # A release failure must not make a rejected pre-POST admission spendable.
            self._repository.alert(
                submission.margin_reservation_id, f"margin-release-error:{type(exc).__name__}"
            )

    def _transition(
        self, dispatch: BitgetDispatch, expected: str, target: str, reason: str | None = None
    ) -> None:
        self._repository.transition(
            dispatch.id, expected_state=expected, target_state=target, reason=reason
        )

    def _reject(self, dispatch: BitgetDispatch, reason: str) -> None:
        self._repository.transition(
            dispatch.id, expected_state="QUEUED", target_state="REJECTED", reason=reason
        )
        if reason != "cutover-gated":
            self._repository.alert(dispatch.id, reason)

    def _reject_from(self, dispatch: BitgetDispatch, current: str, reason: str) -> None:
        self._repository.transition(
            dispatch.id, expected_state=current, target_state="REJECTED", reason=reason
        )
        self._repository.alert(dispatch.id, reason)


async def _resolve_preflight(
    preflight: Preflight, dispatch: BitgetDispatch
) -> BitgetAdmission | tuple[InstrumentSpec, VenueRiskConfig]:
    result = preflight(dispatch)
    if isawaitable(result):
        return await result
    return result


_BITGET_SYMBOL_ALIASES = {
    # Bitget lists BONK perpetuals under the 1000BONK contract symbol.
    "BONK": "1000BONKUSDT",
    "BONKUSDT": "1000BONKUSDT",
}


def _bitget_symbol(pair_token: str) -> str:
    symbol = pair_token.upper().strip()
    if not symbol:
        raise ValueError("dispatch symbol is required")
    return _BITGET_SYMBOL_ALIASES.get(
        symbol, symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    )


def _reason(exc: Exception) -> str:
    message = str(exc)
    if "take_profits" in message:
        return "missing-take-profits"
    return message or "invalid-dispatch"
