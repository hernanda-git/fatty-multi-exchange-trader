"""Fail-closed Bitget dispatch orchestration over durable claimed work."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from inspect import isawaitable
from time import monotonic
from typing import Any, Protocol, cast
from uuid import UUID

from fatty_trader.domain.enums import Direction
from fatty_trader.domain.models import CanonicalSignal, InstrumentSpec, VenueRiskConfig
from fatty_trader.execution.bitget_admission import BitgetEntrySubmission
from fatty_trader.execution.bitget_dispatch_repository import (
    BitgetDispatch,
    dispatch_evidence,
    sanitize_audit_evidence,
)
from fatty_trader.execution.bitget_protection_watchdog import PROTECTION_STREAM_SCOPE
from fatty_trader.execution.entry_routing import EntryRoute, LimitEntryContext, route_entry
from fatty_trader.intake.freshness import SourceFreshnessExpired
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
        evidence: dict[str, Any] | None = None,
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
        audit = dispatch_evidence(dispatch)
        audit["source_symbol"] = dispatch.pair_token
        stage = "gates"
        stage_started = monotonic()
        provider_effect = "not_submitted"

        def transition(expected: str, target: str, reason: str | None = None) -> None:
            assert dispatch is not None
            self._repository.transition(
                dispatch.id,
                expected_state=expected,
                target_state=target,
                reason=reason,
                evidence={
                    **audit,
                    "stage": stage,
                    "outcome": target.lower(),
                    "reason_code": reason,
                    "observed_at": datetime.now(UTC).isoformat(),
                    "elapsed_ms": round((monotonic() - stage_started) * 1000, 3),
                    "provider_effect": provider_effect,
                    "provider_effect_scope": "entry_submission",
                },
            )

        def reject(current: str, reason: str, exc: Exception | None = None) -> None:
            nonlocal stage
            assert dispatch is not None
            if exc is not None:
                diagnostics = sanitize_audit_evidence(getattr(exc, "audit_evidence", {}))
                audit.update(_exception_evidence(exc))
                if isinstance(diagnostics.get("stage"), str):
                    stage = diagnostics["stage"]
            transition(current, "REJECTED", reason)
            if reason != "cutover-gated":
                self._repository.alert(dispatch.id, reason)

        try:
            dispatch = replace(dispatch, pair_token=_bitget_symbol(dispatch.pair_token))
            audit["symbol"] = dispatch.pair_token
        except ValueError as exc:
            stage = "canonical_validation"
            reject("QUEUED", "invalid-dispatch-symbol", exc)
            return "rejected"
        if self._kill_switch is not None and self._kill_switch.is_active("bitget"):
            reject("QUEUED", "kill-switch-latched")
            return "kill-switch-latched"
        # Keep stream admission separate from the venue fallback-protection scope.
        if self._kill_switch is not None and self._kill_switch.is_active(PROTECTION_STREAM_SCOPE):
            reject("QUEUED", "protection-stream-latched")
            return "protection-stream-latched"
        if not self._gate.execution_enabled:
            reject("QUEUED", "cutover-gated")
            return "cutover-gated"
        if (
            self._gate.canary_max_orders > 0
            and self._gate.canary_symbol is not None
            and dispatch.pair_token != self._gate.canary_symbol
        ):
            reject("QUEUED", "canary-symbol-mismatch")
            return "rejected"
        if self._protection_admission is not None:
            stage = "protection_admission"
            stage_started = monotonic()
            try:
                admission = self._protection_admission(dispatch.pair_token)
                if isawaitable(admission):
                    admission = await admission
                allowed, reason = admission
                if not isinstance(allowed, bool) or not isinstance(reason, str):
                    raise ValueError("protection admission response is invalid")
            except Exception as exc:
                reject("QUEUED", f"protection-admission-error:{type(exc).__name__}", exc)
                return "rejected"
            if not allowed:
                reject("QUEUED", _safe_reason_code(reason, "protection-admission-denied"))
                return "rejected"
        stage = "canonical_validation"
        stage_started = monotonic()
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
            reject("QUEUED", "invalid-canonical-signal", exc)
            return "rejected"
        transition("QUEUED", "PREFLIGHT")
        stage = "preflight"
        stage_started = monotonic()
        catalog_observation: dict[str, Any] = {}
        try:
            from fatty_trader.exchanges.bitget.metadata import catalog_audit_context

            with catalog_audit_context(catalog_observation):
                preflight = await _resolve_preflight(self._preflight, dispatch)
            if isinstance(preflight, BitgetAdmission):
                submission = preflight.submission
                plan_quantity = submission.quantity
                audit["sizing"] = {
                    "quantity": str(submission.quantity),
                    "effective_leverage": submission.effective_leverage,
                    "planned_margin_usdt": str(submission.planned_margin_usdt),
                    "planned_notional_usdt": str(submission.planned_notional_usdt),
                    "margin_mode": submission.margin_mode,
                    "balance_snapshot_id": str(submission.balance_snapshot_id),
                    "margin_reservation_id": str(submission.margin_reservation_id),
                    "observed_at": submission.observed_at.astimezone(UTC).isoformat(),
                    "planned_stop_loss": str(submission.planned_stop_loss),
                    "planned_take_profits": [str(p) for p in submission.planned_take_profits],
                }
                if submission.entry_route is not None:
                    audit["routing"] = _route_evidence(submission.entry_route)
            else:
                spec, risk = preflight
                plan = minimum_safe_plan(
                    spec=spec, config=risk, reference_price=dispatch.entry_price
                )
                submission = None
                plan_quantity = plan.quantity
                audit["sizing"] = {
                    "quantity": str(plan_quantity),
                    "effective_leverage": plan.effective_leverage,
                    "planned_margin_usdt": str(plan.effective_margin_usdt),
                    "planned_notional_usdt": str(plan.notional_usdt),
                    "required_min_notional_usdt": str(plan.required_min_notional_usdt),
                    "qty_step": str(spec.qty_step),
                    "min_qty": str(spec.min_qty),
                    "min_notional": str(spec.min_notional),
                }
        except SourceFreshnessExpired:
            # The source guard commits its own EXPIRED transition and evidence.
            return "expired"
        except Exception as exc:
            audit["contract_catalog"] = sanitize_audit_evidence(
                catalog_observation.get("contract_catalog", {})
            )
            reject("PREFLIGHT", _reason(exc), exc)
            return "rejected"
        audit["contract_catalog"] = sanitize_audit_evidence(
            catalog_observation.get("contract_catalog", {})
        )
        audit["preflight_elapsed_ms"] = round((monotonic() - stage_started) * 1000, 3)
        route: EntryRoute | None = None
        if self._entry_routing is not None:
            stage = "entry_routing"
            stage_started = monotonic()
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
                audit["routing"] = {
                    **_route_evidence(route),
                    "market_price": str(snapshot.market_price),
                    "signal_entry": str(dispatch.entry_price),
                    "late_threshold_pct": str(snapshot.late_threshold_pct),
                    "near_limit_threshold_pct": (
                        str(snapshot.near_limit_threshold_pct)
                        if snapshot.near_limit_threshold_pct is not None
                        else None
                    ),
                }
            except Exception as exc:
                self._release_admission(dispatch, submission)
                reject("PREFLIGHT", f"entry-routing-error:{type(exc).__name__}", exc)
                return "rejected"
        transition("PREFLIGHT", "SIZED")
        stage = "execution_admission"
        stage_started = monotonic()
        transition("SIZED", "VALIDATED")
        if self._execution is None:
            self._release_admission(dispatch, submission)
            reject("VALIDATED", "missing-execution-client")
            return "rejected"
        if route is not None and not callable(getattr(self._execution, "submit_entry_route", None)):
            self._release_admission(dispatch, submission)
            reject("VALIDATED", "entry-routing-unsupported")
            return "rejected"
        if self._gate.canary_max_orders > 0 and not self._repository.reserve_canary_entry(
            dispatch.id, "bitget", self._gate.canary_max_orders
        ):
            self._release_admission(dispatch, submission)
            reject("VALIDATED", "canary-order-cap-reached")
            return "rejected"
        stage = "provider_submission"
        stage_started = monotonic()
        # Persist the boundary before invoking code that may POST. It is not
        # proof a POST occurred; subsequent failures cannot prove none occurred.
        provider_effect = "submission_may_have_occurred"
        transition("VALIDATED", "SUBMITTING")
        try:
            if route is None:
                if submission is None:
                    status = await cast(Any, self._execution).submit_entry(dispatch, plan_quantity)
                else:
                    status = await self._execution.submit_entry(dispatch, submission)
            else:
                routed_submit = cast(RoutedEntryExecution, self._execution).submit_entry_route
                status = await routed_submit(dispatch, plan_quantity, route)
        except TimeoutError as exc:
            audit.update(_exception_evidence(exc))
            transition("SUBMITTING", "UNKNOWN", "provider-unknown")
            return "unknown"
        except Exception as exc:
            audit.update(_exception_evidence(exc))
            reason = f"provider-readback-error:{type(exc).__name__}"
            transition("SUBMITTING", "UNKNOWN", reason)
            self._repository.alert(dispatch.id, reason)
            return "unknown"
        if status == "EXPIRED":
            return "expired"
        target = {
            "ACKNOWLEDGED": "ACKNOWLEDGED",
            "FILLED": "FILLED",
            "FILLED_FALLBACK": "FILLED",
            "FILLED_UNPROTECTED": "FILLED",
            "FILLED_LOCALLY_MANAGED": "FILLED",
            "PARTIAL_UNPROTECTED": "PARTIALLY_FILLED",
            "PARTIAL_LOCALLY_MANAGED": "PARTIALLY_FILLED",
            "PARTIAL": "PARTIALLY_FILLED",
            "REJECTED": "REJECTED",
        }.get(status)
        if target is None:
            audit["provider_status"] = "unrecognized"
            transition("SUBMITTING", "UNKNOWN", "provider-unknown")
            return "unknown"
        provider_effect = "provider_response_observed"
        audit["provider_status"] = status
        transition(
            "SUBMITTING",
            target,
            (
                "missing-protection-escalated"
                if status in {"FILLED_UNPROTECTED", "PARTIAL_UNPROTECTED"}
                else "locally-managed-protection"
                if status in {"FILLED_LOCALLY_MANAGED", "PARTIAL_LOCALLY_MANAGED"}
                else "fallback-protection-active"
                if status == "FILLED_FALLBACK"
                else None
            ),
        )
        return {
            "ACKNOWLEDGED": "acknowledged",
            "FILLED": "filled",
            "PARTIALLY_FILLED": "partial",
            "REJECTED": "rejected",
        }[target]

    def _release_admission(
        self, dispatch: BitgetDispatch, submission: BitgetEntrySubmission | None
    ) -> None:
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
            self._repository.alert(dispatch.id, f"margin-release-error:{type(exc).__name__}")


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


def _safe_reason_code(value: str, fallback: str) -> str:
    if 0 < len(value) <= 96 and all(c.isalnum() or c in "-_:" for c in value):
        return value
    return fallback


def _reason(exc: Exception) -> str:
    diagnostics = sanitize_audit_evidence(getattr(exc, "audit_evidence", {}))
    code = diagnostics.get("code")
    if isinstance(code, str):
        return _safe_reason_code(code, "preflight-error")
    # Match only known local invariant messages. Never copy arbitrary provider
    # exception text into persisted reason, notifications, or logs.
    known = {
        "required margin exceeds auto margin cap": "auto-margin-cap-exceeded",
        "required margin exceeds safe free-margin headroom": "free-margin-headroom-exceeded",
        "required notional exceeds position cap": "position-notional-cap-exceeded",
        "rounded quantity exceeds position cap": "rounded-position-cap-exceeded",
        "Bitget account margin mode must be isolated": "margin-mode-not-isolated",
        "Bitget account has unsupported position mode": "unsupported-position-mode",
        "Bitget isolated long/short leverage must match": "isolated-leverage-mismatch",
        "Bitget symbol has an active position": "active-symbol-position",
        "Bitget clock skew exceeds safety limit": "clock-skew-exceeded",
        "Bitget position-lever response must be a non-empty list": "mmr-tiers-unavailable",
        "Bitget client cannot read position-lever MMR tiers": "mmr-reader-unavailable",
        "Bitget ticker response must be an object": "market-price-response-invalid",
        "Bitget ticker response has invalid lastPr": "market-price-invalid",
        "Bitget ticker price must be finite and positive": "market-price-invalid",
    }
    if exc.args and isinstance(exc.args[0], str) and exc.args[0] in known:
        return known[exc.args[0]]
    if exc.args and isinstance(exc.args[0], str):
        message = exc.args[0]
        sizing_prefix = "Bitget live sizing rejected: "
        if message.startswith(sizing_prefix):
            detail = message[len(sizing_prefix) :]
            for prefix, reason in (
                ("position-cap:", "position-cap-exceeded"),
                ("sl-guard:", "liquidation-stop-guard-refused"),
                ("min-notional", "minimum-notional-unmeetable"),
                ("no live leverage", "live-leverage-unavailable"),
                ("no stop-loss", "stop-loss-unavailable"),
                ("split-", "split-sizing-refused"),
            ):
                if detail.startswith(prefix):
                    return reason
            return "live-sizing-refused"
        admission_prefix = "Bitget margin admission rejected: "
        if message.startswith(admission_prefix):
            detail = message[len(admission_prefix) :]
            allowed = {
                "unsupported-exchange",
                "invalid-balance-snapshot",
                "invalid-balance-headroom",
                "invalid-margin-cap",
                "margin-cap-exceeded",
                "invalid-reservation-ttl",
                "missing-position-admission-evidence",
                "invalid-snapshot-age-policy",
                "stale-balance-snapshot",
                "future-balance-snapshot",
                "legacy-unowned-reservation",
                "symbol-already-owned",
                "position-cap-exceeded",
                "insufficient-reserved-headroom",
            }
            return detail if detail in allowed else "margin-admission-refused"
    if isinstance(exc, KeyError):
        return "contract-not-found"
    if isinstance(exc, ValueError):
        return "preflight-validation-failed"
    if isinstance(exc, TimeoutError):
        return "preflight-timeout"
    return "preflight-error"


def _route_evidence(route: EntryRoute) -> dict[str, Any]:
    return {
        "mode": str(route.mode),
        "market_quantity": str(route.market_quantity),
        "limit_quantity": str(route.limit_quantity),
        "limit_price": str(route.limit_price) if route.limit_price is not None else None,
        "reason": route.reason,
    }


def _exception_evidence(exc: Exception) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "exception_type": type(exc).__name__,
        "audit_evidence": sanitize_audit_evidence(getattr(exc, "audit_evidence", {})),
    }
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.isascii() and code.isdecimal() and len(code) <= 16:
        evidence["provider_code"] = code
    status = getattr(exc, "http_status", None)
    if type(status) is int and 100 <= status <= 599:
        evidence["http_status"] = status
    return evidence
