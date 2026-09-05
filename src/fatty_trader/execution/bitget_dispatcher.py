"""Fail-closed Bitget dispatch orchestration over durable claimed work."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from inspect import isawaitable
from typing import Protocol
from uuid import UUID

from fatty_trader.domain.enums import Direction
from fatty_trader.domain.models import CanonicalSignal, InstrumentSpec, VenueRiskConfig
from fatty_trader.execution.bitget_dispatch_repository import BitgetDispatch
from fatty_trader.risk.sizing import SizingError, minimum_safe_plan


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


class KillSwitch(Protocol):
    def is_active(self, scope: str) -> bool: ...


class EntryExecution(Protocol):
    async def submit_entry(self, dispatch: BitgetDispatch, quantity: Decimal) -> str: ...


@dataclass(frozen=True)
class DispatchGate:
    """Per-venue gate. Defaults closed and is separate from global PAPER mode."""

    execution_enabled: bool = False


Preflight = (
    Callable[[str], tuple[InstrumentSpec, VenueRiskConfig]]
    | Callable[[str], Awaitable[tuple[InstrumentSpec, VenueRiskConfig]]]
)


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
    ) -> None:
        self._repository = repository
        self._kill_switch = kill_switch
        self._gate = gate or DispatchGate()
        self._execution = execution
        self._preflight = preflight

    async def run_once(self, worker_id: str, lease_seconds: int) -> str:
        dispatch = self._repository.claim(worker_id, lease_seconds)
        if dispatch is None:
            return "idle"
        if self._kill_switch is not None and self._kill_switch.is_active("bitget"):
            self._reject(dispatch, "kill-switch-latched")
            return "kill-switch-latched"
        if not self._gate.execution_enabled:
            self._reject(dispatch, "cutover-gated")
            return "cutover-gated"
        try:
            signal = CanonicalSignal(
                source_message_id=1,
                source_revision="0" * 64,
                pair_token=dispatch.pair_token,
                direction=Direction(dispatch.direction),
                entry_price=dispatch.entry_price,
                stop_loss=dispatch.stop_loss,
                take_profits=dispatch.take_profits,
            )
            if not signal.take_profits:
                raise ValueError("missing-take-profits")
        except ValueError as exc:
            self._reject(dispatch, _reason(exc))
            return "rejected"
        if not signal.take_profits:
            self._reject(dispatch, "missing-take-profits")
            return "rejected"
        self._transition(dispatch, "QUEUED", "PREFLIGHT")
        try:
            spec, risk = await _resolve_preflight(self._preflight, dispatch.pair_token)
            plan = minimum_safe_plan(spec=spec, config=risk, reference_price=dispatch.entry_price)
        except (SizingError, ValueError) as exc:
            self._reject_from(dispatch, "PREFLIGHT", _reason(exc))
            return "rejected"
        self._transition(dispatch, "PREFLIGHT", "SIZED")
        self._transition(dispatch, "SIZED", "VALIDATED")
        if self._execution is None:
            self._reject_from(dispatch, "VALIDATED", "missing-execution-client")
            return "rejected"
        self._transition(dispatch, "VALIDATED", "SUBMITTING")
        try:
            status = await self._execution.submit_entry(dispatch, plan.quantity)
        except TimeoutError:
            self._transition(dispatch, "SUBMITTING", "UNKNOWN", "provider-unknown")
            return "unknown"
        target = {
            "ACKNOWLEDGED": "ACKNOWLEDGED",
            "FILLED": "FILLED",
            "PARTIAL": "PARTIALLY_FILLED",
            "REJECTED": "REJECTED",
        }.get(status)
        if target is None:
            self._transition(dispatch, "SUBMITTING", "UNKNOWN", "provider-unknown")
            return "unknown"
        self._transition(dispatch, "SUBMITTING", target)
        return target.lower().replace("partially_", "")

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
        self._repository.alert(dispatch.id, reason)

    def _reject_from(self, dispatch: BitgetDispatch, current: str, reason: str) -> None:
        self._repository.transition(
            dispatch.id, expected_state=current, target_state="REJECTED", reason=reason
        )
        self._repository.alert(dispatch.id, reason)


async def _resolve_preflight(
    preflight: Preflight, symbol: str
) -> tuple[InstrumentSpec, VenueRiskConfig]:
    result = preflight(symbol)
    if isawaitable(result):
        return await result
    return result


def _reason(exc: ValueError) -> str:
    message = str(exc)
    if "take_profits" in message:
        return "missing-take-profits"
    return message or "invalid-dispatch"
