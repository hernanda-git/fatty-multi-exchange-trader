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
async def test_locally_managed_position_does_not_veto_account_inventory():
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    calls: list[tuple[str, str, Decimal, str]] = []

    def locally_managed(symbol, direction, quantity, environment):
        calls.append((symbol, direction, quantity, environment))
        return symbol == "BTCUSDT" and direction == "LONG" and quantity == Decimal("2")

    client = Reads()
    reader = GetOnlyProtectionRecovery(
        client,
        SimpleNamespace(
            inventory_issues=lambda env: [],
            locally_managed_position=locally_managed,
        ),
        environment="DEMO",
    )
    reader.begin()

    issues = await reader.inventory()

    assert "provider-position-not-uniquely-owned-and-protected" not in issues
    assert calls == [("BTCUSDT", "LONG", Decimal("2"), "DEMO")]


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


class ReservationCandidates:
    """Records retirements; candidates are supplied by the test."""

    def __init__(self, candidates):
        self._candidates = candidates
        self.resolved: list[tuple[str, str]] = []

    def orphan_candidates(self, environment):
        assert environment in {"LIVE", "DEMO"}
        return list(self._candidates)

    def resolve(self, reservation_id, outcome):
        self.resolved.append((str(reservation_id), outcome))


def orphan_candidate():
    return {
        "id": "res-1",
        "symbol": "ENAUSDT",
        "client_order_id": "root-oid",
        "state": "reserved",
    }


class Unreadable(Reads):
    async def get_single_position(self, symbol):
        raise TimeoutError("provider offline")


