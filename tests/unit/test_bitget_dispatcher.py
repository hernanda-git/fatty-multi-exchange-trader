from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from fatty_trader.domain.enums import Exchange, MarginMode
from fatty_trader.domain.models import InstrumentSpec, VenueRiskConfig
from fatty_trader.execution.bitget_dispatch_repository import BitgetDispatch
from fatty_trader.execution.bitget_dispatcher import (
    BitgetDispatcher,
    DispatchGate,
    EntryRoutingSnapshot,
)
from fatty_trader.execution.entry_routing import EntryMode, LimitEntryContext


class Repository:
    def __init__(self, dispatch: BitgetDispatch | None) -> None:
        self.dispatch = dispatch
        self.transitions: list[tuple[str, str, str | None]] = []
        self.alerts: list[str] = []
        self.evidence: list[dict[str, Any]] = []
        self.released_canaries: list[object] = []

    def claim(self, worker_id: str, lease_seconds: int) -> BitgetDispatch | None:
        assert worker_id == "worker"
        assert lease_seconds == 30
        return self.dispatch

    def canary_entry_count(self, exchange: str) -> int:
        assert exchange == "bitget"
        return 0

    def reserve_canary_entry(self, dispatch_id: object, exchange: str, max_orders: int) -> bool:
        assert self.dispatch is not None and dispatch_id == self.dispatch.id
        assert exchange == "bitget"
        assert max_orders > 0
        return True

    def release_canary_entry(self, dispatch_id: object, exchange: str) -> None:
        assert exchange == "bitget"
        self.released_canaries.append(dispatch_id)

    def transition(
        self,
        dispatch_id: object,
        *,
        expected_state: str,
        target_state: str,
        reason: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        assert self.dispatch is not None and dispatch_id == self.dispatch.id
        self.transitions.append((expected_state, target_state, reason))
        assert evidence is not None
        self.evidence.append(evidence)

    def alert(self, dispatch_id: object, reason: str) -> None:
        assert self.dispatch is not None and dispatch_id == self.dispatch.id
        self.alerts.append(reason)


@dataclass
class Execution:
    post_count: int = 0

    async def submit_entry(self, dispatch: BitgetDispatch, quantity: Decimal) -> str:
        self.post_count += 1
        assert dispatch.pair_token == "BTCUSDT"
        assert quantity == Decimal("0.004")
        return "FILLED"


def _dispatch(*, take_profits: tuple[Decimal, ...] = (Decimal("65000"),)) -> BitgetDispatch:
    return BitgetDispatch(
        id=uuid4(),
        state="QUEUED",
        claimed_by="worker",
        attempts=1,
        pair_token="BTCUSDT",
        direction="LONG",
        entry_price=Decimal("64000"),
        stop_loss=Decimal("63000"),
        take_profits=take_profits,
    )


def _spec() -> InstrumentSpec:
    return InstrumentSpec(
        exchange=Exchange.BITGET,
        symbol="BTCUSDT",
        qty_step=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("5"),
        max_leverage=20,
    )


def _risk() -> VenueRiskConfig:
    return VenueRiskConfig(
        exchange=Exchange.BITGET,
        base_margin_usdt=Decimal("10"),
        default_leverage=20,
        max_leverage=20,
        max_auto_margin_usdt=Decimal("20"),
        free_margin_usdt=Decimal("100"),
        free_margin_headroom_pct=Decimal("0.5"),
        max_position_notional_usdt=Decimal("1000"),
        margin_mode=MarginMode.ISOLATED,
    )


def test_pair_token_is_normalized_to_bitget_usdt_symbol() -> None:
    from fatty_trader.execution.bitget_dispatcher import _bitget_symbol

    assert _bitget_symbol("pump") == "PUMPUSDT"
    assert _bitget_symbol("SUSHIUSDT") == "SUSHIUSDT"
    assert _bitget_symbol("BONK") == "1000BONKUSDT"
    assert _bitget_symbol("BONKUSDT") == "1000BONKUSDT"


@pytest.mark.asyncio
async def test_closed_gate_blocks_invalid_dispatch_without_provider_post() -> None:
    repository = Repository(_dispatch(take_profits=()))
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=False),
        execution=execution,
        preflight=lambda _: (_spec(), _risk()),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "cutover-gated"
    assert execution.post_count == 0
    assert repository.transitions == [("QUEUED", "REJECTED", "cutover-gated")]
    assert repository.alerts == []


