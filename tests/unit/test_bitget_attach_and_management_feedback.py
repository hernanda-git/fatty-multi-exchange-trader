"""Attach reliability and manual-instruction feedback (fakes only; no network).

A filled position must end up carrying its venue stop, and a manual management
instruction must end with a visible outcome: the operator has to be able to see
that the venue closed the position, moved the stop, or refused the request.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from fatty_trader.domain.enums import Direction, Exchange
from fatty_trader.exchanges.bitget.async_execution import AsyncBitgetExecution
from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue
from fatty_trader.exchanges.bitget.client import BitgetApiError
from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveIntentRecord
from fatty_trader.execution.protection import ProtectionPlan, ProtectionState
from tests.unit.test_bitget_native_protection import NativeProtectionClient


def _intent() -> LiveIntentRecord:
    return LiveIntentRecord(
        exchange="bitget",
        client_oid="live-bitget-BTCUSDT-0011223344556677",
        symbol="BTCUSDT",
        side="BUY",
        role="ENTRY",
        state="filled",
        requested_qty=Decimal("0.008"),
        filled_qty=Decimal("0.008"),
        avg_price=Decimal("50000"),
        margin_reservation_id=None,
        dispatch_id=None,
        entry_leg=None,
    )


def _plan() -> ProtectionPlan:
    return ProtectionPlan(
        exchange=Exchange.BITGET,
        symbol="BTCUSDT",
        direction=Direction.LONG,
        quantity=Decimal("0.008"),
        stop_loss=Decimal("49000"),
        take_profits=(Decimal("51000"),),
    )


@pytest.mark.asyncio
async def test_a_transient_placement_failure_is_retried_inside_one_claim() -> None:
    store = InMemoryLiveIntentStore()
    client = NativeProtectionClient(position_qty="0.008")
    original = client.place_position_tpsl
    calls: list[int] = []

    async def flaky_place(**kwargs: str) -> list[dict[str, str]]:
        calls.append(1)
        if len(calls) == 1:
            raise BitgetApiError("Bitget protection placement response is invalid")
        return await original(**kwargs)

    client.place_position_tpsl = flaky_place  # type: ignore[method-assign]
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))

    result = await adapter.protect_filled_position(_intent(), _plan(), store)

    assert result.state is ProtectionState.VENUE_PROTECTED
    assert len(calls) == 2
    # One durable claim, one placement: the retry never duplicates the legs.
    assert len(store.protection_intents(_intent().client_oid)) == 2


@pytest.mark.asyncio
async def test_a_parameter_rejection_is_never_retried() -> None:
    store = InMemoryLiveIntentStore()
    client = NativeProtectionClient(position_qty="0.008")
    calls: list[int] = []

    async def rejecting_place(**kwargs: str) -> list[dict[str, str]]:
        calls.append(1)
        raise BitgetApiError("Bitget rejected the parameters 43011")

    client.place_position_tpsl = rejecting_place  # type: ignore[method-assign]
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))

    result = await adapter.protect_filled_position(_intent(), _plan(), store)

    assert result.state is ProtectionState.DEGRADED
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_permanently_failing_placement_never_leaves_a_naked_fill_claimed() -> None:
    store = InMemoryLiveIntentStore()
    client = NativeProtectionClient(position_qty="0.008")

    async def failing_place(**kwargs: str) -> list[dict[str, str]]:
        raise BitgetApiError("Bitget rejected the parameters 43011")

    client.place_position_tpsl = failing_place  # type: ignore[method-assign]
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))

    result = await adapter.protect_filled_position(_intent(), _plan(), store)

    assert result.state is ProtectionState.DEGRADED
    # The durable protection claim survives, so the intent is auditable and no
    # close is invented for a position the venue still holds.
    assert len(store.protection_intents(_intent().client_oid)) == 2


def _management_update() -> Any:
    from fatty_trader.analyzer.trade_management import ManagementAction
    from fatty_trader.execution.source_management import SourceManagementUpdate

    return SourceManagementUpdate.new("rev-1", "WLDUSDT", ManagementAction.CLOSE)


@pytest.mark.asyncio
async def test_a_manual_close_reports_its_outcome_to_the_operator() -> None:
    from fatty_trader.execution.source_management import (
        InMemorySourceManagementStore,
        SourceManagementExecutor,
    )

    class Gateway:
        def __init__(self) -> None:
            self.closed: list[str] = []
            self.open = True

        def get_positions(self, symbol: str) -> list[dict[str, Any]]:
            if not self.open:
                return []
            return [
                {
                    "symbol": symbol,
                    "side": "SHORT",
                    "size": Decimal("10"),
                    "entry": Decimal("0.5"),
                }
            ]

        def get_orders(self, symbol: str) -> list[dict[str, Any]]:
            return []

        def cancel_pending_entries(self, *args: Any, **kwargs: Any) -> bool:
            return True

        def close_reduce_only(self, **kwargs: Any) -> None:
            self.closed.append(kwargs["client_oid"])
            self.open = False

        def normalize_tp1_close_quantity(self, symbol: str, quantity: Decimal) -> Decimal:
            return quantity

        def reconcile_stop_loss(self, client_oid: str) -> None:
            return None

        def replace_stop_loss(self, **kwargs: Any) -> None:
            return None

    store = InMemorySourceManagementStore([_management_update()])
    gateway = Gateway()
    executor = SourceManagementExecutor(store, gateway, mutations_enabled=True)

    outcome = executor.run_once("worker-1")

    assert outcome == "reconciled"
    assert gateway.closed
    # The operator sees the result of the instruction they sent.
    assert store.outcomes == [("WLDUSDT", "reconciled")]
