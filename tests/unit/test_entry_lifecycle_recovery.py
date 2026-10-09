from dataclasses import replace
from decimal import Decimal

import pytest
from test_bitget_dispatch_execution_adapter import Execution, _dispatch, _result, _submission

from fatty_trader.exchanges.bitget.async_execution import AsyncProtectionResult
from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore
from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
from fatty_trader.execution.protection import ProtectionState


class Repository:
    def __init__(self, dispatch, oid):
        self.dispatch = dispatch
        self.oid = oid
        self.events = []

    def recovery_candidates(self):
        return [(self.dispatch, self.oid)]

    def transition(self, dispatch_id, **kwargs):
        assert kwargs["expected_state"] == self.dispatch.state
        self.events.append(kwargs)
        self.dispatch = replace(self.dispatch, state=kwargs["target_state"])


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_state", ["SUBMITTING", "ACKNOWLEDGED", "FILLED"])
@pytest.mark.parametrize("protected", [False, True])
async def test_restart_get_only_restores_expectation_or_durable_escalation(crash_state, protected):
    dispatch = replace(_dispatch(), state=crash_state)
    intent = BitgetDispatchExecution._intent(dispatch, _submission())
    store = InMemoryLiveIntentStore()
    store.save(intent)
    execution = Execution(result=_result())
    repo = Repository(dispatch, intent.client_oid)
    plans = []

    async def read_protection(entry, plan):
        plans.append(plan)
        assert entry.filled_qty == Decimal("0.002")
        return AsyncProtectionResult(
            ProtectionState.VENUE_PROTECTED if protected else ProtectionState.DEGRADED,
            Decimal("0.002"),
            "missing-native-protection",
        )

    adapter = BitgetDispatchExecution(
        execution, store, dispatch_repository=repo, recovery_protection=read_protection
    )
    assert await adapter.recover_entry_lifecycles() == 1
    assert execution.submit_calls == execution.protect_calls == []
    assert plans[0].stop_loss == dispatch.stop_loss
    assert plans[0].take_profits == dispatch.take_profits
    assert repo.events[-1]["target_state"] == "FILLED"
    assert repo.events[-1]["reason"] == (
        "recovery-protection-verified"
        if protected
        else "recovery-missing-protection:missing-native-protection"
    )
    assert store.get(intent.client_oid).state == "filled"


@pytest.mark.asyncio
async def test_proven_fill_is_not_unknown_when_protection_missing():
    execution = Execution(
        result=_result(),
        protection=AsyncProtectionResult(
            ProtectionState.DEGRADED, Decimal("0"), "missing-native-protection"
        ),
    )
    adapter = BitgetDispatchExecution(execution, InMemoryLiveIntentStore())
    assert await adapter.submit_entry(_dispatch(), _submission()) == "FILLED_UNPROTECTED"


@pytest.mark.asyncio
async def test_protection_exception_does_not_erase_confirmed_entry_fill():
    class BrokenProtection(Execution):
        async def protect_filled_position(self, intent, plan, store):
            raise RuntimeError("provider protection unavailable")

    store = InMemoryLiveIntentStore()
    execution = BrokenProtection(result=_result())
    adapter = BitgetDispatchExecution(execution, store)
    assert await adapter.submit_entry(_dispatch(), _submission()) == "FILLED_UNPROTECTED"
    assert store.get(adapter.client_oid(_dispatch())).state == "filled"


@pytest.mark.asyncio
async def test_recovery_refuses_provider_quantity_regression_without_erasing_evidence():
    from fatty_trader.exchanges.bitget.live import LiveOrderStatus

    dispatch = replace(_dispatch(), state="FILLED")
    intent = BitgetDispatchExecution._intent(dispatch, _submission())
    intent.state = "filled"
    intent.filled_qty = Decimal("0.002")
    intent.provider_order_id = "provider-order-1"
    intent.provider_fill_ids = ("fill-1",)
    store = InMemoryLiveIntentStore()
    store.save(intent)
    result = replace(
        _result(LiveOrderStatus.UNKNOWN),
        filled_qty=Decimal("0"),
        provider_order_id=None,
        provider_fill_ids=(),
        provider_fills=(),
    )
    repo = Repository(dispatch, intent.client_oid)
    adapter = BitgetDispatchExecution(Execution(result=result), store, dispatch_repository=repo)
    with pytest.raises(ValueError, match="quantity regression"):
        await adapter.recover_entry_lifecycles()
    assert adapter.recovery_ready is False
    assert intent.state == "filled"
    assert intent.filled_qty == Decimal("0.002")
    assert intent.provider_fill_ids == ("fill-1",)
    assert repo.events == []


@pytest.mark.asyncio
async def test_partial_fill_with_registered_fallback_is_not_promoted_to_full_fill():
    from fatty_trader.exchanges.bitget.live import LiveOrderStatus

    execution = Execution(
        result=_result(LiveOrderStatus.PARTIAL),
        protection=AsyncProtectionResult(
            ProtectionState.DEGRADED,
            Decimal("0.002"),
            "native-protection-unsupported-fallback-registered",
        ),
    )
    adapter = BitgetDispatchExecution(execution, InMemoryLiveIntentStore())
    assert await adapter.submit_entry(_dispatch(), _submission()) == "PARTIAL_UNPROTECTED"


@pytest.mark.asyncio
async def test_historical_missing_validated_plan_stays_unresolved_without_reader_guess():
    dispatch = replace(_dispatch(), state="FILLED")
    intent = BitgetDispatchExecution._intent(dispatch, _submission())
    intent.planned_stop_loss = None
    intent.planned_take_profits = None
    store = InMemoryLiveIntentStore()
    store.save(intent)
    execution = Execution(result=_result())
    repo = Repository(dispatch, intent.client_oid)

    async def reader(entry, plan):
        raise AssertionError("historical source prices are not a validated execution plan")

    adapter = BitgetDispatchExecution(
        execution, store, dispatch_repository=repo, recovery_protection=reader
    )
    assert await adapter.recover_entry_lifecycles() == 1
    assert adapter.recovery_ready is False
    assert (
        repo.events[-1]["reason"] == "recovery-missing-protection:recovery-validated-plan-missing"
    )
    assert store.get(intent.client_oid).state == "filled"
    assert execution.submit_calls == execution.protect_calls == []