@pytest.mark.asyncio
async def test_stop_only_signal_is_sized_and_submitted_when_gate_is_open() -> None:
    repository = Repository(_dispatch(take_profits=()))
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=execution,
        preflight=lambda _: (_spec(), _risk()),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "filled"
    assert execution.post_count == 1
    assert ("QUEUED", "PREFLIGHT", None) in repository.transitions
    assert ("SUBMITTING", "FILLED", None) in repository.transitions


@pytest.mark.asyncio
async def test_persistent_kill_switch_blocks_before_provider_post() -> None:
    class KillSwitch:
        def is_active(self, scope: str) -> bool:
            return scope == "bitget"

    repository = Repository(_dispatch())
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=execution,
        preflight=lambda _: (_spec(), _risk()),
        kill_switch=KillSwitch(),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "kill-switch-latched"
    assert execution.post_count == 0
    assert repository.transitions == [("QUEUED", "REJECTED", "kill-switch-latched")]
    assert repository.alerts == ["kill-switch-latched"]


@pytest.mark.asyncio
async def test_dead_protection_stream_scope_blocks_entries_without_touching_venue_scope() -> None:
    """A dead protection stream halts entries on its own scope only.

    The venue-wide `bitget` scope also gates the fallback TP/SL monitor, so
    latching it on a dead WebSocket would switch off the stop-loss path for
    exactly the positions that need it.
    """
    from fatty_trader.execution.bitget_protection_watchdog import PROTECTION_STREAM_SCOPE

    class LatchedSwitch:
        def is_active(self, scope: str) -> bool:
            return scope == PROTECTION_STREAM_SCOPE

    repository = Repository(_dispatch())
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=execution,
        preflight=lambda _: (_spec(), _risk()),
        kill_switch=LatchedSwitch(),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "protection-stream-latched"
    assert execution.post_count == 0
    assert repository.transitions == [("QUEUED", "REJECTED", "protection-stream-latched")]
    assert repository.alerts == ["protection-stream-latched"]


@pytest.mark.asyncio
async def test_symbol_protection_admission_blocks_before_preflight_without_global_kill_switch() -> (
    None
):
    repository = Repository(_dispatch())
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=execution,
        preflight=lambda _: (_ for _ in ()).throw(AssertionError("must not preflight")),
        protection_admission=lambda symbol: (False, "fallback-stream-stale"),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "rejected"
    assert execution.post_count == 0
    assert repository.transitions == [("QUEUED", "REJECTED", "fallback-stream-stale")]
    assert repository.alerts == ["fallback-stream-stale"]


@pytest.mark.asyncio
async def test_valid_dispatch_persists_only_the_explicit_state_order() -> None:
    repository = Repository(_dispatch())
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=execution,
        preflight=lambda _: (_spec(), _risk()),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "filled"
    assert execution.post_count == 1
    assert repository.transitions == [
        ("QUEUED", "PREFLIGHT", None),
        ("PREFLIGHT", "SIZED", None),
        ("SIZED", "VALIDATED", None),
        ("VALIDATED", "SUBMITTING", None),
        ("SUBMITTING", "FILLED", None),
    ]


@pytest.mark.asyncio
async def test_provider_rejection_returns_rejected_dispatcher_status() -> None:
    class RejectedExecution:
        async def submit_entry(self, dispatch: BitgetDispatch, quantity: Decimal) -> str:
            return "REJECTED"

    repository = Repository(_dispatch())
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=RejectedExecution(),
        preflight=lambda _: (_spec(), _risk()),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "rejected"
    assert repository.transitions[-1] == ("SUBMITTING", "REJECTED", None)


@pytest.mark.asyncio
async def test_fallback_filled_dispatch_is_terminal_with_explicit_reason() -> None:
    class FallbackExecution:
        async def submit_entry(self, dispatch: BitgetDispatch, quantity: Decimal) -> str:
            assert dispatch.pair_token == "BTCUSDT"
            assert quantity == Decimal("0.004")
            return "FILLED_FALLBACK"

    repository = Repository(_dispatch())
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=FallbackExecution(),
        preflight=lambda _: (_spec(), _risk()),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "filled"
    assert repository.transitions[-1] == (
        "SUBMITTING",
        "FILLED",
        "fallback-protection-active",
    )


@pytest.mark.asyncio
async def test_explicit_bounded_canary_rejects_non_canary_symbol_before_preflight_or_post() -> None:
    repository = Repository(_dispatch())
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True, canary_max_orders=1, canary_symbol="ETHUSDT"),
        execution=execution,
        preflight=lambda _: (_ for _ in ()).throw(AssertionError("must not preflight")),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "rejected"
    assert execution.post_count == 0
    assert repository.transitions == [("QUEUED", "REJECTED", "canary-symbol-mismatch")]


