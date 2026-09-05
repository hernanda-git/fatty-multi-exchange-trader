"""TDD coverage for async Bitget native protection and containment (fakes only)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fatty_trader.domain.enums import Direction, Exchange
from fatty_trader.exchanges.bitget.async_execution import AsyncBitgetExecution
from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue
from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveIntentRecord
from fatty_trader.execution.protection import ProtectionPlan, ProtectionState


class NativeProtectionClient:
    def __init__(
        self,
        *,
        position_qty: str = "0.008",
        margin_mode: str = "isolated",
        plans: list[dict[str, str]] | None = None,
        read_fails: bool = False,
    ) -> None:
        self.position_qty = position_qty
        self.margin_mode = margin_mode
        self.plans = (
            plans
            if plans is not None
            else [
                {"planType": "loss_plan", "size": position_qty},
                {"planType": "profit_plan", "size": position_qty},
            ]
        )
        self.read_fails = read_fails
        self.protection_calls: list[dict[str, str]] = []
        self.close_calls: list[dict[str, str]] = []

    async def get_account(self, symbol: str) -> dict[str, str]:
        return {
            "available": "100",
            "marginMode": "isolated",
            "posMode": "one_way_mode",
            "isolatedLongLever": "20",
            "isolatedShortLever": "20",
        }

    async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
        if self.read_fails:
            raise TimeoutError("provider read unavailable")
        return [{"symbol": symbol, "total": self.position_qty, "marginMode": self.margin_mode}]

    async def get_pending_plan_orders(self, symbol: str) -> list[dict[str, str]]:
        if self.read_fails:
            raise TimeoutError("provider read unavailable")
        return list(self.plans)

    async def place_position_tpsl(self, **kwargs: str) -> dict[str, str]:
        self.protection_calls.append(kwargs)
        return {"orderId": "native-plan"}

    async def place_market_close(self, **kwargs: str) -> dict[str, str]:
        self.close_calls.append(kwargs)
        return {"orderId": "emergency-close"}

    async def place_entry_order(self, **kwargs: str) -> dict[str, str]:
        raise AssertionError("entry submission is not part of this test")

    async def get_order_detail(self, symbol: str, *, client_oid: str) -> dict[str, str]:
        raise AssertionError("entry reconciliation is not part of this test")

    async def get_fills(self, symbol: str) -> list[dict[str, str]]:
        raise AssertionError("entry reconciliation is not part of this test")

    async def get_contracts(self) -> list[dict[str, str]]:
        return []

    async def get_ticker(self, symbol: str) -> dict[str, str]:
        return {"lastPr": "50000"}

    async def get_clock_skew_ms(self) -> int:
        return 0

    async def aclose(self) -> None:
        pass


def _intent() -> LiveIntentRecord:
    return LiveIntentRecord(
        exchange="bitget",
        client_oid="live-bitget-BTCUSDT-0011223344556677",
        symbol="BTCUSDT",
        side="BUY",
        requested_qty=Decimal("0.01"),
        filled_qty=Decimal("0.008"),
        state="filled",
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
async def test_full_or_partial_fill_protection_uses_confirmed_filled_quantity() -> None:
    client = NativeProtectionClient(position_qty="0.008")
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))

    result = await adapter.protect_filled_position(_intent(), _plan(), InMemoryLiveIntentStore())

    assert result.state is ProtectionState.VENUE_PROTECTED
    assert client.protection_calls[0]["quantity"] == "0.008"
    assert client.close_calls == []


@pytest.mark.asyncio
async def test_plan_id_needs_readback_of_sl_tp_and_quantity() -> None:
    client = NativeProtectionClient(plans=[{"planType": "loss_plan", "size": "0.008"}])
    store = InMemoryLiveIntentStore()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))

    result = await adapter.protect_filled_position(_intent(), _plan(), store)

    assert result.state is ProtectionState.DEGRADED
    assert len(client.close_calls) == 1
    assert adapter.degraded is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("margin_mode", "plans", "read_fails"),
    [
        ("crossed", None, False),
        ("isolated", [{"planType": "profit_plan", "size": "0.008"}], False),
        ("isolated", None, True),
    ],
)
async def test_unconfirmed_protection_degrades_and_blocks_additional_dispatches(
    margin_mode: str, plans: list[dict[str, str]] | None, read_fails: bool
) -> None:
    client = NativeProtectionClient(margin_mode=margin_mode, plans=plans, read_fails=read_fails)
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))

    result = await adapter.protect_filled_position(_intent(), _plan(), InMemoryLiveIntentStore())

    assert result.state in (ProtectionState.DEGRADED, ProtectionState.FAILED)
    assert adapter.degraded is True
    with pytest.raises(RuntimeError, match="degraded"):
        await adapter.submit_entry(_intent())


@pytest.mark.asyncio
async def test_containment_persists_one_deterministic_close_without_retry() -> None:
    client = NativeProtectionClient(plans=[])
    store = InMemoryLiveIntentStore()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))

    first = await adapter.protect_filled_position(_intent(), _plan(), store)
    second = await adapter.protect_filled_position(_intent(), _plan(), store)

    assert first.emergency_close_oid == second.emergency_close_oid
    assert first.emergency_close_oid == "live-bitget-BTCUSDT-0011223344556677-emergency"
    assert len(client.close_calls) == 1
    close = store.get(first.emergency_close_oid)
    assert close is not None
    assert close.role == "EMERGENCY_CLOSE"
    assert close.requested_qty == Decimal("0.008")


@pytest.mark.asyncio
async def test_pre_cutover_flat_account_never_calls_emergency_close() -> None:
    client = NativeProtectionClient(position_qty="0", plans=[])
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))

    result = await adapter.protect_filled_position(_intent(), _plan(), InMemoryLiveIntentStore())

    assert result.state is ProtectionState.FAILED
    assert result.emergency_close_oid is None
    assert client.close_calls == []
