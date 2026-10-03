"""Offline authenticated-transport and durable accounting contract regressions."""

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from fatty_trader.exchanges.bitget.async_execution import AsyncBitgetExecution
from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue
from fatty_trader.exchanges.bitget.client import BitgetApiError, BitgetRestClient
from fatty_trader.exchanges.bitget.live import LiveIntentRecord, LiveOrderStatus


def owned_intent():
    fill = {
        "tradeId": "100",
        "orderId": "owned-order",
        "symbol": "BTCUSDT",
        "side": "buy",
        "tradeSide": "buy_single",
        "posMode": "one_way_mode",
        "baseVolume": "2",
        "price": "100",
        "fee": Decimal("0.2"),
        "cTime": "1700000000000",
    }
    return LiveIntentRecord(
        "bitget",
        "owned",
        "BTCUSDT",
        "BUY",
        requested_qty=Decimal("2"),
        filled_qty=Decimal("2"),
        avg_price=Decimal("100"),
        fee=Decimal("0.2"),
        provider_order_id="owned-order",
        provider_fill_ids=("100",),
        provider_fills=(fill,),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_empty_detail_never_erases_durable_fill(missing):
    class Reads:
        async def get_order_detail(self, *args, **kwargs):
            if missing:
                raise BitgetApiError("40109", code="40109")
            return {}

        async def get_fills(self, *args):
            return []

        async def get_single_position(self, *args):
            return []

        async def get_pending_orders(self, *args):
            return []

    client = Reads()
    intent = owned_intent()
    result = await AsyncBitgetExecution(client, AsyncBitgetVenue(client)).reconcile_intent(intent)
    assert result.status is LiveOrderStatus.UNKNOWN
    assert result.filled_qty == intent.filled_qty
    assert result.avg_price == intent.avg_price
    assert result.fee == intent.fee
    assert result.provider_fill_ids == intent.provider_fill_ids
    assert result.provider_fills == intent.provider_fills


@pytest.mark.asyncio
async def test_authenticated_short_page_cursor_is_consumed_before_complete_order_readback():
    page = json.loads(
        (Path(__file__).parents[1] / "fixtures/bitget_one_way_fills.json").read_text()
    )["page"]
    opening = page["fillList"][1]
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.headers["ACCESS-SIGN"]
        assert request.headers["ACCESS-KEY"] == "offline"
        if request.url.path.endswith("detail"):
            data = {"orderId": opening["orderId"], "state": "filled", "baseVolume": "12"}
        elif request.url.params.get("idLessThan"):
            assert request.url.params["idLessThan"] == page["endId"]
            data = {"fillList": [], "endId": ""}
        else:
            data = page
        return httpx.Response(200, json={"code": "00000", "data": data})

    client = BitgetRestClient(
        "offline", "offline", "offline", transport=httpx.MockTransport(respond)
    )
    intent = LiveIntentRecord(
        "bitget",
        "owned",
        "WLDUSDT",
        "BUY",
        requested_qty=Decimal("12"),
        provider_order_id=opening["orderId"],
    )
    try:
        result = await AsyncBitgetExecution(client, AsyncBitgetVenue(client)).reconcile_intent(
            intent
        )
        assert result.status is LiveOrderStatus.FILLED
        assert result.filled_qty == Decimal("12")
        assert result.fee == Decimal("0.00324504")
        assert result.provider_fill_ids == (opening["tradeId"],)
        assert len(requests) == 3  # detail + short page + explicit terminal page
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["buy", "sell"])
async def test_real_one_way_fill_can_prove_owned_restart_epoch(side):
    from dataclasses import replace
    from types import SimpleNamespace

    from fatty_trader.domain.enums import Direction
    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery
    from fatty_trader.execution.protection import ProtectionState
    from tests.unit.test_get_only_protection_recovery import Reads, evidence

    intent, plan = evidence()
    intent.side = side.upper()
    intent.provider_fills[0].update(
        side=side, tradeSide=side + "_single", posMode="one_way_mode", profit="0"
    )
    client = Reads()
    if side == "sell":
        plan = replace(plan, direction=Direction.SHORT)
        client.position[0]["holdSide"] = "short"
        for row in client.plans:
            row["holdSide"] = "short"
    reader = GetOnlyProtectionRecovery(
        client, SimpleNamespace(inventory_issues=lambda env: []), environment="DEMO"
    )
    assert (await reader(intent, plan)).state is ProtectionState.VENUE_PROTECTED
    assert await reader.inventory() == ()
    client.position[0]["cTime"] = "1700000000200"
    assert (await reader(intent, plan)).state is not ProtectionState.VENUE_PROTECTED