@pytest.mark.asyncio
async def test_bounded_demo_cap_without_symbol_allows_dynamic_contract() -> None:
    repository = Repository(replace(_dispatch(), pair_token="NOT"))

    @dataclass
    class DynamicExecution:
        post_count: int = 0

        async def submit_entry(self, dispatch: BitgetDispatch, quantity: Decimal) -> str:
            self.post_count += 1
            assert dispatch.pair_token == "NOTUSDT"
            return "FILLED"

    execution = DynamicExecution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True, canary_max_orders=100, canary_symbol=None),
        execution=execution,
        preflight=lambda _: (_spec().model_copy(update={"symbol": "NOTUSDT"}), _risk()),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "filled"
    assert execution.post_count == 1


@pytest.mark.asyncio
async def test_atomic_canary_reservation_rejects_at_cap_before_provider_post() -> None:
    class CappedRepository(Repository):
        def reserve_canary_entry(self, dispatch_id: object, exchange: str, max_orders: int) -> bool:
            super().reserve_canary_entry(dispatch_id, exchange, max_orders)
            return False

    repository = CappedRepository(_dispatch())
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True, canary_max_orders=1, canary_symbol="BTCUSDT"),
        execution=execution,
        preflight=lambda _: (_spec(), _risk()),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "rejected"
    assert execution.post_count == 0
    assert repository.transitions[-1] == ("VALIDATED", "REJECTED", "canary-order-cap-reached")


@pytest.mark.asyncio
async def test_context_aware_normal_pullback_fails_closed_without_routed_execution() -> None:
    repository = Repository(_dispatch())
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=execution,
        preflight=lambda _: (_spec(), _risk()),
        entry_routing=lambda _: EntryRoutingSnapshot(
            market_price=Decimal("64000"),
            limit_context=LimitEntryContext(
                provider_acknowledged=False,
                was_working=False,
                observed_at=datetime.now(UTC),
                near_entry_mark=None,
                prior_mark=None,
            ),
        ),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "rejected"
    assert execution.post_count == 0
    assert repository.transitions[-1] == (
        "VALIDATED",
        "REJECTED",
        "entry-routing-unsupported",
    )


@pytest.mark.asyncio
async def test_context_aware_near_limit_route_is_passed_to_routed_execution() -> None:
    @dataclass
    class RoutedExecution:
        route: object | None = None

        async def submit_entry_route(
            self, dispatch: BitgetDispatch, quantity: Decimal, route: object
        ) -> str:
            self.route = route
            assert quantity == Decimal("0.004")
            return "FILLED"

    repository = Repository(_dispatch())
    execution = RoutedExecution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=execution,
        preflight=lambda _: (_spec(), _risk()),
        entry_routing=lambda _: EntryRoutingSnapshot(
            market_price=Decimal("64200"),
            limit_context=LimitEntryContext(
                provider_acknowledged=True,
                was_working=True,
                observed_at=datetime.now(UTC),
                near_entry_mark=Decimal("64000"),
                prior_mark=Decimal("64000"),
            ),
        ),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "filled"
    assert execution.route is not None
    assert execution.route.mode is EntryMode.NEAR_LIMIT_SPLIT_MARKET_LIMIT
    assert execution.route.market_quantity == Decimal("0.001")
    assert execution.route.limit_quantity == Decimal("0.003")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome,target,result",
    [
        ("FILLED_UNPROTECTED", "FILLED", "filled"),
        ("PARTIAL_UNPROTECTED", "PARTIALLY_FILLED", "partial"),
    ],
)
async def test_missing_protection_keeps_valid_fill_state_with_durable_escalation(
    outcome, target, result
):
    from fatty_trader.domain.enums import DispatchState

    class UnsafeFillExecution(Execution):
        async def submit_entry(self, dispatch, quantity):
            self.post_count += 1
            return outcome

    repository = Repository(_dispatch())
    execution = UnsafeFillExecution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=execution,
        preflight=lambda _: (_spec(), _risk()),
    )
    assert await dispatcher.run_once("worker", 30) == result
    assert repository.transitions[-1] == ("SUBMITTING", target, "missing-protection-escalated")
    assert target in set(DispatchState)
    assert execution.post_count == 1


