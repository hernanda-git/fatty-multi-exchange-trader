"""Regression coverage for venue-executed native plan closes (fakes only; no network).

The venue runs a position TP/SL plan as its own order.  That order reports the
plan's provider id as its client order id, so the close is provably ours, but it
never carries the intent-derived client order id the ledger indexes on.  Without a
binding step the close stays invisible: the entry lifecycle never settles, the
dispatch spins in recovery, and realized P&L is understated.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveIntentRecord
from fatty_trader.exchanges.bitget.native_close import (
    NativeCloseResolutionError,
    reconcile_native_plan_close,
    resolve_native_plan_close,
)

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


def _venue_fill() -> dict[str, Any]:
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
        "cTime": "1792506727378",
    }


class Venue:
    def __init__(
        self,
        *,
        fills: list[dict[str, Any]] | None = None,
        client_oid_by_order: dict[str, str] | None = None,
    ) -> None:
        self.fills = fills if fills is not None else [_venue_fill()]
        self.client_oid_by_order = (
            client_oid_by_order
            if client_oid_by_order is not None
            else {EXECUTED_ORDER_ID: PLAN_ORDER_ID}
        )
        self.order_detail_calls: list[str] = []

    async def get_fills(self, symbol: str) -> list[dict[str, Any]]:
        return [fill for fill in self.fills if fill.get("symbol") == symbol]

    async def get_order_detail(self, symbol: str, order_id: str) -> Any:
        self.order_detail_calls.append(order_id)
        client_oid = self.client_oid_by_order.get(order_id)
        if client_oid is None:
            return {"code": "00000", "data": []}
        return {"code": "00000", "data": [{"orderId": order_id, "clientOid": client_oid}]}


def _store() -> InMemoryLiveIntentStore:
    store = InMemoryLiveIntentStore()
    store.save(_entry_intent())
    store.save(_protection_intent())
    return store


async def _resolve(store: InMemoryLiveIntentStore, venue: Venue) -> Any:
    return await resolve_native_plan_close(
        intent=store.get(ROOT_OID),  # type: ignore[arg-type]
        read_fills=venue.get_fills,
        read_order_detail=venue.get_order_detail,
        plan_owner=lambda client_oid: store.protection_intent_by_provider_order_id(
            "bitget", client_oid
        ),
    )


@pytest.mark.asyncio
async def test_venue_plan_close_resolves_to_the_owned_protection_leg() -> None:
    store = _store()
    venue = Venue()

    native = await _resolve(store, venue)

    assert native is not None
    assert native.root_oid == ROOT_OID
    assert native.plan_oid == f"{ROOT_OID}-sl"
    assert native.executed_order_id == EXECUTED_ORDER_ID
    assert native.provider_fill_id == PROVIDER_FILL_ID
    assert native.quantity == Decimal("39")
    assert native.price == Decimal("0.5754")
    assert native.side == "BUY"


@pytest.mark.asyncio
async def test_close_is_bound_and_the_real_fill_is_recorded() -> None:
    store = _store()
    venue = Venue()

    native = await _resolve(store, venue)
    assert native is not None
    assert reconcile_native_plan_close(store, native) is True

    close = store.get(f"{ROOT_OID}-native-close")
    assert close is not None
    assert close.role == "CLOSE"
    assert close.state == "filled"
    assert close.filled_qty == Decimal("39")
    assert close.provider_order_id == EXECUTED_ORDER_ID
    assert close.provider_fill_ids == (PROVIDER_FILL_ID,)
    # The venue fill is now a durable ledger row under the owned close.
    recorded = [fill for owner, fill in store.fills if owner == f"{ROOT_OID}-native-close"]
    assert len(recorded) == 1
    # The binding proves the close against the entry that owns the plan.
    assert store.close_bindings[f"{ROOT_OID}-native-close"] == ROOT_OID


@pytest.mark.asyncio
async def test_unknown_venue_order_is_never_bound() -> None:
    store = _store()
    venue = Venue(client_oid_by_order={EXECUTED_ORDER_ID: "9999999999999999999"})

    native = await _resolve(store, venue)

    assert native is None
    assert store.get(f"{ROOT_OID}-native-close") is None


@pytest.mark.asyncio
async def test_a_fill_on_the_entry_side_is_never_bound() -> None:
    store = _store()
    entry_side_fill = {**_venue_fill(), "side": "sell"}
    venue = Venue(fills=[entry_side_fill])

    native = await _resolve(store, venue)

    assert native is None


@pytest.mark.asyncio
async def test_a_fill_for_another_symbol_is_never_bound() -> None:
    store = _store()
    venue = Venue(fills=[{**_venue_fill(), "symbol": "BTCUSDT"}])

    native = await _resolve(store, venue)

    assert native is None


@pytest.mark.asyncio
async def test_the_entry_fill_is_never_treated_as_its_own_close() -> None:
    store = _store()
    entry_fill = {
        **_venue_fill(),
        "tradeId": "1496442784672118784",
        "orderId": "1496442784150339587",
        "side": "sell",
    }
    venue = Venue(fills=[entry_fill])

    native = await _resolve(store, venue)

    assert native is None


@pytest.mark.asyncio
async def test_reconciliation_is_idempotent_across_cycles() -> None:
    store = _store()
    venue = Venue()

    native = await _resolve(store, venue)
    assert native is not None
    assert reconcile_native_plan_close(store, native) is True
    assert reconcile_native_plan_close(store, native) is False

    close = store.get(f"{ROOT_OID}-native-close")
    assert close is not None
    assert close.filled_qty == Decimal("39")


@pytest.mark.asyncio
async def test_a_conflicting_provider_order_id_is_refused() -> None:
    store = _store()
    venue = Venue()

    native = await _resolve(store, venue)
    assert native is not None
    reconcile_native_plan_close(store, native)

    conflicting = replace(native, executed_order_id="1496449999999999999")
    with pytest.raises(NativeCloseResolutionError):
        reconcile_native_plan_close(store, conflicting)
