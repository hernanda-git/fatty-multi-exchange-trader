from decimal import Decimal
from types import SimpleNamespace

import pytest

from fatty_trader.domain.enums import Direction, Exchange
from fatty_trader.exchanges.bitget.live import LiveIntentRecord
from fatty_trader.execution.protection import ProtectionPlan, ProtectionState


class Reads:
    def __init__(self):
        self.position = [
            dict(
                symbol="BTCUSDT",
                total="2",
                holdSide="long",
                marginMode="isolated",
                posMode="one_way_mode",
                stopLossId="sl-id",
                takeProfitId="tp-id",
            )
        ]
        self.plans = [
            dict(
                symbol="BTCUSDT",
                holdSide="long",
                planType=kind,
                orderId=pid,
                clientOid="owned-" + leg,
                triggerPrice=level,
                triggerType="mark_price",
                executePrice="0",
                size="0",
                planStatus="live",
            )
            for kind, pid, leg, level in [
                ("pos_loss", "sl-id", "sl", "90"),
                ("pos_profit", "tp-id", "tp", "110"),
            ]
        ]
        self.pending = []
        self.calls = []

    async def get_single_position(self, symbol):
        self.calls.append("get_single_position")
        return self.position

    async def get_pending_plan_orders(self, symbol):
        self.calls.append("get_pending_plan_orders")
        return self.plans

    async def get_all_positions(self):
        self.calls.append("get_all_positions")
        return self.position

    async def get_pending_orders(self):
        self.calls.append("get_pending_orders")
        return self.pending

    def __getattr__(self, name):
        raise AssertionError("unexpected provider method: " + name)


def evidence():
    intent = LiveIntentRecord(
        "bitget",
        "owned",
        "BTCUSDT",
        "BUY",
        filled_qty=Decimal("2"),
        provider_order_id="entry-id",
        provider_fill_ids=("fill-id",),
    )
    plan = ProtectionPlan(
        Exchange.BITGET, "BTCUSDT", Direction.LONG, Decimal("2"), Decimal("90"), (Decimal("110"),)
    )
    return intent, plan


@pytest.mark.asyncio
async def test_exact_existing_owned_native_plans_recover_without_writes():
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    client = Reads()
    reader = GetOnlyProtectionRecovery(
        client, SimpleNamespace(inventory_issues=lambda env: []), environment="DEMO"
    )
    result = await reader(*evidence())
    assert result.state is ProtectionState.VENUE_PROTECTED
    assert result.observed_quantity == Decimal("2")
    assert await reader.inventory() == ()
    assert all(call.startswith("get_") for call in client.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [("clientOid", "foreign"), ("holdSide", "short"), ("size", "1"), ("triggerPrice", "94")],
)
async def test_never_adopts_inexact_plan_identity_side_qty_or_level(field, value):
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    client = Reads()
    client.plans[0][field] = value
    reader = GetOnlyProtectionRecovery(
        client, SimpleNamespace(inventory_issues=lambda env: []), environment="DEMO"
    )
    assert (await reader(*evidence())).state is not ProtectionState.VENUE_PROTECTED


@pytest.mark.asyncio
async def test_new_pass_cannot_use_last_pass_owned_inventory_truth():
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    client = Reads()
    reader = GetOnlyProtectionRecovery(
        client, SimpleNamespace(inventory_issues=lambda env: []), environment="DEMO"
    )
    assert (await reader(*evidence())).state is ProtectionState.VENUE_PROTECTED
    reader.begin()
    assert "provider-position-not-uniquely-owned-and-protected" in await reader.inventory()


@pytest.mark.asyncio
async def test_current_canonical_quantity_must_equal_proven_owned_fills():
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    client = Reads()
    client.position[0]["total"] = "3"
    reader = GetOnlyProtectionRecovery(
        client, SimpleNamespace(inventory_issues=lambda env: []), environment="DEMO"
    )
    assert (await reader(*evidence())).state is not ProtectionState.VENUE_PROTECTED


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [{"userId": "999"}, {}, {"userId": "NaN"}, None])
async def test_baseline_account_mismatch_never_reads_owned_inventory(identity):
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    class BoundReads(Reads):
        async def _get(self, path):
            return identity

    client = BoundReads()
    repository = SimpleNamespace(
        baseline_binding_issues=lambda uid, env: [] if uid == "123" else ["mismatch"],
        inventory_issues=lambda env: [],
    )
    reader = GetOnlyProtectionRecovery(client, repository, environment="LIVE")
    with pytest.raises(ValueError):
        await reader.inventory()
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {},
        {"clientOid": "manual"},
        {"reduceOnly": "YES"},
        {"price": "99"},
        {"size": "4"},
        {"baseVolume": "NaN"},
        {"orderId": "other"},
    ],
)
async def test_recovery_accepts_only_exact_owned_waiting_limit(change):
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    client = Reads()
    client.position = []
    intent = LiveIntentRecord(
        "bitget",
        "owned-limit",
        "BTCUSDT",
        "BUY",
        requested_qty=Decimal("3"),
        provider_order_id="limit-id",
        entry_leg="limit",
        order_type="limit",
        limit_price=Decimal("100"),
    )
    client.pending = [
        {
            "clientOid": "owned-limit",
            "symbol": "BTCUSDT",
            "side": "buy",
            "orderType": "limit",
            "reduceOnly": "NO",
            "state": "live",
            "price": "100",
            "size": "3",
            "baseVolume": "0",
            "orderId": "limit-id",
            **change,
        }
    ]
    reader = GetOnlyProtectionRecovery(
        client,
        SimpleNamespace(inventory_issues=lambda env: []),
        environment="LIVE",
        intent_store=SimpleNamespace(pending_entries=lambda: (intent,)),
    )
    issues = await reader.inventory()
    assert issues == () if not change else "pending-orders-require-reconciliation" in issues


@pytest.mark.asyncio
async def test_recovery_refuses_missing_waiting_order_and_duplicate_provider_rows():
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    client = Reads()
    client.position = []
    intent = LiveIntentRecord(
        "bitget",
        "owned-limit",
        "BTCUSDT",
        "BUY",
        requested_qty=Decimal("3"),
        provider_order_id="limit-id",
        entry_leg="limit",
        order_type="limit",
        limit_price=Decimal("100"),
    )
    reader = GetOnlyProtectionRecovery(
        client,
        SimpleNamespace(inventory_issues=lambda env: []),
        environment="LIVE",
        intent_store=SimpleNamespace(pending_entries=lambda: (intent,)),
    )
    assert "owned-pending-entry-inventory-mismatch" in await reader.inventory()
    row = {
        "clientOid": "owned-limit",
        "symbol": "BTCUSDT",
        "side": "buy",
        "orderType": "limit",
        "reduceOnly": "NO",
        "state": "live",
        "price": "100",
        "size": "3",
        "baseVolume": "0",
        "orderId": "limit-id",
    }
    client.pending = [row, dict(row)]
    assert "pending-entry-duplicate" in await reader.inventory()