@pytest.mark.asyncio
async def test_preflight_failure_is_classified_correlated_and_never_logs_raw_exception() -> None:
    from fatty_trader.exchanges.bitget.metadata import BitgetCatalogError

    dispatch = replace(
        _dispatch(), source_channel_id=-100123, source_message_id=16310,
        source_id=uuid4(), canonical_signal_id=uuid4(), source_revision="a" * 64,
    )
    repository = Repository(dispatch)
    execution = Execution()

    def refused(_):
        raise BitgetCatalogError(
            "Authorization: Bearer secret private account payload",
            {
                "stage": "contract_catalog", "row_count": 834,
                "requested_symbol": "BTCUSDT", "payload_sha256": "f" * 64,
                "invalid_rows": [{"index": 321, "row_type": "str"}],
                "headers": {"Authorization": "Bearer secret"},
            },
            "catalog_rows_invalid",
        )

    dispatcher = BitgetDispatcher(
        repository, gate=DispatchGate(execution_enabled=True),
        execution=execution, preflight=refused,
    )
    assert await dispatcher.run_once("worker", 30) == "rejected"
    assert execution.post_count == 0
    assert repository.transitions[-1] == ("PREFLIGHT", "REJECTED", "catalog_rows_invalid")
    evidence = repository.evidence[-1]
    assert evidence["stage"] == "contract_catalog"
    assert evidence["exception_type"] == "BitgetCatalogError"
    assert evidence["provider_effect"] == "not_submitted"
    assert evidence["source_channel_id"] == -100123
    assert evidence["source_message_id"] == 16310
    assert evidence["source_revision"] == "a" * 64
    assert evidence["canonical_signal_id"] == str(dispatch.canonical_signal_id)
    assert evidence["source_id"] == str(dispatch.source_id)
    assert evidence["dispatch_id"] == str(dispatch.id)
    assert evidence["worker_id"] == "worker" and evidence["attempt"] == 1
    assert evidence["audit_evidence"]["invalid_rows"] == [{"index": 321, "row_type": "str"}]
    assert datetime.fromisoformat(evidence["observed_at"]).utcoffset().total_seconds() == 0
    assert evidence["elapsed_ms"] >= 0
    assert "secret" not in str(evidence) and "payload" not in str(repository.alerts)


@pytest.mark.asyncio
async def test_catalog_success_observation_survives_later_preflight_refusal() -> None:
    from fatty_trader.exchanges.bitget.metadata import record_catalog_observation

    repository = Repository(_dispatch())

    def refused(_):
        record_catalog_observation(
            {"stage": "contract_catalog", "row_count": 834, "requested_symbol": "BTCUSDT"},
            "target_validated",
        )
        raise ValueError("private provider account payload must not be copied")

    dispatcher = BitgetDispatcher(
        repository, gate=DispatchGate(execution_enabled=True), preflight=refused,
    )
    assert await dispatcher.run_once("worker", 30) == "rejected"
    evidence = repository.evidence[-1]
    assert evidence["stage"] == "preflight"
    assert evidence["reason_code"] == "preflight-validation-failed"
    assert evidence["contract_catalog"]["code"] == "target_validated"
    assert evidence["contract_catalog"]["row_count"] == 834
    assert "private" not in str(evidence)


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [TimeoutError, RuntimeError])
async def test_ambiguous_submission_never_claims_no_provider_effect(exception) -> None:
    class AmbiguousExecution:
        async def submit_entry(self, dispatch, quantity):
            raise exception("secret provider headers and account payload")

    repository = Repository(_dispatch())
    dispatcher = BitgetDispatcher(
        repository, gate=DispatchGate(execution_enabled=True, canary_max_orders=1),
        execution=AmbiguousExecution(),
        preflight=lambda _: (_spec(), _risk()),
    )
    assert await dispatcher.run_once("worker", 30) == "unknown"
    assert repository.transitions[-1][1] == "UNKNOWN"
    evidence = repository.evidence[-1]
    assert evidence["stage"] == "provider_submission"
    assert evidence["provider_effect"] == "submission_may_have_occurred"
    assert evidence["exception_type"] == exception.__name__
    assert "secret" not in str(evidence)
    assert repository.released_canaries == []


@pytest.mark.asyncio
async def test_submitting_audit_failure_prevents_provider_invocation_and_success() -> None:
    class FailingRepository(Repository):
        def transition(self, dispatch_id, **kwargs):
            if kwargs["target_state"] == "SUBMITTING":
                raise RuntimeError("audit unavailable")
            super().transition(dispatch_id, **kwargs)

    repository = FailingRepository(_dispatch())
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository, gate=DispatchGate(execution_enabled=True), execution=execution,
        preflight=lambda _: (_spec(), _risk()),
    )
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await dispatcher.run_once("worker", 30)
    assert execution.post_count == 0
    assert all(target != "FILLED" for _, target, _ in repository.transitions)


