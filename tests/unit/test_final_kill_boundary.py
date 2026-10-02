"""ENTRY-only veto must leave existing uncertain/fill truth GET-reconcilable."""

from decimal import Decimal

import pytest
from test_bitget_dispatch_execution_adapter import Execution, _dispatch, _result, _submission

from fatty_trader.exchanges.bitget.async_execution import AsyncProtectionResult
from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveOrderStatus
from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
from fatty_trader.execution.protection import ProtectionState


class Kill:
    def __init__(self):
        self.reads = []

    def is_active(self, scope) -> bool:
        self.reads.append(scope)
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("filled", [False, True])
async def test_kill_does_not_reject_existing_ambiguous_or_filled_intent(filled):
    store = InMemoryLiveIntentStore()
    intent = BitgetDispatchExecution._intent(_dispatch(), _submission())
    intent.state = "unknown"
    store.save(intent)
    result = _result(LiveOrderStatus.FILLED if filled else LiveOrderStatus.UNKNOWN)
    execution = Execution(
        result=result,
        protection=AsyncProtectionResult(ProtectionState.VENUE_PROTECTED, Decimal("0.002")),
    )
    kill = Kill()
    adapter = BitgetDispatchExecution(execution, store, kill_switch=kill)
    status = await adapter.submit_entry(_dispatch(), _submission())
    assert status == ("FILLED_UNPROTECTED" if filled else "UNKNOWN")
    assert execution.submit_calls == []
    assert execution.reconcile_calls == [intent.client_oid]
    assert execution.protect_calls == []
    stored = store.get(intent.client_oid)
    assert stored is not None
    assert stored.state == ("filled" if filled else "unknown")
    assert kill.reads == []


@pytest.mark.asyncio
async def test_known_unsent_kill_reads_both_scopes_and_rejects():
    store = InMemoryLiveIntentStore()
    execution = Execution(result=_result())
    kill = Kill()
    adapter = BitgetDispatchExecution(execution, store, kill_switch=kill)
    assert await adapter.submit_entry(_dispatch(), _submission()) == "REJECTED"
    assert kill.reads == ["global", "bitget"]
    assert execution.submit_calls == []
    assert execution.protect_calls == []


@pytest.mark.asyncio
async def test_kill_after_proven_fill_does_not_gate_required_protection():
    class LateKill(Kill):
        active = False

        def is_active(self, scope) -> bool:
            self.reads.append(scope)
            return self.active

    kill = LateKill()

    class FilledExecution(Execution):
        async def submit_entry(self, intent):
            result = await super().submit_entry(intent)
            kill.active = True
            return result

    execution = FilledExecution(
        result=_result(),
        protection=AsyncProtectionResult(ProtectionState.VENUE_PROTECTED, Decimal("0.002")),
    )
    adapter = BitgetDispatchExecution(execution, InMemoryLiveIntentStore(), kill_switch=kill)
    assert await adapter.submit_entry(_dispatch(), _submission()) == "FILLED"
    assert kill.active
    assert execution.protect_calls == execution.submit_calls
    assert len(execution.protect_calls) == 1
