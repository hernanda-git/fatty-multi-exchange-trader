"""TDD coverage for async Bitget native protection and containment (fakes only)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fatty_trader.domain.enums import Direction, Exchange
from fatty_trader.exchanges.bitget.async_execution import (
    AsyncBitgetExecution,
    _open_position_quantity,
)
from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue
from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveIntentRecord
from fatty_trader.execution.protection import ProtectionPlan, ProtectionState
from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository


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
                {
                    "symbol": "BTCUSDT",
                    "holdSide": "buy",
                    "planType": "pos_loss",
                    "orderId": "native-plan",
                    "stopLossClientOid": "live-bitget-BTCUSDT-0011223344556677-sl",
                    "triggerPrice": "49000",
                    "executePrice": "0",
                    "triggerType": "mark_price",
                    "planStatus": "live",
                    "size": "",
                },
                {
                    "symbol": "BTCUSDT",
                    "holdSide": "buy",
                    "planType": "pos_profit",
                    "orderId": "native-plan",
                    "stopSurplusClientOid": "live-bitget-BTCUSDT-0011223344556677-tp",
                    "triggerPrice": "51000",
                    "executePrice": "0",
                    "triggerType": "mark_price",
                    "planStatus": "live",
                    "size": "",
                },
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
        return [
            {
                "symbol": symbol,
                "holdSide": "buy",
                "total": self.position_qty,
                "marginMode": self.margin_mode,
                "posMode": "one_way_mode",
                "stopLossId": "native-plan",
                "takeProfitId": "native-plan",
            }
        ]

    async def get_pending_plan_orders(self, symbol: str) -> list[dict[str, str]]:
        if self.read_fails:
            raise TimeoutError("provider read unavailable")
        return list(self.plans)

    async def place_position_tpsl(self, **kwargs: str) -> list[dict[str, str]]:
        self.protection_calls.append(kwargs)
        for plan in self.plans:
            if plan.get("planType") == "pos_loss":
                plan["stopLossClientOid"] = kwargs["stop_loss_client_oid"]
                plan["triggerPrice"] = kwargs["stop_loss"]
            elif plan.get("planType") == "pos_profit":
                plan["stopSurplusClientOid"] = kwargs["take_profit_client_oid"]
                plan["triggerPrice"] = kwargs["take_profit"]
        return [
            {
                "orderId": "native-plan",
                "stopLossClientOid": kwargs["stop_loss_client_oid"],
                "stopSurplusClientOid": kwargs["take_profit_client_oid"],
            }
        ]

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


def _plan_stop_only() -> ProtectionPlan:
    return ProtectionPlan(
        exchange=Exchange.BITGET,
        symbol="BTCUSDT",
        direction=Direction.LONG,
        quantity=Decimal("0.008"),
        stop_loss=Decimal("49000"),
    )


def test_live_intent_store_claims_a_close_identity_only_once() -> None:
    store = InMemoryLiveIntentStore()
    close = LiveIntentRecord(
        exchange="bitget",
        client_oid="live-bitget-BTCUSDT-0011223344556677-emergency",
        symbol="BTCUSDT",
        side="SELL",
        role="EMERGENCY_CLOSE",
        requested_qty=Decimal("0.008"),
    )

    assert store.claim(close) is True
    assert store.claim(close) is False
    assert store.get(close.client_oid) is not None


def test_open_position_quantity_rejects_malformed_provider_rows() -> None:
    assert _open_position_quantity({"data": [{"total": "0.008"}]}) == Decimal("0.008")
    assert _open_position_quantity({"data": []}) == Decimal("0")
    assert _open_position_quantity([{"symbol": "BTCUSDT"}]) is None


@pytest.mark.asyncio
async def test_full_or_partial_fill_protection_uses_confirmed_filled_quantity() -> None:
    client = NativeProtectionClient(position_qty="0.008")
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))

    result = await adapter.protect_filled_position(_intent(), _plan(), InMemoryLiveIntentStore())

    assert result.state is ProtectionState.VENUE_PROTECTED
    assert client.protection_calls[0]["quantity"] == "0.008"
    assert client.protection_calls[0]["hold_side"] == "buy"
    assert client.protection_calls[0]["stop_loss_execute_price"] == "0"
    assert client.protection_calls[0]["take_profit_execute_price"] == "0"
    assert client.close_calls == []


@pytest.mark.asyncio
async def test_stop_only_protection_does_not_require_take_profit() -> None:
    client = NativeProtectionClient(
        position_qty="0.008",
        plans=[
            {
                "planType": "pos_loss",
                "symbol": "BTCUSDT",
                "holdSide": "buy",
                "planStatus": "active",
                "orderId": "native-plan",
                "stopLossTriggerPrice": "49000",
                "stopLossTriggerType": "mark_price",
                "stopLossExecutePrice": "0",
            }
        ],
    )
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))

    result = await adapter.protect_filled_position(
        _intent(), _plan_stop_only(), InMemoryLiveIntentStore()
    )

    assert result.state is ProtectionState.VENUE_PROTECTED
    assert client.protection_calls[0]["take_profit"] is None
    assert client.protection_calls[0]["take_profit_client_oid"] is None


@pytest.mark.asyncio
async def test_verified_native_readback_persists_symbol_capability() -> None:
    client = NativeProtectionClient(position_qty="0.008")
    capabilities = InMemoryProtectionCapabilityRepository()
    adapter = AsyncBitgetExecution(
        client,
        AsyncBitgetVenue(client),
        capability_repository=capabilities,
        environment="LIVE",
    )

    result = await adapter.protect_filled_position(_intent(), _plan(), InMemoryLiveIntentStore())

    assert result.state is ProtectionState.VENUE_PROTECTED
    capability = capabilities.get("bitget", "LIVE", "BTCUSDT")
    assert capability is not None
    assert capability.native_state.value == "VERIFIED"


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
async def test_containment_uses_atomic_claim_before_emergency_close() -> None:
    class ClaimOnlyStore(InMemoryLiveIntentStore):
        def save(self, record: LiveIntentRecord) -> None:
            raise AssertionError("containment must use atomic claim")

    client = NativeProtectionClient(plans=[])
    store = ClaimOnlyStore()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))

    result = await adapter.protect_filled_position(_intent(), _plan(), store)

    assert result.emergency_close_oid is not None
    assert len(client.close_calls) == 1


@pytest.mark.asyncio
async def test_pre_cutover_flat_account_never_calls_emergency_close() -> None:
    client = NativeProtectionClient(position_qty="0", plans=[])
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))

    result = await adapter.protect_filled_position(_intent(), _plan(), InMemoryLiveIntentStore())

    assert result.state is ProtectionState.FAILED
    assert result.emergency_close_oid is None
    assert client.close_calls == []