@pytest.mark.asyncio
async def test_fallback_requires_consumed_cursor_and_accepts_owned_one_way_direction():
    from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore
    from tests.unit.test_get_only_protection_recovery import evidence

    intent, plan = evidence()
    intent.state = "filled"
    fill = intent.provider_fills[0]
    fill.update(tradeSide="buy_single", posMode="one_way_mode")

    class Reads:
        environment = "DEMO"
        complete = True

        async def get_single_position(self, symbol):
            return [{"symbol": symbol, "total": "2", "holdSide": "long", "cTime": fill["cTime"]}]

        async def get_fills(self, symbol):
            return {"fillList": [fill], "endId": "" if self.complete else "next-page"}

    client = Reads()
    store = InMemoryLiveIntentStore()
    store.claim(intent)
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client), environment="DEMO")
    assert await adapter._fallback_identity(intent, plan, store) == (fill["cTime"], "DEMO")
    client.complete = False
    with pytest.raises(ValueError):
        await adapter._fallback_identity(intent, plan, store)


@pytest.mark.asyncio
async def test_unknown_intent_nonempty_detail_preserves_durable_floor_and_cursor():
    from fatty_trader.exchanges.bitget.reconciliation import reconcile_unknown_intent

    intent = owned_intent()

    async def detail(*args):
        return {"orderId": "owned-order", "state": "filled", "baseVolume": "2"}

    async def fills(*args):
        return {"fillList": [], "endId": "unconsumed"}

    async def empty(*args):
        return []

    result = await reconcile_unknown_intent(
        intent,
        read_order_detail=detail,
        read_fills=fills,
        read_position=empty,
        read_pending_orders=empty,
    )
    assert result.state == "unknown"
    assert result.filled_qty == Decimal("2")
    assert result.provider_fill_ids == ("100",)
    assert result.fee == Decimal("0.2")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal", [None, {}, {"fillList": []}, {"fillList": [], "endId": None}, "failure", "repeated"]
)
async def test_unproven_terminal_page_preserves_rows_but_never_claims_complete(terminal):
    page = json.loads(
        (Path(__file__).parents[1] / "fixtures/bitget_one_way_fills.json").read_text()
    )["page"]
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.headers["ACCESS-SIGN"]
        if len(requests) == 1:
            data = page
        elif terminal == "failure":
            return httpx.Response(200, json={"code": "400172", "msg": "unavailable"})
        elif terminal == "repeated":
            data = page
        else:
            data = terminal
        return httpx.Response(200, json={"code": "00000", "data": data})

    client = BitgetRestClient(
        "offline", "offline", "offline", transport=httpx.MockTransport(respond)
    )
    try:
        if terminal == "failure":
            # A provider error mid-walk must propagate. Returning a partial row
            # set would silently downgrade "unknown" to "these are all the
            # fills", which is the fail-open this contract exists to prevent.
            with pytest.raises(BitgetApiError) as excinfo:
                await client.get_fills("WLDUSDT", max_pages=3)
            assert excinfo.value.code == "400172"
            assert len(requests) == 2
            return
        data = await client.get_fills("WLDUSDT", max_pages=3)
        assert data["endId"]
        assert data["fillList"] == page["fillList"]
        assert len(requests) == 2
        if terminal is None:
            assert data["pages"][-1] is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_page_bound_keeps_cursor_and_window_stable():
    requests = []

    def respond(request):
        requests.append(request)
        n = 1000 - len(requests)
        return httpx.Response(
            200,
            json={"code": "00000", "data": {"fillList": [{"tradeId": str(n)}], "endId": str(n)}},
        )

    client = BitgetRestClient(
        "offline", "offline", "offline", transport=httpx.MockTransport(respond)
    )
    try:
        data = await client.get_fills("BTCUSDT", max_pages=3)
        assert len(requests) == 3
        assert data["endId"] == "997"
        assert len(data["fillList"]) == 3
        assert len({r.url.params["endTime"] for r in requests}) == 1
        assert requests[1].url.params["idLessThan"] == "999"
    finally:
        await client.aclose()


