"""ENTRY-only veto must leave existing uncertain/fill truth GET-reconcilable."""

from dataclasses import replace
from decimal import Decimal

import pytest
from test_bitget_dispatch_execution_adapter import Execution, _dispatch, _result, _submission

from fatty_trader.exchanges.bitget.async_execution import AsyncProtectionResult
from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveOrderStatus
from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
from fatty_trader.execution.bitget_protection_watchdog import PROTECTION_STREAM_SCOPE
from fatty_trader.execution.entry_routing import EntryMode, EntryRoute
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
async def test_known_unsent_kill_reads_all_entry_scopes_and_rejects():
    store = InMemoryLiveIntentStore()
    execution = Execution(result=_result())
    kill = Kill()
    adapter = BitgetDispatchExecution(execution, store, kill_switch=kill)
    assert await adapter.submit_entry(_dispatch(), _submission()) == "REJECTED"
    assert kill.reads == ["global", "bitget", PROTECTION_STREAM_SCOPE]
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


@pytest.mark.asyncio
@pytest.mark.parametrize("split,leg", [(False, "market"), (True, "market"), (True, "limit")])
async def test_stream_latch_after_provider_setup_blocks_each_entry_leg(split, leg):
    class StreamKill(Kill):
        active = False

        def is_active(self, scope):
            self.reads.append(scope)
            return scope == PROTECTION_STREAM_SCOPE and self.active

    kill = StreamKill()

    class GuardedExecution(Execution):
        async def submit_entry_guarded(self, intent, final_entry_check):
            # Represents awaited leverage setup finishing after admission/protection.
            if (intent.entry_leg or "market") == leg:
                kill.active = True
            final_entry_check()
            return await self.submit_entry(intent)

    store = InMemoryLiveIntentStore()
    submission = _submission()
    if split:
        submission = replace(
            submission,
            quantity=Decimal("0.008"),
            entry_route=EntryRoute(
                EntryMode.SPLIT_MARKET_LIMIT,
                Decimal("0.002"),
                Decimal("0.006"),
                Decimal("64000"),
            ),
        )
    execution = GuardedExecution(
        result=_result(),
        protection=AsyncProtectionResult(ProtectionState.VENUE_PROTECTED, Decimal("0.002")),
    )
    adapter = BitgetDispatchExecution(execution, store, kill_switch=kill)
    assert await adapter.submit_entry(_dispatch(), submission) == (
        "PARTIAL" if leg == "limit" else "REJECTED"
    )
    root_oid = adapter.client_oid(_dispatch())
    assert execution.submit_calls == ([root_oid] if leg == "limit" else [])
    assert execution.protect_calls == execution.submit_calls
    root = store.get(root_oid)
    assert root is not None and root.state == ("filled" if leg == "limit" else "rejected")
    assert root.provider_terminal
    if split:
        limit = store.get(root.client_oid + "-limit")
        assert (
            limit is not None
            and limit.state == ("rejected" if leg == "limit" else "cancelled")
            and limit.provider_terminal
        )