@pytest.mark.asyncio
async def test_proved_empty_orphan_reservation_is_retired_and_alerted():
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    client = Reads()
    client.position = []
    client.pending = []
    client.plans = []
    reservations = ReservationCandidates([orphan_candidate()])
    alerts: list[dict[str, object]] = []
    repository = SimpleNamespace(
        inventory_issues=lambda env: [],
        alert_orphan_retirement=lambda **kwargs: alerts.append(kwargs),
    )
    reader = GetOnlyProtectionRecovery(
        client, repository, environment="LIVE", reservations=reservations
    )

    retired = await reader.retire_proved_empty_orphans()

    assert reservations.resolved == [("res-1", "REJECTED")]
    assert retired == [
        {"reservation_id": "res-1", "symbol": "ENAUSDT", "previous_state": "reserved"}
    ]
    assert alerts == [
        {"reservation_id": "res-1", "symbol": "ENAUSDT", "reason": "recovery-proved-empty"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("position", "pending", "plans"),
    [
        ([{"symbol": "ENAUSDT", "total": "5", "holdSide": "long"}], [], []),
        ([], [{"symbol": "ENAUSDT", "clientOid": "root-oid"}], []),
        ([], [{"total": "5"}], []),
        ([], [], [{"symbol": "ENAUSDT", "planType": "pos_loss"}]),
    ],
    ids=["position-open", "pending-order", "pending-order-without-identity", "pending-plan"],
)
async def test_orphan_is_never_retired_while_the_venue_shows_work(position, pending, plans):
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    client = Reads()
    client.position = position
    client.pending = pending
    client.plans = plans
    reservations = ReservationCandidates([orphan_candidate()])
    reader = GetOnlyProtectionRecovery(
        client,
        SimpleNamespace(inventory_issues=lambda env: []),
        environment="LIVE",
        reservations=reservations,
    )

    assert await reader.retire_proved_empty_orphans() == []
    assert reservations.resolved == []


@pytest.mark.asyncio
async def test_orphan_retirement_requires_readable_venue_and_symbol():
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    unreadable = ReservationCandidates([orphan_candidate()])
    reader = GetOnlyProtectionRecovery(
        Unreadable(),
        SimpleNamespace(inventory_issues=lambda env: []),
        environment="LIVE",
        reservations=unreadable,
    )
    assert await reader.retire_proved_empty_orphans() == []
    assert unreadable.resolved == []

    anonymous = ReservationCandidates([dict(orphan_candidate(), symbol="")])
    reader = GetOnlyProtectionRecovery(
        Reads(),
        SimpleNamespace(inventory_issues=lambda env: []),
        environment="LIVE",
        reservations=anonymous,
    )
    assert await reader.retire_proved_empty_orphans() == []
    assert anonymous.resolved == []


@pytest.mark.asyncio
async def test_orphan_retirement_is_a_noop_without_a_reservation_repository():
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    reader = GetOnlyProtectionRecovery(
        Reads(), SimpleNamespace(inventory_issues=lambda env: []), environment="LIVE"
    )

    assert await reader.retire_proved_empty_orphans() == []
    assert await reader.settle_absent_protection() == []


class ProtectionIntents:
    """Unknown non-entry intents supplied by the test; records settlements."""

    def __init__(self, intents):
        self._intents = intents
        self.settled: list[str] = []

    def unknown_non_entry(self):
        return tuple(self._intents)

    def settle_absent(self, client_oid):
        self.settled.append(client_oid)
        return 1


def protection_intent(filled: str = "0"):
    return SimpleNamespace(
        client_oid="live-WLDUSDT-test-sl",
        symbol="WLDUSDT",
        filled_qty=Decimal(filled),
        dispatch_id="dispatch-1",
    )


def flat_reads():
    client = Reads()
    client.position = []
    client.pending = []
    client.plans = []
    return client


@pytest.mark.asyncio
async def test_absent_protection_plan_on_a_flat_symbol_is_settled_once():
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    intents = ProtectionIntents([protection_intent()])
    alerts: list[dict[str, object]] = []
    repository = SimpleNamespace(
        inventory_issues=lambda env: [],
        alert_protection_settled=lambda **kwargs: alerts.append(kwargs),
    )
    reader = GetOnlyProtectionRecovery(
        flat_reads(), repository, environment="LIVE", intent_store=intents
    )

    settled = await reader.settle_absent_protection()

    assert settled == [
        {"intent": "live-WLDUSDT-test-sl", "symbol": "WLDUSDT", "dispatch_id": "dispatch-1"}
    ]
    assert intents.settled == ["live-WLDUSDT-test-sl"]
    assert alerts == [
        {
            "client_oid": "live-WLDUSDT-test-sl",
            "symbol": "WLDUSDT",
            "reason": "protection-plan-absent-settled",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("position", "pending", "plans"),
    [
        ([{"symbol": "WLDUSDT", "total": "11", "holdSide": "long"}], [], []),
        ([], [{"symbol": "WLDUSDT", "clientOid": "x"}], []),
        ([], [{"total": "11"}], []),
        ([], [], [{"symbol": "WLDUSDT", "planType": "pos_loss"}]),
    ],
    ids=["position-open", "pending-order", "pending-order-without-identity", "pending-plan"],
)
async def test_absent_protection_is_not_settled_without_full_proof(position, pending, plans):
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    client = Reads()
    client.position = position
    client.pending = pending
    client.plans = plans
    intents = ProtectionIntents([protection_intent()])
    reader = GetOnlyProtectionRecovery(
        client,
        SimpleNamespace(inventory_issues=lambda env: []),
        environment="LIVE",
        intent_store=intents,
    )

    assert await reader.settle_absent_protection() == []
    assert intents.settled == []


@pytest.mark.asyncio
async def test_filled_or_unreadable_protection_intent_is_never_settled():
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery

    filled = ProtectionIntents([protection_intent(filled="11")])
    reader = GetOnlyProtectionRecovery(
        flat_reads(),
        SimpleNamespace(inventory_issues=lambda env: []),
        environment="LIVE",
        intent_store=filled,
    )
    assert await reader.settle_absent_protection() == []
    assert filled.settled == []

    unreadable = ProtectionIntents([protection_intent()])
    reader = GetOnlyProtectionRecovery(
        Unreadable(),
        SimpleNamespace(inventory_issues=lambda env: []),
        environment="LIVE",
        intent_store=unreadable,
    )
    assert await reader.settle_absent_protection() == []
    assert unreadable.settled == []
