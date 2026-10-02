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


# Exercise production execution -> real REST client -> serialized HTTP body.
def _rest_protection_client(
    intent,
    *,
    take_profit=True,
    provider_error=None,
    error_position_flat=True,
):
    import json

    import httpx

    from fatty_trader.exchanges.bitget.client import BitgetRestClient

    seen = []
    plans = []
    for leg, plan_type, trigger, suffix in (
        ("stopLoss", "pos_loss", "49000", "sl"),
        ("stopSurplus", "pos_profit", "51000", "tp"),
    ):
        if suffix == "tp" and not take_profit:
            continue
        plans.append(
            {
                "symbol": intent.symbol,
                "holdSide": "buy",
                "planType": plan_type,
                "orderId": f"plan-{suffix}",
                f"{leg}ClientOid": f"{intent.client_oid}-{suffix}",
                "triggerPrice": trigger,
                "triggerType": "mark_price",
                "orderType": "market",
                "planStatus": "live",
                "size": "",
            }
        )

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/place-pos-tpsl"):
            json.loads(request.content)  # Require a real serialized request.
            if provider_error:
                return httpx.Response(400, json={"code": "43011", "msg": provider_error})
            data = plans
        elif request.url.path.endswith("/single-position"):
            # Failure containment observes flat, so this test cannot place a close.
            data = (
                []
                if provider_error and error_position_flat
                else [
                    {
                        "symbol": intent.symbol,
                        "holdSide": "buy",
                        "total": str(intent.filled_qty),
                        "marginMode": "isolated",
                        "posMode": "one_way_mode",
                        "stopLossId": "plan-sl",
                        "takeProfitId": "plan-tp" if take_profit else "",
                    }
                ]
            )
        elif request.url.path.endswith("/orders-plan-pending"):
            data = {"entrustedList": plans}
        else:
            raise AssertionError(f"Unexpected HTTP request: {request.method} {request.url}")
        return httpx.Response(200, json={"code": "00000", "data": data})

    client = BitgetRestClient(
        "offline-key",
        "offline-secret",
        "offline-passphrase",
        mode="LIVE",
        transport=httpx.MockTransport(handler),
    )
    return client, seen


@pytest.mark.asyncio
@pytest.mark.parametrize("take_profit", [False, True])
@pytest.mark.parametrize("filled_qty", ["0.01", "0.008"])
async def test_production_native_market_protection_serializes_omission(take_profit, filled_qty):
    import json
    from dataclasses import replace

    intent = _intent()
    intent.filled_qty = Decimal(filled_qty)
    plan = replace(_plan() if take_profit else _plan_stop_only(), quantity=intent.filled_qty)
    client, seen = _rest_protection_client(intent, take_profit=take_profit)
    capabilities = InMemoryProtectionCapabilityRepository()
    adapter = AsyncBitgetExecution(
        client, AsyncBitgetVenue(client), capability_repository=capabilities, environment="LIVE"
    )
    try:
        result = await adapter.protect_filled_position(intent, plan, InMemoryLiveIntentStore())
    finally:
        await client.aclose()
    posts = [request for request in seen if request.method == "POST"]
    assert len(posts) == 1
    payload = json.loads(posts[0].content)
    expected = {
        "symbol": "BTCUSDT",
        "productType": "USDT-FUTURES",
        "marginCoin": "USDT",
        "holdSide": "buy",
        "stopLossTriggerPrice": "49000",
        "stopLossTriggerType": "mark_price",
        "stopLossClientOid": f"{intent.client_oid}-sl",
    }
    if take_profit:
        expected.update(
            stopSurplusTriggerPrice="51000",
            stopSurplusTriggerType="mark_price",
            stopSurplusClientOid=f"{intent.client_oid}-tp",
        )
    assert payload == expected  # No execute prices, size, delegateType, or phantom TP.
    assert result.state is ProtectionState.VENUE_PROTECTED
    assert result.observed_quantity == intent.filled_qty
    assert adapter.degraded is False
    capability = capabilities.get("bitget", "LIVE", intent.symbol)
    assert capability.native_state.value == "VERIFIED"
    assert capability.last_verified_at is not None
    assert capability.last_error is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_error",
    [
        "presetSLExcutePrice must than 0",
        "delegateType is error",
        "The parameter does not meet the specification",
    ],
)
async def test_native_parameter_rejection_is_failed_not_symbol_unsupported(provider_error):
    intent = _intent()
    client, seen = _rest_protection_client(intent, provider_error=provider_error)
    capabilities = InMemoryProtectionCapabilityRepository()
    adapter = AsyncBitgetExecution(
        client, AsyncBitgetVenue(client), capability_repository=capabilities, environment="LIVE"
    )
    try:
        result = await adapter.protect_filled_position(intent, _plan(), InMemoryLiveIntentStore())
    finally:
        await client.aclose()
    # 43011 is parameter validation, not evidence of a symbol capability.
    assert len([request for request in seen if request.method == "POST"]) == 1
    capability = capabilities.get("bitget", "LIVE", intent.symbol)
    assert capability is not None
    assert capability.native_state.value == "FAILED"
    assert capability.fallback_allowed is False
    assert capability.last_error == "native-protection-parameter-rejected"
    assert result.state is ProtectionState.DEGRADED
    assert result.reason == "native-protection-parameter-rejected"
    assert adapter.degraded is True


@pytest.mark.asyncio
async def test_native_parameter_error_degrades_open_position_without_emergency_close():
    intent = _intent()
    client, seen = _rest_protection_client(
        intent,
        provider_error="presetSLExcutePrice must than 0",
        error_position_flat=False,
    )
    capabilities = InMemoryProtectionCapabilityRepository()
    store = InMemoryLiveIntentStore()
    adapter = AsyncBitgetExecution(
        client, AsyncBitgetVenue(client), capability_repository=capabilities, environment="LIVE"
    )
    try:
        result = await adapter.protect_filled_position(intent, _plan(), store)
    finally:
        await client.aclose()
    assert result.state is ProtectionState.DEGRADED
    assert result.reason == "native-protection-parameter-rejected"
    assert result.emergency_close_oid is None
    assert store.get(f"{intent.client_oid}-emergency") is None
    assert len([request for request in seen if request.method == "POST"]) == 1
    capability = capabilities.get("bitget", "LIVE", intent.symbol)
    assert capability is not None
    assert capability.native_state.value == "FAILED"
    assert capability.last_error == "native-protection-parameter-rejected"
    assert adapter.degraded is True
    # Protection failure cannot rewrite the separately proven entry fill.
    assert intent.state == "filled"
    assert intent.filled_qty == Decimal("0.008")