def test_missing_cursor_metadata_is_not_complete_evidence():
    from fatty_trader.exchanges.bitget.reconciliation import _complete_fills

    assert _complete_fills({"fillList": []}) == ([], False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_fields",
    [
        {"baseVolume": "invalid"},
        {"fee": "invalid"},
        {"feeDetail": [{"totalFee": "invalid"}]},
        {"feeDetail": "invalid-json"},
    ],
)
async def test_malformed_fill_row_cannot_make_other_fills_complete(bad_fields):
    class Reads:
        async def get_order_detail(self, *args, **kwargs):
            return {"orderId": "owned-order", "state": "filled", "baseVolume": "2"}

        async def get_fills(self, *args):
            good = owned_intent().provider_fills[0]
            bad = {**good, "tradeId": "101", **bad_fields}
            return {"fillList": [good, bad], "endId": ""}

    client = Reads()
    result = await AsyncBitgetExecution(client, AsyncBitgetVenue(client)).reconcile_intent(
        owned_intent()
    )
    assert result.status is LiveOrderStatus.UNKNOWN
    assert result.filled_qty == Decimal("2")


@pytest.mark.asyncio
async def test_different_page_with_same_quantity_cannot_erase_durable_trade():
    class Reads:
        async def get_order_detail(self, *args, **kwargs):
            return {"orderId": "owned-order", "state": "filled", "baseVolume": "2"}

        async def get_fills(self, *args):
            return [{**owned_intent().provider_fills[0], "tradeId": "101"}]

    client = Reads()
    result = await AsyncBitgetExecution(client, AsyncBitgetVenue(client)).reconcile_intent(
        owned_intent()
    )
    assert result.status is LiveOrderStatus.UNKNOWN
    assert set(result.provider_fill_ids) == {"100", "101"}
    assert result.filled_qty == Decimal("4")
    assert result.fee == Decimal("0.4")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields", [{"baseVolume": "1"}, {"fee": Decimal("0.1")}, {"cTime": "1700000000200"}]
)
async def test_same_trade_id_cannot_regress_confirmed_economics(fields):
    class Reads:
        async def get_order_detail(self, *args, **kwargs):
            return {"orderId": "owned-order", "state": "filled", "baseVolume": "2"}

        async def get_fills(self, *args):
            return [{**owned_intent().provider_fills[0], **fields}]

    client = Reads()
    result = await AsyncBitgetExecution(client, AsyncBitgetVenue(client)).reconcile_intent(
        owned_intent()
    )
    assert result.status is LiveOrderStatus.UNKNOWN
    assert result.filled_qty == Decimal("2")
    assert result.fee == Decimal("0.2")
    assert result.provider_fills[0]["baseVolume"] == "2"
    assert result.provider_fills[0]["cTime"] == "1700000000000"


@pytest.mark.asyncio
async def test_restart_plan_cannot_borrow_another_symbols_owned_entry_epoch():
    from dataclasses import replace
    from types import SimpleNamespace

    from fatty_trader.execution.bitget_protection_recovery import GetOnlyProtectionRecovery
    from fatty_trader.execution.protection import ProtectionState
    from tests.unit.test_get_only_protection_recovery import Reads, evidence

    client = Reads()
    intent, plan = evidence()
    plan = replace(plan, symbol="ETHUSDT")
    client.position[0]["symbol"] = "ETHUSDT"
    for row in client.plans:
        row["symbol"] = "ETHUSDT"
    reader = GetOnlyProtectionRecovery(
        client, SimpleNamespace(inventory_issues=lambda env: []), environment="DEMO"
    )
    assert (await reader(intent, plan)).state is not ProtectionState.VENUE_PROTECTED
