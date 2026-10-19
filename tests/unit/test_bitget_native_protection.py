"""TDD coverage for async Bitget native protection and containment (fakes only)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

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


class PositionWithoutPlanIdsClient(NativeProtectionClient):
    """Live-shaped provider: live plans exist but the position row omits their IDs."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault(
            "plans",
            [
                {
                    "symbol": "BTCUSDT",
                    "posSide": "net",
                    "side": "sell",
                    "posMode": "one_way_mode",
                    "marginMode": "crossed",
                    "planType": "pos_loss",
                    "orderId": "native-plan-sl",
                    "clientOid": "live-bitget-BTCUSDT-0011223344556677-sl",
                    "triggerPrice": "49000",
                    "triggerType": "mark_price",
                    "planStatus": "live",
                    "size": "0",
                },
                {
                    "symbol": "BTCUSDT",
                    "posSide": "net",
                    "side": "sell",
                    "posMode": "one_way_mode",
                    "marginMode": "crossed",
                    "planType": "pos_profit",
                    "orderId": "native-plan-tp",
                    "clientOid": "live-bitget-BTCUSDT-0011223344556677-tp",
                    "triggerPrice": "51000",
                    "triggerType": "mark_price",
                    "planStatus": "live",
                    "size": "0",
                },
            ],
        )
        super().__init__(**kwargs)

    async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
        rows = await super().get_single_position(symbol)
        for row in rows:
            row["stopLossId"] = None
            row["takeProfitId"] = None
        return rows

    async def place_position_tpsl(self, **kwargs: str) -> list[dict[str, str]]:
        self.protection_calls.append(kwargs)
        for plan in self.plans:
            if plan.get("planType") == "pos_loss":
                plan["clientOid"] = kwargs["stop_loss_client_oid"]
                plan["triggerPrice"] = kwargs["stop_loss"]
            elif plan.get("planType") == "pos_profit":
                plan["clientOid"] = kwargs["take_profit_client_oid"]
                plan["triggerPrice"] = kwargs["take_profit"]
        return [
            {
                "orderId": "native-plan-sl",
                "stopLossClientOid": kwargs["stop_loss_client_oid"],
            },
            {
                "orderId": "native-plan-tp",
                "stopSurplusClientOid": kwargs["take_profit_client_oid"],
            },
        ]


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
async def test_native_protection_claim_is_durable_before_post_and_replay_is_get_only() -> None:
    store = InMemoryLiveIntentStore()
    client = NativeProtectionClient(position_qty="0.008")
    original = client.place_position_tpsl

    async def fenced_place(**kwargs):
        for oid in (kwargs["stop_loss_client_oid"], kwargs["take_profit_client_oid"]):
            record = store.get(oid)
            assert record is not None and record.state == "requested"
            assert record.protection_root_oid == _intent().client_oid
        return await original(**kwargs)

    client.place_position_tpsl = fenced_place
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))
    first = await adapter.protect_filled_position(_intent(), _plan(), store)
    second = await adapter.protect_filled_position(_intent(), _plan(), store)
    assert first.state is second.state is ProtectionState.VENUE_PROTECTED
    assert len(client.protection_calls) == 1


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
async def test_position_level_plans_verify_when_the_position_row_omits_plan_ids() -> None:
    client = PositionWithoutPlanIdsClient(position_qty="0.008")
    capabilities = InMemoryProtectionCapabilityRepository()
    adapter = AsyncBitgetExecution(
        client,
        AsyncBitgetVenue(client),
        capability_repository=capabilities,
    )

    result = await adapter.protect_filled_position(_intent(), _plan(), InMemoryLiveIntentStore())

    assert result.state is ProtectionState.VENUE_PROTECTED
    assert client.close_calls == []
    assert adapter.degraded is False
    capability = capabilities.get("bitget", "DEMO", "BTCUSDT")
    assert capability is not None
    assert capability.native_state.value == "VERIFIED"


@pytest.mark.asyncio
async def test_plan_id_needs_readback_of_sl_tp_and_quantity() -> None:
    client = NativeProtectionClient(plans=[{"planType": "loss_plan", "size": "0.008"}])
    store = InMemoryLiveIntentStore()
    adapter = AsyncBitgetExecution(
        client, AsyncBitgetVenue(client), emergency_containment_enabled=True
    )

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
    adapter = AsyncBitgetExecution(
        client, AsyncBitgetVenue(client), emergency_containment_enabled=True
    )

    first = await adapter.protect_filled_position(_intent(), _plan(), store)
    second = await adapter.protect_filled_position(_intent(), _plan(), store)

    assert first.emergency_close_oid == "live-bitget-BTCUSDT-0011223344556677-emergency"
    assert second.emergency_close_oid is None  # existing placement claim is GET-only
    assert len(client.protection_calls) == 1
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
    adapter = AsyncBitgetExecution(
        client, AsyncBitgetVenue(client), emergency_containment_enabled=True
    )

    result = await adapter.protect_filled_position(_intent(), _plan(), store)

    assert result.emergency_close_oid is not None
    assert len(client.close_calls) == 1


