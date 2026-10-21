"""The monitor must bind our own plan closes, not file them as stranger exits.

A venue TP/SL plan executes as its own order, and the resulting fill carries no
client order id.  The generic provider-exit reconciler would claim that fill as
somebody else's exit and record it under a synthetic owner, which leaves the
entry lifecycle open forever.  The monitor therefore resolves the executed order
first and binds the fill to the entry that owns the plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveIntentRecord
from fatty_trader.execution.bitget_monitor import BitgetMonitor
from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository

ROOT_OID = "live-bitget-WLDUSDT-a80eda4836895a04"
PLAN_ORDER_ID = "1496442792033873920"
EXECUTED_ORDER_ID = "1496447164467527682"
PROVIDER_FILL_ID = "1496447164672118784"


def _entry_intent() -> LiveIntentRecord:
    return LiveIntentRecord(
        exchange="bitget",
        client_oid=ROOT_OID,
        symbol="WLDUSDT",
        side="SELL",
        role="ENTRY",
        state="filled",
        requested_qty=Decimal("39"),
        filled_qty=Decimal("39"),
        avg_price=Decimal("0.5627"),
        provider_order_id="1496442784150339587",
        provider_fill_ids=("1496442784672118784",),
        margin_reservation_id=None,
        dispatch_id=None,
        entry_leg=None,
    )


def _protection_intent() -> LiveIntentRecord:
    return LiveIntentRecord(
        exchange="bitget",
        client_oid=f"{ROOT_OID}-sl",
        symbol="WLDUSDT",
        side="BUY",
        role="SL",
        state="acknowledged",
        requested_qty=Decimal("39"),
        filled_qty=Decimal("0"),
        provider_order_id=PLAN_ORDER_ID,
        protection_root_oid=ROOT_OID,
    )


def _venue_fill(cTime: int) -> dict[str, Any]:
    return {
        "tradeId": PROVIDER_FILL_ID,
        "orderId": EXECUTED_ORDER_ID,
        "symbol": "WLDUSDT",
        "side": "buy",
        "price": "0.5754",
        "baseVolume": "39",
        "profit": "-0.4953",
        "feeDetail": [{"feeCoin": "USDT", "totalFee": "-0.01346436"}],
        "tradeSide": "buy_single",
        "enterPointSource": "api",
        "cTime": str(cTime),
    }


@dataclass
class Venue:
    fills: list[dict[str, Any]] = field(default_factory=list)
    order_client_oid: str = PLAN_ORDER_ID
    order_detail_calls: list[str] = field(default_factory=list)

    async def get_all_positions(self) -> list[dict[str, Any]]:
        return []

    async def get_pending_orders(self) -> list[dict[str, Any]]:
        return []

    async def get_pending_plan_orders(self, symbol: str) -> list[dict[str, Any]]:
        return []

    async def get_single_position(self, symbol: str) -> list[dict[str, Any]]:
        return []

    async def get_order_detail(
        self, symbol: str, *, client_oid: str | None = None, order_id: str | None = None
    ) -> Any:
        self.order_detail_calls.append(order_id or client_oid or "")
        if self.order_client_oid == "":
            return {"code": "00000", "data": []}
        return {
            "code": "00000",
            "data": [{"orderId": order_id, "clientOid": self.order_client_oid}],
        }

    async def get_fills(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return self.fills

    async def get_clock_skew_ms(self) -> int:
        return 0


@pytest.mark.asyncio
async def test_the_monitor_binds_our_plan_close_to_its_entry() -> None:
    import time

    store = InMemoryLiveIntentStore()
    store.save(_entry_intent())
    store.save(_protection_intent())
    venue = Venue(fills=[_venue_fill(int(time.time() * 1000))])
    repository = InMemoryReconciliationRepository()

    monitor = BitgetMonitor(venue, repository, live_intent_store=store)
    await monitor.run_once()

    close = store.get(f"{ROOT_OID}-native-close")
    assert close is not None
    assert close.role == "CLOSE"
    assert close.filled_qty == Decimal("39")
    assert close.provider_order_id == EXECUTED_ORDER_ID
    # The fill is a durable ledger row under the owned close.
    recorded = [fill for owner, fill in store.fills if owner == f"{ROOT_OID}-native-close"]
    assert len(recorded) == 1
    # It is not also filed as somebody else's provider exit.
    assert not [owner for owner, _ in store.fills if owner.startswith("provider-bitget-")]


@pytest.mark.asyncio
async def test_a_liquidation_is_still_recorded_as_a_provider_exit() -> None:
    import time

    store = InMemoryLiveIntentStore()
    venue = Venue(
        fills=[
            {
                **_venue_fill(int(time.time() * 1000)),
                "tradeSide": "burst_sell_single",
                "enterPointSource": "SYS",
            }
        ],
        order_client_oid="",
    )
    repository = InMemoryReconciliationRepository()

    monitor = BitgetMonitor(venue, repository, live_intent_store=store)
    await monitor.run_once()

    assert store.get(f"{ROOT_OID}-native-close") is None
    assert any(owner.startswith("provider-bitget-") for owner, _ in store.fills)


@pytest.mark.asyncio
async def test_an_old_fill_is_not_resolved_against_our_plans() -> None:
    store = InMemoryLiveIntentStore()
    store.save(_entry_intent())
    store.save(_protection_intent())
    # Far outside the lookback window: the venue history must not be re-read.
    venue = Venue(fills=[_venue_fill(1_600_000_000_000)])
    repository = InMemoryReconciliationRepository()

    monitor = BitgetMonitor(venue, repository, live_intent_store=store)
    await monitor.run_once()

    assert venue.order_detail_calls == []
    assert store.get(f"{ROOT_OID}-native-close") is None
