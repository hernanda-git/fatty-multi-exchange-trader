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
                cTime="1700000000000",
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
                cTime="1700000000100",
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
        provider_fills=(
            {
                "tradeId": "fill-id",
                "orderId": "entry-id",
                "clientOid": "owned",
                "symbol": "BTCUSDT",
                "side": "buy",
                "tradeSide": "open",
                "baseVolume": "2",
                "price": "100",
                "fee": "0",
                "cTime": "1700000000000",
            },
        ),
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
@pytest.mark.parametrize(
    "source,value",
    [
        ("position", "1700000000200"),
        ("position", None),
        ("position", "01700000000000"),
        ("fill", None),
        ("fill", "1700000000200"),
        ("plan", "1699999999999"),
        ("plan", None),
    ],
)
async def test_restart_never_adopts_missing_or_mismatched_position_epoch(source, value):
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    client = Reads()
    intent, plan = evidence()
    target = {
        "position": client.position[0],
        "fill": intent.provider_fills[0],
        "plan": client.plans[0],
    }[source]
    target["cTime"] = value
    reader = GetOnlyProtectionRecovery(
        client, SimpleNamespace(inventory_issues=lambda env: []), environment="DEMO"
    )
    assert (await reader(intent, plan)).state is not ProtectionState.VENUE_PROTECTED
    assert "provider-position-not-uniquely-owned-and-protected" in await reader.inventory()
    assert all(call.startswith("get_") for call in client.calls)


@pytest.mark.asyncio
async def test_inventory_cannot_adopt_replacement_epoch_after_successful_verification():
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    client = Reads()
    reader = GetOnlyProtectionRecovery(
        client, SimpleNamespace(inventory_issues=lambda env: []), environment="DEMO"
    )
    assert (await reader(*evidence())).state is ProtectionState.VENUE_PROTECTED
    client.position[0]["cTime"] = "1700000000200"
    assert "provider-position-not-uniquely-owned-and-protected" in await reader.inventory()


@pytest.mark.asyncio
async def test_recovery_plan_cannot_expand_owned_entry_quantity():
    from dataclasses import replace

    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    client = Reads()
    intent, plan = evidence()
    client.position[0]["total"] = "3"
    reader = GetOnlyProtectionRecovery(
        client, SimpleNamespace(inventory_issues=lambda env: []), environment="DEMO"
    )
    assert (await reader(intent, replace(plan, quantity=Decimal("3")))).state is not (
        ProtectionState.VENUE_PROTECTED
    )


@pytest.mark.asyncio
async def test_current_canonical_quantity_must_equal_proven_owned_fills():
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    client = Reads()
    client.position[0]["total"] = "3"
    reader = GetOnlyProtectionRecovery(
        client, SimpleNamespace(inventory_issues=lambda env: []), environment="DEMO"
    )
    assert (await reader(*evidence())).state is not ProtectionState.VENUE_PROTECTED