@pytest.mark.asyncio
async def test_pre_cutover_flat_account_never_calls_emergency_close() -> None:
    client = NativeProtectionClient(position_qty="0", plans=[])
    adapter = AsyncBitgetExecution(
        client, AsyncBitgetVenue(client), emergency_containment_enabled=True
    )

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


class DelayedPlanClient(NativeProtectionClient):
    """Plans become readable only after N readbacks: the race that closed two trades."""

    def __init__(self, *, visible_after: int, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._visible_after = visible_after
        self._plan_reads = 0
        self._hidden_plans = list(self.plans)
        self.plans = []

    async def get_pending_plan_orders(self, symbol: str) -> list[dict[str, str]]:
        self._plan_reads += 1
        if self._plan_reads >= self._visible_after:
            self.plans = list(self._hidden_plans)
        return await super().get_pending_plan_orders(symbol)


@pytest.mark.asyncio
async def test_protection_confirmed_within_the_grace_never_closes_the_position() -> None:
    client = DelayedPlanClient(visible_after=3, position_qty="0.008")
    adapter = AsyncBitgetExecution(
        client,
        AsyncBitgetVenue(client),
        protection_grace_seconds=1.0,
        protection_grace_interval_seconds=0.05,
    )

    result = await adapter.protect_filled_position(_intent(), _plan(), InMemoryLiveIntentStore())

    assert result.state is ProtectionState.VENUE_PROTECTED
    assert "after-grace" in (result.reason or "")
    assert client.close_calls == []
    assert len(client.protection_calls) == 1
    assert adapter.degraded is False


@pytest.mark.asyncio
async def test_protection_never_confirmed_still_contains_after_the_grace() -> None:
    client = NativeProtectionClient(position_qty="0.008", plans=[])
    store = InMemoryLiveIntentStore()
    adapter = AsyncBitgetExecution(
        client,
        AsyncBitgetVenue(client),
        emergency_containment_enabled=True,
        protection_grace_seconds=0.2,
        protection_grace_interval_seconds=0.05,
    )

    result = await adapter.protect_filled_position(_intent(), _plan(), store)

    assert len(client.close_calls) == 1
    assert result.emergency_close_oid is not None
    assert adapter.degraded is True


@pytest.mark.asyncio
async def test_grace_is_read_only_and_skipped_without_a_budget() -> None:
    client = NativeProtectionClient(position_qty="0.008", plans=[])
    store = InMemoryLiveIntentStore()
    adapter = AsyncBitgetExecution(
        client, AsyncBitgetVenue(client), emergency_containment_enabled=True
    )

    await adapter.protect_filled_position(_intent(), _plan(), store)

    # Default budget is zero: the grace never re-places protection and only the
    # placement POSTs; containment stays the opt-in path exercised here.
    assert adapter._protection_grace_seconds == 0
    assert len(client.protection_calls) == 1
    assert len(client.close_calls) == 1


class FallbackIdentityClient(NativeProtectionClient):
    """A client whose provider reads prove the fallback identity of the entry."""

    environment = "DEMO"

    async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
        return [
            {
                "symbol": symbol,
                "holdSide": "long",
                "total": "0.008",
                "cTime": "1700000000000",
            }
        ]

    async def get_fills(self, symbol: str) -> list[dict[str, str]]:
        return [
            {
                "orderId": "provider-1",
                "symbol": symbol,
                "clientOid": _intent().client_oid,
                "side": "buy",
                "tradeSide": "open",
                "tradeId": "fill-1",
                "cTime": "1700000000000",
                "baseVolume": "0.008",
            }
        ]


def _proven_entry_intent() -> LiveIntentRecord:
    intent = _intent()
    intent.provider_order_id = "provider-1"
    intent.avg_price = Decimal("50000")
    intent.state = "filled"
    return intent


def _record_registrations(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    import fatty_trader.execution.bitget_fallback_protection as fallback_protection

    registrations: list[dict[str, Any]] = []

    def fake_register(**kwargs: Any) -> str:
        registrations.append(kwargs)
        return "bitget:BTCUSDT"

    monkeypatch.setattr(fallback_protection, "register_fallback", fake_register)
    return registrations


@pytest.mark.asyncio
async def test_unconfirmed_protection_is_locally_managed_without_a_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registrations = _record_registrations(monkeypatch)
    client = FallbackIdentityClient(position_qty="0.008", plans=[])
    capabilities = InMemoryProtectionCapabilityRepository()
    intent = _proven_entry_intent()
    store = InMemoryLiveIntentStore()
    store.save(intent)
    adapter = AsyncBitgetExecution(
        client,
        AsyncBitgetVenue(client),
        capability_repository=capabilities,
        fallback_protection_enabled=True,
        environment="DEMO",
    )

    result = await adapter.protect_filled_position(intent, _plan(), store)

    assert len(registrations) == 1
    assert result.state is ProtectionState.DEGRADED
    assert (result.reason or "").endswith("-locally-managed")
    assert result.emergency_close_oid is None
    assert client.close_calls == []
    assert store.get(f"{intent.client_oid}-emergency") is None
    assert adapter.degraded is True
    capability = capabilities.get("bitget", "DEMO", "BTCUSDT")
    assert capability is not None
    assert capability.native_state.value == "FAILED"
    assert "grace:" in (capability.last_error or "")


@pytest.mark.asyncio
async def test_locally_managed_registration_is_skipped_when_monitor_cannot_mutate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registrations = _record_registrations(monkeypatch)
    client = NativeProtectionClient(position_qty="0.008", plans=[])
    capabilities = InMemoryProtectionCapabilityRepository()
    adapter = AsyncBitgetExecution(
        client,
        AsyncBitgetVenue(client),
        fallback_protection_enabled=False,
        capability_repository=capabilities,
    )

    result = await adapter.protect_filled_position(
        _proven_entry_intent(), _plan(), InMemoryLiveIntentStore()
    )

    assert registrations == []
    assert result.state is ProtectionState.DEGRADED
    assert "fallback-monitor-mutations-disabled" in (result.reason or "")
    # No durable row was written, so the verdict must not claim local ownership.
    assert not (result.reason or "").endswith("-locally-managed")
    assert client.close_calls == []
    assert adapter.degraded is True
    capability = capabilities.get("bitget", "DEMO", "BTCUSDT")
    assert capability is not None
    assert capability.last_error == result.reason


@pytest.mark.asyncio
async def test_locally_managed_registration_is_attempted_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registrations = _record_registrations(monkeypatch)
    client = FallbackIdentityClient(position_qty="0.008", plans=[])
    intent = _proven_entry_intent()
    store = InMemoryLiveIntentStore()
    store.save(intent)
    adapter = AsyncBitgetExecution(
        client,
        AsyncBitgetVenue(client),
        fallback_protection_enabled=True,
        environment="DEMO",
    )

    result = await adapter.protect_filled_position(intent, _plan(), store)

    assert len(registrations) == 1
    kwargs = registrations[0]
    assert kwargs["position_key"] == intent.client_oid
    assert kwargs["provider_position_epoch"] == "1700000000000"
    assert kwargs["environment"] == "DEMO"
    assert kwargs["quantity"] == Decimal("0.008")
    assert kwargs["entry_price"] == Decimal("50000")
    assert result.state is ProtectionState.DEGRADED
    assert (result.reason or "").endswith("-locally-managed")
    assert "registration-failed" not in (result.reason or "")
    assert client.close_calls == []
    assert adapter.degraded is True


@pytest.mark.asyncio
async def test_locally_managed_registration_failure_never_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registrations = _record_registrations(monkeypatch)
    # No authenticated environment: the identity proof must fail, and the failure
    # must be contained into the result instead of escaping protect_filled_position.
    client = NativeProtectionClient(position_qty="0.008", plans=[])
    capabilities = InMemoryProtectionCapabilityRepository()
    intent = _proven_entry_intent()
    store = InMemoryLiveIntentStore()
    store.save(intent)
    adapter = AsyncBitgetExecution(
        client,
        AsyncBitgetVenue(client),
        fallback_protection_enabled=True,
        capability_repository=capabilities,
    )

    result = await adapter.protect_filled_position(intent, _plan(), store)

    assert registrations == []
    assert result.state is ProtectionState.DEGRADED
    assert "fallback-registration-failed" in (result.reason or "")
    # A failed registration leaves no local owner; do not emit the managed marker.
    assert not (result.reason or "").endswith("-locally-managed")
    assert client.close_calls == []
    assert adapter.degraded is True
    capability = capabilities.get("bitget", "DEMO", "BTCUSDT")
    assert capability is not None
    assert capability.last_error == result.reason


@pytest.mark.asyncio
async def test_emergency_containment_env_flag_restores_the_close_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BITGET_EMERGENCY_CONTAINMENT_ENABLED", "1")
    client = NativeProtectionClient(position_qty="0.008", plans=[])
    store = InMemoryLiveIntentStore()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))

    result = await adapter.protect_filled_position(_intent(), _plan(), store)

    assert adapter._emergency_containment_enabled is True
    assert len(client.close_calls) == 1
    assert result.emergency_close_oid is not None
    assert adapter.degraded is True
