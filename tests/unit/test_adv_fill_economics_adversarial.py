"""Independent adversarial probes on fill pagination + durable economics (B)."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from fatty_trader.exchanges.bitget.async_execution import AsyncBitgetExecution
from fatty_trader.exchanges.bitget.client import BitgetRestClient
from fatty_trader.exchanges.bitget.reconciliation import _complete_fills, _preserve_confirmed_trades
from fatty_trader.exchanges.bitget.live import LiveIntentRecord, normalize_fill


def make_client(pages):
    """A real signed BitgetRestClient over httpx.MockTransport replaying `pages`."""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        idx = len(calls) - 1
        if idx >= len(pages):
            return httpx.Response(200, json={"fillList": [], "endId": ""})
        page = pages[idx]
        if page == "TRANSIENT-500":
            return httpx.Response(500, json={"code": "50000", "msg": "transient"})
        if page == "PERMANENT-40309":
            return httpx.Response(200, json={"code": "40309", "msg": "symbol delisted"})
        if page is None:
            return httpx.Response(200, json={"code": "00000", "data": None})
        return httpx.Response(200, json={"code": "00000", "data": page})

    client = BitgetRestClient(
        api_key="k",
        api_secret="s",
        passphrase="p",
        transport=httpx.MockTransport(handler),
    )
    return client, calls


def row(tid, qty="2", price="100", order="O1", **extra):
    base = {
        "tradeId": tid,
        "symbol": "BTCUSDT",
        "orderId": order,
        "price": price,
        "baseVolume": qty,
        "side": "buy",
        "tradeSide": "buy_single",
        "posMode": "one_way_mode",
        "cTime": "1700000000000",
        "feeDetail": [{"feeCoin": "USDT", "totalFee": "-0.1"}],
    }
    base.update(extra)
    return base


async def scan(pages, **kw):
    client, calls = make_client(pages)
    return await client.get_fills("BTCUSDT", **kw), calls


# ------------------------------------------------------------------ pagination


@pytest.mark.asyncio
async def test_b1_duplicate_tradeid_on_page2_is_rejected():
    r = await scan(
        [
            {"fillList": [row("30"), row("20")], "endId": "20"},
            {"fillList": [row("20"), row("10")], "endId": "10"},  # dup 20
        ]
    )
    rows, complete = _complete_fills(r[0])
    assert not complete, "duplicate tradeId across pages must not prove exhaustion"
    assert complete is False


@pytest.mark.asyncio
async def test_b2_endid_equal_to_last_row_instead_of_oldest_is_rejected():
    """Provider echoes newest id as endId; code requires endId == oldest."""
    r = await scan(
        [
            {"fillList": [row("30"), row("20")], "endId": "30"},
            {"fillList": [], "endId": ""},
        ]
    )
    _, complete = _complete_fills(r[0])
    assert complete is False


@pytest.mark.asyncio
async def test_b3_non_numeric_and_leading_zero_tradeids_rejected():
    for bad in ("abc", "007", "", "1.5", "-3", "0", " 12", "١٢"):
        r = await scan([{"fillList": [row("30"), row(bad)], "endId": bad}])
        _, complete = _complete_fills(r[0])
        assert complete is False, f"accepted bad tradeId {bad!r}"


@pytest.mark.asyncio
async def test_b4_descending_order_violation_rejected():
    r = await scan([{"fillList": [row("10"), row("30")], "endId": "10"}])
    _, complete = _complete_fills(r[0])
    assert complete is False


@pytest.mark.asyncio
async def test_b5_full_100_row_page_then_empty_is_complete():
    ids = [str(1000 - i) for i in range(100)]
    r = await scan(
        [
            {"fillList": [row(i) for i in ids], "endId": ids[-1]},
            {"fillList": [], "endId": ""},
        ]
    )
    rows, complete = _complete_fills(r[0])
    assert complete is True, "100-row page should paginate fine"
    assert len(rows) == 100


@pytest.mark.asyncio
async def test_b6_101_row_page_rejected():
    ids = [str(2000 - i) for i in range(101)]
    r = await scan([{"fillList": [row(i) for i in ids], "endId": ids[-1]}])
    _, complete = _complete_fills(r[0])
    assert complete is False


@pytest.mark.asyncio
async def test_b7_empty_first_page_variants():
    # endId "" -> complete
    r = await scan([{"fillList": [], "endId": ""}])
    _, c = _complete_fills(r[0])
    assert c is True
    # empty page with nonempty endId -> unproven
    r = await scan([{"fillList": [], "endId": "5"}])
    _, c = _complete_fills(r[0])
    assert c is False
    # data null -> unproven
    r = await scan([None])
    _, c = _complete_fills(r[0])
    assert c is False


@pytest.mark.asyncio
async def test_b8_missing_endid_entirely_is_unproven():
    r = await scan([{"fillList": [row("30"), row("20")]}])
    _, c = _complete_fills(r[0])
    assert c is False


@pytest.mark.asyncio
async def test_b9_max_pages_bound_is_enforced():
    pages = [{"fillList": [row(str(10**6 - n))], "endId": str(10**6 - n)} for n in range(30)]
    r = await scan(pages, max_pages=3)
    _, c = _complete_fills(r[0])
    assert c is False, "page-bound exhaustion must stay unproven"
    with pytest.raises(ValueError):
        await scan(pages, max_pages=0)
    with pytest.raises(ValueError):
        await scan(pages, max_pages=101)


@pytest.mark.asyncio
async def test_b10_provider_error_mid_pagination_RAISES_never_empty_page():
    """A mid-walk GET failure must propagate, not degrade into a partial page.

    This is the 04ceca1 fix: the old `except BitgetApiError: break` returned a
    partial/empty fill list that was indistinguishable downstream from a
    complete ledger.
    """
    from fatty_trader.exchanges.bitget.client import BitgetApiError, BitgetUnknownResultError

    with pytest.raises((BitgetApiError, BitgetUnknownResultError)):
        await scan(
            [
                {"fillList": [row("30"), row("20")], "endId": "20"},
                "PERMANENT-40309",
            ]
        )


@pytest.mark.asyncio
async def test_b10b_permanent_codes_never_retried_and_never_swallowed():
    """40309/40008 must raise after exactly ONE GET attempt (no backoff)."""
    from fatty_trader.exchanges.bitget.client import BitgetApiError, BitgetRestClient

    for code in ("40309", "40008"):
        calls = []
        slept = []

        def handler(request):
            calls.append(1)
            return httpx.Response(400, json={"code": code, "msg": "permanent"})

        async def fake_sleep(d):
            slept.append(d)

        client = BitgetRestClient(
            api_key="k",
            api_secret="s",
            passphrase="p",
            transport=httpx.MockTransport(handler),
            sleep=fake_sleep,
        )
        with pytest.raises(BitgetApiError) as exc:
            await client.get_fills("BTCUSDT")
        assert exc.value.code == code
        assert len(calls) == 1, f"{code} was retried {len(calls)}x"
        assert slept == [], f"{code} reached a backoff: {slept}"


@pytest.mark.asyncio
async def test_b10c_rate_limit_429_is_retried_with_exact_schedule():
    from fatty_trader.exchanges.bitget.client import BitgetRestClient

    calls, slept = [], []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, json={"code": "30006", "msg": "too frequent"})
        return httpx.Response(200, json={"code": "00000", "data": {"fillList": [], "endId": ""}})

    async def fake_sleep(d):
        slept.append(d)

    client = BitgetRestClient(
        api_key="k",
        api_secret="s",
        passphrase="p",
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
    )
    result = await client.get_fills("BTCUSDT")
    assert result == {"fillList": [], "endId": "", "pages": [{"fillList": [], "endId": ""}]}
    assert slept == [0.25, 0.5], slept


@pytest.mark.asyncio
async def test_b10d_30007_no_longer_retried():
    from fatty_trader.exchanges.bitget.client import BitgetApiError, BitgetRestClient

    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"code": "30007", "msg": "?"})

    async def fake_sleep(d):
        raise AssertionError("30007 must not reach a backoff")

    client = BitgetRestClient(
        api_key="k",
        api_secret="s",
        passphrase="p",
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
    )
    with pytest.raises(BitgetApiError):
        await client.get_fills("BTCUSDT")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_b10e_post_never_reaches_a_backoff():
    """No POST is retried and no POST sleeps: an ENTRY POST is single-attempt."""
    from fatty_trader.exchanges.bitget.client import (
        BitgetApiError,
        BitgetRestClient,
        BitgetUnknownResultError,
    )

    # Runtime proof, not a source-text heuristic: every POST failure mode must
    # raise on the first attempt with zero backoff sleeps.
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"code": "40309", "msg": "denied"})

    async def fake_sleep(d):
        raise AssertionError("a POST reached a backoff")

    client = BitgetRestClient(
        api_key="k",
        api_secret="s",
        passphrase="p",
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
    )
    # A well-formed non-00000 error envelope is a definitive rejection and
    # legitimately raises BitgetApiError. What matters is that it is raised on
    # the FIRST attempt, with zero backoff sleeps.
    with pytest.raises(BitgetApiError) as exc:
        await client.place_entry_order(symbol="BTCUSDT", side="BUY", quantity="1", client_oid="c1")
    assert exc.value.code == "40309"
    assert len(calls) == 1, f"POST retried {len(calls)}x"


@pytest.mark.asyncio
async def test_b10f_resign_per_attempt_cannot_loop_on_40008():
    """A 40008 must not be retried, so re-signing cannot create an infinite loop."""
    from fatty_trader.exchanges.bitget.client import BitgetApiError, BitgetRestClient

    stamps = []

    def handler(request):
        stamps.append(request.headers.get("ACCESS-TIMESTAMP"))
        return httpx.Response(400, json={"code": "40008", "msg": "timestamp expired"})

    async def fake_sleep(d):
        pass

    client = BitgetRestClient(
        api_key="k",
        api_secret="s",
        passphrase="p",
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
    )
    with pytest.raises(BitgetApiError):
        await client.get_fills("BTCUSDT")
    assert len(stamps) == 1, f"40008 retried {len(stamps)}x -> resign loop risk"


@pytest.mark.asyncio
async def test_b11_endtime_is_frozen_across_pages():
    pages = [
        {"fillList": [row("30"), row("20")], "endId": "20"},
        {"fillList": [], "endId": ""},
    ]
    r = await scan(pages)
    calls = r[1]
    assert calls[0]["endTime"] == calls[1]["endTime"]
    assert calls[1]["idLessThan"] == "20"
    assert calls[0]["limit"] == "100"


@pytest.mark.asyncio
async def test_b12_caller_page_limit_cannot_be_inflated_by_provider_shape():
    # idLessThan must always be strictly older than the previous cursor
    pages = [
        {"fillList": [row("30"), row("20")], "endId": "20"},
        {"fillList": [row("25"), row("15")], "endId": "15"},  # 25 >= cursor 20
        {"fillList": [], "endId": ""},
    ]
    r = await scan(pages)
    _, c = _complete_fills(r[0])
    assert c is False


# ------------------------------------------------------------- economics shapes


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda f: f.update({"feeDetail": [{"totalFee": "abc"}]}), id="fee-nonnumeric"),
        pytest.param(lambda f: f.update({"baseVolume": "0"}), id="zero-quantity"),
        pytest.param(lambda f: f.update({"baseVolume": "-2"}), id="negative-quantity"),
        pytest.param(lambda f: f.update({"price": "0"}), id="zero-price"),
        pytest.param(lambda f: f.update({"price": "NaN"}), id="nan-price"),
        pytest.param(lambda f: f.update({"price": "Infinity"}), id="inf-price"),
        pytest.param(lambda f: f.update({"fee": "NaN"}), id="nan-fee-field"),
        pytest.param(lambda f: f.update({"feeDetail": "notjson"}), id="feeDetail-garbage"),
        pytest.param(lambda f: f.update({"feeDetail": [{"fee": None}]}), id="feeDetail-null"),
    ],
)
def test_b13_bad_economics_forces_unproven(mutate):
    f = row("30")
    mutate(f)
    payload = {"fillList": [f, row("20")], "endId": ""}
    rows, complete = _complete_fills(payload)
    assert complete is False, f"accepted bad economics: {f}"


def test_b13b_json_string_feedetail_is_parsed_not_rejected():
    """A JSON-string feeDetail is a legitimate Bitget shape and must be ACCEPTED."""
    f = row("30")
    f["feeDetail"] = json.dumps(f["feeDetail"])
    from fatty_trader.exchanges.bitget.live import summarize_fills

    _, _, fee, _ = summarize_fills([normalize_fill(f)])
    assert fee == Decimal("0.1"), "JSON-string feeDetail lost the fee"
    rows, complete = _complete_fills({"fillList": [f], "endId": ""})
    assert complete is True and len(rows) == 1


def test_b13c_negative_fee_is_legitimate_and_accepted():
    """Bitget reports fees as negative; that must NOT be treated as corruption."""
    f = row("30")
    f["feeDetail"] = [{"feeCoin": "USDT", "totalFee": "-0.25"}]
    f["fee"] = "-5"
    rows, complete = _complete_fills({"fillList": [f], "endId": ""})
    assert complete is True, "a negative but finite fee was wrongly rejected"


def test_b13d_nonfinite_fee_rejected():
    f = row("30")
    for bad in ("NaN", "Infinity", "-Infinity", "sNaN"):
        f = row("30")
        f["feeDetail"] = [{"feeCoin": "USDT", "totalFee": bad}]
        _, complete = _complete_fills({"fillList": [f], "endId": ""})
        assert complete is False, f"accepted non-finite fee {bad}"
    # A very large but finite magnitude is accepted: it is a real decimal value.
    huge = row("30")
    huge["feeDetail"] = [{"feeCoin": "USDT", "totalFee": "-1e999"}]
    _, complete = _complete_fills({"fillList": [huge], "endId": ""})
    assert complete is True


def test_b13e_idless_row_is_rejected_and_carries_no_quantity():
    """FIXED (commit c6d03b2): an id-less row is dropped and adds no quantity."""
    f = row("30")
    del f["tradeId"]
    rows, complete = _complete_fills({"fillList": [f], "endId": ""})
    assert complete is False
    assert rows == [], f"id-less row still contributes: {rows}"
    from fatty_trader.exchanges.bitget.live import summarize_fills

    qty, _, _, ids = summarize_fills(rows)
    assert qty == Decimal("0") and ids == ()


def test_b14_duplicate_tradeid_within_one_page_unproven():
    f1 = row("30")
    f2 = row("30")
    f2["price"] = "999"
    _, complete = _complete_fills({"fillList": [f1, f2], "endId": ""})
    assert complete is False


def test_b15_negative_fee_is_absolutized_not_signed():
    f = row("30")
    f["feeDetail"] = [{"feeCoin": "USDT", "totalFee": "-0.25"}]
    from fatty_trader.exchanges.bitget.live import summarize_fills

    _, _, fee, _ = summarize_fills([normalize_fill(f)])
    assert fee == Decimal("0.25")


# ------------------------------------------------- durable evidence preservation


def _intent(**kw):
    base = dict(
        exchange="bitget",
        client_oid="c1",
        symbol="BTCUSDT",
        side="BUY",
        requested_qty=Decimal("2"),
        filled_qty=Decimal("2"),
        provider_order_id="O1",
        provider_fill_ids=("30",),
        provider_fills=(normalize_fill(row("30", price="100", feeDetail=[{"totalFee": "-0.1"}])),),
    )
    base.update(kw)
    return LiveIntentRecord(**base)


def test_b16_empty_page_cannot_erase_durable_qty():
    intent = _intent()
    rows, complete = _complete_fills({"fillList": [], "endId": ""})
    merged, consistent = _preserve_confirmed_trades(intent, rows)
    assert consistent is False, "page missing a confirmed trade must be inconsistent"
    from fatty_trader.exchanges.bitget.live import summarize_fills

    qty, _, _, ids = summarize_fills(merged)
    assert qty == Decimal("2"), "durable 2 was erased to 0"
    assert "30" in ids


def test_b17_changed_economics_for_same_tradeid_keeps_durable_and_flags():
    intent = _intent()
    current = [normalize_fill(row("30", price="999", feeDetail=[{"totalFee": "-9"}]))]
    merged, consistent = _preserve_confirmed_trades(intent, current)
    assert consistent is False
    from fatty_trader.exchanges.bitget.live import summarize_fills

    _, avg, _, _ = summarize_fills(merged)
    assert avg == Decimal("100"), "durable avg price rewritten by provider"


def test_b18_invented_fill_is_not_possible():
    intent = _intent()
    # provider reports an extra trade the durable record never saw, larger qty
    current = [
        normalize_fill(row("30")),
        normalize_fill(row("25", qty="5")),
    ]
    merged, consistent = _preserve_confirmed_trades(intent, current)
    from fatty_trader.exchanges.bitget.live import summarize_fills

    qty, _, _, ids = summarize_fills(merged)
    assert qty == Decimal("7")  # page-provided rows are kept as-is, not invented
    # sanity: nothing was fabricated by the helper itself
    assert len(ids) == 2


def test_b19_intent_without_previous_fills_stays_consistent():
    intent = _intent(provider_fills=(), provider_fill_ids=(), filled_qty=Decimal("0"))
    rows, complete = _complete_fills({"fillList": [row("30")], "endId": ""})
    merged, consistent = _preserve_confirmed_trades(intent, rows)
    assert consistent is True and complete is True


def test_b20_fill_missing_tradeid_does_not_satisfy_a_durable_id_floor():
    """A row with no tradeId must not be able to stand in for a confirmed trade."""
    intent = _intent()
    bad = row("30")
    del bad["tradeId"]
    rows, complete = _complete_fills({"fillList": [bad], "endId": ""})
    merged, consistent = _preserve_confirmed_trades(intent, rows)
    from fatty_trader.exchanges.bitget.live import summarize_fills

    _, _, _, ids = summarize_fills(merged)
    # The durable trade is merged back, so the ID floor holds; the contradiction
    # must instead be surfaced as consistent=False, which forces UNKNOWN upstream.
    assert set(intent.provider_fill_ids).issubset(ids)
    assert consistent is False, "id-less provider row did not force UNKNOWN"
