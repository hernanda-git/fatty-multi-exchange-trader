"""End-to-end adversarial probes through AsyncBitgetExecution.reconcile_intent."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.async_execution import AsyncBitgetExecution
from fatty_trader.exchanges.bitget.live import (
    InMemoryLiveIntentStore,
    LiveIntentRecord,
    LiveOrderStatus,
    normalize_fill,
)


class Client:
    def __init__(self, detail, fills, position=None, pending=None):
        self._detail = detail
        self._fills = fills
        self._position = position if position is not None else []
        self._pending = pending if pending is not None else []

    async def get_order_detail(self, symbol, client_oid=None, **kw):
        if isinstance(self._detail, Exception):
            raise self._detail
        return self._detail

    async def get_fills(self, symbol, *a, **kw):
        return self._fills

    async def get_single_position(self, symbol):
        return self._position

    async def get_pending_orders(self, symbol):
        return self._pending


def fill(tid="30", qty="2", price="100", order="O1", **kw):
    base = {
        "tradeId": tid,
        "symbol": "BTCUSDT",
        "orderId": order,
        "price": price,
        "baseVolume": qty,
        "side": "buy",
        "tradeSide": "buy_single",
        "posMode": "one_way_mode",
        "profit": "0",
        "enterPointSource": "api",
        "cTime": "1700000000000",
        "feeDetail": [{"feeCoin": "USDT", "totalFee": "-0.1"}],
    }
    base.update(kw)
    return base


def intent(**kw):
    base = dict(
        exchange="bitget",
        client_oid="c1",
        symbol="BTCUSDT",
        side="BUY",
        requested_qty=Decimal("2"),
        filled_qty=Decimal("0"),
        state="unknown",
    )
    base.update(kw)
    return LiveIntentRecord(**base)


def build(client):
    from fatty_trader.exchanges.bitget.async_execution import AsyncBitgetVenue

    store = InMemoryLiveIntentStore()
    return AsyncBitgetExecution(client, AsyncBitgetVenue(client)), store


# ---------------------------------------------------- original prior blocker #1


@pytest.mark.asyncio
async def test_d1_filled_order_fully_observed_is_FILLED_not_UNKNOWN():
    """requested == detail == fill qty all 2, one real fill ID.

    Uses the REAL signed BitgetRestClient over MockTransport so that the genuine
    get_fills() pagination runs: the first page carries a NONEMPTY endId (the
    prior reviewer's exact repro) and exhaustion must be proven by an explicit
    empty object page, not by the cursor being empty.
    """
    import httpx

    from fatty_trader.exchanges.bitget.client import BitgetRestClient

    detail = {
        "orderId": "O1",
        "state": "filled",
        "baseVolume": "2",
        "priceAvg": "100",
        "size": "2",
        "clientOid": "c1",
        "symbol": "BTCUSDT",
    }
    pages = [
        {"code": "00000", "data": {"fillList": [fill()], "endId": "30"}},
        {"code": "00000", "data": {"fillList": [], "endId": ""}},
    ]
    seen = []

    def handler(request):
        seen.append(1)
        return httpx.Response(200, json=pages[min(len(seen) - 1, len(pages) - 1)])

    real = BitgetRestClient(
        api_key="k",
        api_secret="s",
        passphrase="p",
        transport=httpx.MockTransport(handler),
    )
    client = Client(detail, None)
    client.get_fills = real.get_fills  # type: ignore[method-assign]
    assert client._fills is None
    ex, store = build(client)
    i = intent()
    store.save(i)
    r = await ex.reconcile_intent(i)
    assert r.status is LiveOrderStatus.FILLED, f"fully observed order still UNKNOWN: {r}"
    assert r.filled_qty == Decimal("2")
    assert len(seen) == 2, "pagination did not actually run"


@pytest.mark.asyncio
async def test_d2_completely_empty_fills_page_stays_unknown():
    detail = {
        "orderId": "O1",
        "state": "filled",
        "baseVolume": "2",
        "priceAvg": "100",
        "size": "2",
        "clientOid": "c1",
        "symbol": "BTCUSDT",
    }
    client = Client(detail, {"fillList": [], "endId": ""})
    ex, store = build(client)
    i = intent()
    store.save(i)
    r = await ex.reconcile_intent(i)
    assert r.status is LiveOrderStatus.UNKNOWN, r


# ---------------------------------------------------- original prior blocker #2


@pytest.mark.asyncio
async def test_d3_empty_page_cannot_regress_durable_filled_qty():
    detail = {
        "orderId": "O1",
        "state": "filled",
        "baseVolume": "0",
        "priceAvg": "0",
        "size": "2",
        "clientOid": "c1",
        "symbol": "BTCUSDT",
    }
    client = Client(detail, {"fillList": [], "endId": ""})
    ex, store = build(client)
    i = intent(
        filled_qty=Decimal("2"),
        avg_price=Decimal("100"),
        fee=Decimal("0.1"),
        provider_order_id="O1",
        provider_fill_ids=("30",),
        provider_fills=(normalize_fill(fill()),),
    )
    store.save(i)
    r = await ex.reconcile_intent(i)
    assert r.filled_qty == Decimal("2"), f"durable 2 regressed to {r.filled_qty}"
    assert r.avg_price == Decimal("100")
    assert "30" in r.provider_fill_ids
    assert r.status is LiveOrderStatus.UNKNOWN


@pytest.mark.asyncio
async def test_d4_durable_evidence_survives_missing_detail_path():
    """Detail 40109 with an empty fills page must not erase durable evidence."""
    from fatty_trader.exchanges.bitget.client import BitgetApiError

    client = Client(BitgetApiError("40109 cannot be found"), {"fillList": [], "endId": ""})
    ex, store = build(client)
    i = intent(
        filled_qty=Decimal("2"),
        avg_price=Decimal("100"),
        fee=Decimal("0.1"),
        provider_order_id="O1",
        provider_fill_ids=("30",),
        provider_fills=(normalize_fill(fill()),),
    )
    store.save(i)
    r = await ex.reconcile_intent(i)
    assert r.filled_qty == Decimal("2"), f"durable 2 erased via missing-detail: {r}"
    assert r.status is LiveOrderStatus.UNKNOWN


@pytest.mark.asyncio
async def test_d5_provider_page_regression_of_avg_price_is_rejected():
    """Provider now reports a different price for the SAME confirmed trade."""
    detail = {
        "orderId": "O1",
        "state": "filled",
        "baseVolume": "2",
        "priceAvg": "999",
        "size": "2",
        "clientOid": "c1",
        "symbol": "BTCUSDT",
    }
    client = Client(detail, {"fillList": [fill(price="999")], "endId": ""})
    ex, store = build(client)
    i = intent(
        filled_qty=Decimal("2"),
        avg_price=Decimal("100"),
        fee=Decimal("0.1"),
        provider_order_id="O1",
        provider_fill_ids=("30",),
        provider_fills=(normalize_fill(fill(price="100")),),
    )
    store.save(i)
    r = await ex.reconcile_intent(i)
    # contradiction => UNKNOWN, and the durable avg price is not silently adopted
    assert r.status is LiveOrderStatus.UNKNOWN, r
    assert r.filled_qty == Decimal("2")


@pytest.mark.asyncio
async def test_d6_real_client_never_emits_an_idless_fill():
    """The real paginating client rejects the whole page on a missing tradeId."""
    import httpx

    from fatty_trader.exchanges.bitget.client import BitgetRestClient

    bad = fill()
    del bad["tradeId"]
    pages = [
        {"code": "00000", "data": {"fillList": [bad], "endId": "30"}},
        {"code": "00000", "data": {"fillList": [], "endId": ""}},
    ]
    calls = []

    def handler(request):
        i = len(calls)
        calls.append(i)
        return httpx.Response(200, json=pages[min(i, 1)])

    real = BitgetRestClient(
        api_key="k", api_secret="s", passphrase="p", transport=httpx.MockTransport(handler)
    )
    aggregate = await real.get_fills("BTCUSDT")
    assert aggregate["fillList"] == [], aggregate
    assert aggregate["endId"] == "unproven"


@pytest.mark.asyncio
async def test_d6b_stub_client_idless_fill_reaches_FILLED_contained_only_by_floor():
    detail = {
        "orderId": "O1",
        "state": "filled",
        "baseVolume": "2",
        "priceAvg": "100",
        "size": "2",
        "clientOid": "c1",
        "symbol": "BTCUSDT",
    }
    bad = fill()
    del bad["tradeId"]
    client = Client(detail, {"fillList": [bad], "endId": ""})
    ex, store = build(client)
    i = intent()
    store.save(i)
    r = await ex.reconcile_intent(i)
    assert r.provider_fill_ids == (), r
    # FIXED (commit c6d03b2): _complete_fills now rejects a row without exactly
    # one trade identity, so an id-less row can no longer certify a filled entry.
    assert r.status is LiveOrderStatus.UNKNOWN, r
    assert r.status is not LiveOrderStatus.FILLED, r


@pytest.mark.asyncio
async def test_d7_partial_fill_is_partial_not_unknown():
    detail = {
        "orderId": "O1",
        "state": "partially_filled",
        "baseVolume": "1",
        "priceAvg": "100",
        "size": "2",
        "clientOid": "c1",
        "symbol": "BTCUSDT",
    }
    client = Client(detail, {"fillList": [fill(qty="1")], "endId": ""})
    ex, store = build(client)
    i = intent()
    store.save(i)
    r = await ex.reconcile_intent(i)
    assert r.status is LiveOrderStatus.PARTIAL, r
    assert r.filled_qty == Decimal("1")


@pytest.mark.asyncio
async def test_d8_null_fills_page_never_treated_as_empty():
    detail = {
        "orderId": "O1",
        "state": "filled",
        "baseVolume": "2",
        "priceAvg": "100",
        "size": "2",
        "clientOid": "c1",
        "symbol": "BTCUSDT",
    }
    client = Client(detail, None)
    ex, store = build(client)
    i = intent()
    store.save(i)
    r = await ex.reconcile_intent(i)
    assert r.status is LiveOrderStatus.UNKNOWN, r