@pytest.mark.asyncio
async def test_successful_route_and_sizing_snapshot_survive_through_provider_result() -> None:
    class RoutedExecution:
        async def submit_entry_route(self, dispatch, quantity, route):
            return "FILLED"

    repository = Repository(replace(_dispatch(), pair_token="BTC"))
    dispatcher = BitgetDispatcher(
        repository, gate=DispatchGate(execution_enabled=True), execution=RoutedExecution(),
        preflight=lambda _: (_spec(), _risk()),
        entry_routing=lambda _: EntryRoutingSnapshot(
            market_price=Decimal("64000"), limit_context=None,
        ),
    )
    assert await dispatcher.run_once("worker", 30) == "filled"
    sized = repository.evidence[1]
    assert sized["source_symbol"] == "BTC" and sized["symbol"] == "BTCUSDT"
    assert sized["sizing"]["quantity"] == "0.004"
    assert sized["routing"]["market_price"] == "64000"
    assert sized["provider_effect"] == "not_submitted"
    assert sized["preflight_elapsed_ms"] >= 0
    submitting, result = repository.evidence[-2:]
    assert submitting["provider_effect"] == "submission_may_have_occurred"
    assert result["provider_effect"] == "provider_response_observed"
    assert result["provider_status"] == "FILLED"
    assert result["routing"] == sized["routing"]
    assert result["sizing"] == sized["sizing"]


@pytest.mark.parametrize(
    "exception,code",
    [
        (ValueError("required margin exceeds auto margin cap"), "auto-margin-cap-exceeded"),
        (ValueError("Bitget symbol has an active position"), "active-symbol-position"),
        (ValueError("private arbitrary response"), "preflight-validation-failed"),
        (KeyError("private arbitrary response"), "contract-not-found"),
        (TimeoutError("private arbitrary response"), "preflight-timeout"),
        (RuntimeError("private arbitrary response"), "preflight-error"),
    ],
)
def test_preflight_reason_classifies_without_exposing_exception_text(exception, code):
    from fatty_trader.execution.bitget_dispatcher import _reason

    assert _reason(exception) == code


def test_exception_diagnostics_select_only_numeric_provider_code_and_http_status():
    from fatty_trader.execution.bitget_dispatcher import _exception_evidence

    class ProviderFailure(RuntimeError):
        code = "40001"
        http_status = 401
        audit_evidence = {"stage": "contract_catalog", "headers": "secret"}

    evidence = _exception_evidence(ProviderFailure("secret Authorization and account body"))
    assert evidence == {
        "exception_type": "ProviderFailure",
        "provider_code": "40001",
        "http_status": 401,
        "audit_evidence": {"stage": "contract_catalog"},
    }
    failure = ProviderFailure("secret")
    failure.code = "Bearer secret"
    failure.http_status = "private payload"
    assert "provider_code" not in _exception_evidence(failure)
    assert "http_status" not in _exception_evidence(failure)


@pytest.mark.asyncio
async def test_failed_terminal_audit_does_not_report_filled_after_provider_invocation():
    class FailingRepository(Repository):
        def transition(self, dispatch_id, **kwargs):
            if kwargs["target_state"] == "FILLED":
                raise RuntimeError("terminal audit unavailable")
            super().transition(dispatch_id, **kwargs)

    repository = FailingRepository(_dispatch())
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository, gate=DispatchGate(execution_enabled=True), execution=execution,
        preflight=lambda _: (_spec(), _risk()),
    )
    with pytest.raises(RuntimeError, match="terminal audit unavailable"):
        await dispatcher.run_once("worker", 30)
    assert execution.post_count == 1
    assert repository.transitions[-1][1] == "SUBMITTING"


@pytest.mark.parametrize(
    "message,reason",
    [
        ("Bitget margin admission rejected: symbol-already-owned", "symbol-already-owned"),
        ("Bitget margin admission rejected: stale-balance-snapshot", "stale-balance-snapshot"),
        ("Bitget margin admission rejected: private secret", "margin-admission-refused"),
        ("Bitget live sizing rejected: position-cap: 4 active", "position-cap-exceeded"),
        (
            "Bitget live sizing rejected: sl-guard: stop-loss not safely before liquidation",
            "liquidation-stop-guard-refused",
        ),
        ("Bitget live sizing rejected: private secret", "live-sizing-refused"),
    ],
)
def test_known_local_admission_refusals_keep_machine_reason_not_raw_message(message, reason):
    from fatty_trader.execution.bitget_dispatcher import _reason

    assert _reason(ValueError(message)) == reason
