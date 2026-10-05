"""Adversarial probes (C): replacement-position inheritance / exit-fill adoption."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.reconciliation_live import (
    NativeProtectionExpectation,
    canonical_provider_epoch,
    owned_opening_fill_side,
)
from fatty_trader.exchanges.bitget.live import normalize_fill, summarize_fills
from fatty_trader.execution.bitget_protection_recovery import _entry_position_epoch
from fatty_trader.exchanges.bitget.live import LiveIntentRecord


# ------------------------------------------------------- owned_opening_fill_side


def fill(**kw):
    base = {
        "symbol": "BTCUSDT",
        "orderId": "O1",
        "clientOid": "c1",
        "side": "buy",
        "tradeSide": "buy_single",
        "posMode": "one_way_mode",
        "profit": "0",
        "enterPointSource": "api",
        "baseVolume": "2",
        "price": "100",
        "tradeId": "30",
        "cTime": "1700000000000",
    }
    base.update(kw)
    return base


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param({"side": "sell"}, id="sell_single-for-BUY-intent"),
        pytest.param({"tradeSide": "sell_single"}, id="sell_single-tradeSide-for-BUY"),
        pytest.param({"reduceOnly": "YES"}, id="reduce-only"),
        pytest.param({"reduceOnly": True}, id="reduce-only-bool"),
        pytest.param({"profit": "0.5"}, id="nonzero-profit"),
        pytest.param({"profit": "-0.0001"}, id="tiny-nonzero-profit"),
        pytest.param({"profit": "NaN"}, id="nan-profit"),
        pytest.param({"profit": "abc"}, id="malformed-profit"),
        pytest.param({"enterPointSource": "SYS"}, id="sys-source"),
        pytest.param({"enterPointSource": "sys"}, id="sys-source-lower"),
        pytest.param({"posMode": "hedge_mode"}, id="hedge-mode"),
        pytest.param({"posMode": None}, id="missing-posmode"),
        pytest.param({"tradeSide": "open"}, id="tradeSide-open-BYPASS?"),
        pytest.param({"tradeSide": "open", "reduceOnly": "YES"}, id="open+reduceOnly"),
        pytest.param({"tradeSide": "open", "profit": "9"}, id="open+profit"),
        pytest.param({"tradeSide": "close"}, id="tradeSide-close"),
    ],
)
def test_c1_buy_intent_rejects(mutate):
    """FIXED (commit c6d03b2): "open" now clears source/profit guards too."""
    accepted = owned_opening_fill_side(fill(**mutate), "buy")
    if mutate.get("tradeSide") == "open":
        # only a clean, fully-guarded "open" row is accepted
        clean = {"tradeSide": "open"}
        assert accepted == (mutate == clean or mutate == dict(clean, **{"profit": "0"})), (
            f"'open' bypassed the guard for {mutate}"
        )
    else:
        assert accepted is False, f"unexpected acceptance for {mutate}"


def test_c2_open_branch_no_longer_bypasses_guards():
    """FIXED (commit c6d03b2). System source and non-zero PnL are refused."""
    assert (
        owned_opening_fill_side(
            fill(tradeSide="open", posMode="hedge_mode", profit="42", enterPointSource="SYS"),
            "buy",
        )
        is False
    )
    assert (
        owned_opening_fill_side(fill(tradeSide="open", posMode=None, profit="abc"), "buy") is False
    )
    # a clean "open" row is still accepted in every observed posMode
    for pos_mode in ("one_way_mode", "hedge_mode", None):
        f = fill(tradeSide="open")
        if pos_mode is None:
            f.pop("posMode", None)
        else:
            f["posMode"] = pos_mode
        assert owned_opening_fill_side(f, "buy") is True, pos_mode


def test_c3_side_mismatch_always_rejected():
    for side in ("buy", "sell"):
        for f_side in ("buy", "sell"):
            f = fill(side=f_side, tradeSide=f_side + "_single")
            assert owned_opening_fill_side(f, side) == (f_side == side)
    # a sell_single fill can never prove a BUY intent
    assert (
        owned_opening_fill_side(
            fill(side="sell", tradeSide="sell_single", side_="x")
            if False
            else fill(side="sell", tradeSide="sell_single"),
            "buy",
        )
        is False
    )


def test_c4_sell_intent_accepts_sell_single_only():
    assert owned_opening_fill_side(fill(side="sell", tradeSide="sell_single"), "sell") is True
    assert owned_opening_fill_side(fill(side="buy", tradeSide="buy_single"), "sell") is False


# ------------------------------------------------------------------- epochs


def test_c5_epoch_token_validation():
    assert canonical_provider_epoch("1700000000000") == "1700000000000"
    for bad in ("0", "017000000000", "-1", "abc", "1700000000000.5", "", None, 1700, "1e9"):
        assert canonical_provider_epoch(bad) is None, f"accepted epoch {bad!r}"


def _intent(**kw):
    base = dict(
        exchange="bitget",
        client_oid="c1",
        symbol="BTCUSDT",
        side="BUY",
        role="ENTRY",
        requested_qty=Decimal("2"),
        filled_qty=Decimal("2"),
        provider_order_id="O1",
        provider_fill_ids=("30",),
        provider_fills=(normalize_fill(fill()),),
    )
    base.update(kw)
    return LiveIntentRecord(**base)


def test_c6_clean_owned_entry_yields_epoch():
    assert _entry_position_epoch(_intent()) == "1700000000000"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param({"orderId": "OTHER"}, id="fill-orderId-differs"),
        pytest.param({"clientOid": "other"}, id="fill-clientOid-differs"),
        pytest.param({"symbol": "ETHUSDT"}, id="fill-symbol-differs"),
        pytest.param({"side": "sell"}, id="fill-side-differs"),
        pytest.param({"tradeSide": "sell_single"}, id="exit-fill-for-BUY"),
        pytest.param({"reduceOnly": "YES"}, id="reduce-only"),
        pytest.param({"profit": "1"}, id="profit-nonzero"),
        pytest.param({"cTime": "abc"}, id="missing-ctime"),
        pytest.param({"cTime": "0169"}, id="leading-zero-ctime"),
        pytest.param({"baseVolume": "1"}, id="quantity-mismatch"),
        pytest.param({"tradeId": "31"}, id="fillid-mismatch"),
        pytest.param({"posMode": "hedge_mode"}, id="hedge-mode"),
        pytest.param({"enterPointSource": "SYS"}, id="sys-source"),
    ],
)
def test_c7_replacement_cannot_inherit(mutate):
    intent = _intent(provider_fills=(normalize_fill(fill(**mutate)),))
    assert _entry_position_epoch(intent) is None, f"inherited epoch via {mutate}"


def test_c8_tradeid_absent_from_fill_set():
    intent = _intent(provider_fills=(normalize_fill(fill(tradeId="")),))
    assert _entry_position_epoch(intent) is None


def test_c9_wrong_role_refused():
    assert _entry_position_epoch(_intent(role="CLOSE")) is None


def test_c10_no_fills_refused():
    assert _entry_position_epoch(_intent(provider_fills=(), provider_fill_ids=())) is None


def test_c11_earliest_epoch_wins_for_multi_fill():
    i = _intent(
        provider_fills=(
            normalize_fill(fill(tradeId="30", cTime="1700000000500", baseVolume="1")),
            normalize_fill(fill(tradeId="20", cTime="1700000000100", baseVolume="1")),
        ),
        provider_fill_ids=("30", "20"),
    )
    assert _entry_position_epoch(i) == "1700000000100"


def test_c12_duplicate_tradeid_across_fills_refused():
    i = _intent(
        provider_fills=(
            normalize_fill(fill(tradeId="30", baseVolume="1")),
            normalize_fill(fill(tradeId="30", baseVolume="1")),
        ),
        provider_fill_ids=("30",),
    )
    assert _entry_position_epoch(i) is None


# ------------------------------------------------- native protection epoch fence


@pytest.mark.asyncio
async def test_c13_replacement_position_epoch_mismatch_is_degraded():
    from fatty_trader.exchanges.bitget.reconciliation_live import confirm_native_protection

    exp = NativeProtectionExpectation(
        symbol="BTCUSDT",
        hold_side="buy",
        quantity=Decimal("2"),
        stop_loss=Decimal("90"),
        provider_position_epoch="1700000000000",
    )

    # position cTime differs -> replacement position, must not be adopted
    async def read_position():
        return [
            {
                "symbol": "BTCUSDT",
                "holdSide": "long",
                "total": "2",
                "marginMode": "isolated",
                "tradeSide": "long",
                "posMode": "one_way_mode",
                "cTime": "1799999999999",
            }
        ]

    async def read_plans():
        return []

    report = await confirm_native_protection(read_position, read_plans, expectation=exp)
    assert report.state.name == "DEGRADED", report
    assert "epoch" in (report.reason or ""), report


@pytest.mark.asyncio
async def test_c13b_matching_position_epoch_reaches_plan_checks():
    from fatty_trader.exchanges.bitget.reconciliation_live import confirm_native_protection

    exp = NativeProtectionExpectation(
        symbol="BTCUSDT",
        hold_side="buy",
        quantity=Decimal("2"),
        stop_loss=Decimal("90"),
        provider_position_epoch="1700000000000",
    )

    async def read_position():
        return [
            {
                "symbol": "BTCUSDT",
                "holdSide": "long",
                "total": "2",
                "marginMode": "isolated",
                "tradeSide": "long",
                "posMode": "one_way_mode",
                "cTime": "1700000000000",
            }
        ]

    async def read_plans():
        return []

    report = await confirm_native_protection(read_position, read_plans, expectation=exp)
    # must NOT be rejected for the epoch reason
    assert "epoch" not in (report.reason or ""), report


@pytest.mark.asyncio
async def test_c14_old_plan_before_epoch_rejected():
    from fatty_trader.exchanges.bitget.reconciliation_live import _verify_exact_leg

    expectation = NativeProtectionExpectation(
        symbol="BTCUSDT",
        hold_side="buy",
        quantity=Decimal("2"),
        stop_loss=Decimal("90"),
        provider_position_epoch="1700000000000",
    )
    plan = {
        "symbol": "BTCUSDT",
        "holdSide": "long",
        "planStatus": "active",
        "cTime": "1699999999999",
        "triggerPrice": "90",
        "clientOid": "c1-sl",
        "orderId": "SL1",
        "size": "2",
    }
    reason = _verify_exact_leg(
        plan,
        expectation=expectation,
        leg="stopLoss",
        trigger=Decimal("90"),
        provider_order_id="SL1",
        client_oid="c1-sl",
        expected_size=Decimal("2"),
    )
    assert reason is not None and "epoch" in reason, reason

    # A plan created at or after the position epoch passes the epoch fence.
    plan2 = dict(plan, cTime="1700000000000")
    reason2 = _verify_exact_leg(
        plan2,
        expectation=expectation,
        leg="stopLoss",
        trigger=Decimal("90"),
        provider_order_id="SL1",
        client_oid="c1-sl",
        expected_size=Decimal("2"),
    )
    assert reason2 != "stop-loss-epoch-mismatch", reason2
