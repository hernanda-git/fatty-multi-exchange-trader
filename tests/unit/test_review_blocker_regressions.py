"""Regression tests for the three independent-review blockers.

Each test is written so it FAILS on the pre-fix revision and passes after.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.live import LiveIntentRecord, normalize_fill
from fatty_trader.exchanges.bitget.reconciliation import _complete_fills
from fatty_trader.exchanges.bitget.reconciliation_live import owned_opening_fill_side


def row(tid="30", qty="2", price="100", order="O1", **extra):
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
    base.update(extra)
    return base


# ---------------------------------------------- BLOCKER B3: fill identity required


def test_b3_idless_fill_row_is_not_valid_evidence():
    """B3: a row with no tradeId must not be accepted as owned evidence."""
    bad = row()
    del bad["tradeId"]
    rows, complete = _complete_fills({"fillList": [bad], "endId": ""})
    assert rows == [], f"id-less row was accepted as evidence: {rows}"
    assert complete is False, "id-less row let the page claim completeness"


def test_b3b_idless_row_alongside_valid_rows_forces_unproven():
    bad = row("30")
    del bad["tradeId"]
    rows, complete = _complete_fills({"fillList": [row("20"), bad], "endId": ""})
    assert complete is False
    assert all("tradeId" in r for r in rows), rows


def test_b3c_normal_fills_still_accepted():
    """The new identity rule must not reject genuine fills."""
    rows, complete = _complete_fills({"fillList": [row("30"), row("20", qty="1")], "endId": ""})
    assert complete is True and len(rows) == 2


def test_b3d_fillid_and_tradeid_disagree_is_still_one_identity():
    """summarize_fills prefers fillId; a row carrying both is not ambiguous.

    Documenting actual behavior: the guard requires exactly ONE identity, and
    Bitget never sends disagreeing fillId/tradeId, so this stays valid evidence
    rather than being rejected. Guarding on disagreement would reject real rows.
    """
    r = row("30")
    r["fillId"] = "77"
    rows, complete = _complete_fills({"fillList": [r], "endId": ""})
    assert complete is True and len(rows) == 1


@pytest.mark.asyncio
async def test_b3e_filled_entry_always_has_owned_fill_ids():
    """End-to-end: a FILLED result can never carry an empty owned fill-ID set."""
    from fatty_trader.exchanges.bitget.async_execution import AsyncBitgetExecution, AsyncBitgetVenue
    from fatty_trader.exchanges.bitget.live import (
        InMemoryLiveIntentStore,
        LiveOrderStatus,
    )

    class Client:
        async def get_order_detail(self, symbol, client_oid=None, **kw):
            return {
                "orderId": "O1",
                "state": "filled",
                "baseVolume": "2",
                "priceAvg": "100",
                "size": "2",
                "clientOid": "c1",
                "symbol": "BTCUSDT",
            }

        async def get_fills(self, symbol, *a, **kw):
            bad = row()
            del bad["tradeId"]
            return {"fillList": [bad], "endId": ""}

    store = InMemoryLiveIntentStore()
    ex = AsyncBitgetExecution(Client(), AsyncBitgetVenue(Client()))
    i = LiveIntentRecord(
        exchange="bitget",
        client_oid="c1",
        symbol="BTCUSDT",
        side="BUY",
        requested_qty=Decimal("2"),
        state="unknown",
    )
    store.save(i)
    r = await ex.reconcile_intent(i)
    assert r.status is not LiveOrderStatus.FILLED, f"an id-less fill row still reached FILLED: {r}"
    assert r.provider_fill_ids == (), r


# ------------------------------- BLOCKER B4: tradeSide "open" clears all guards


@pytest.mark.parametrize(
    "hostile",
    [
        pytest.param({"enterPointSource": "SYS"}, id="open+sys-source"),
        pytest.param({"enterPointSource": "sys"}, id="open+sys-source-lower"),
        pytest.param({"profit": "42"}, id="open+profit"),
        pytest.param({"profit": "-0.0001"}, id="open+tiny-profit"),
        pytest.param({"profit": "NaN"}, id="open+nan-profit"),
        pytest.param({"profit": "abc"}, id="open+malformed-profit"),
    ],
)
def test_b4_open_branch_enforces_the_same_guards(hostile):
    """B4: the legacy 'open' literal must clear posMode/source/proof too."""
    assert owned_opening_fill_side(row(tradeSide="open", **hostile), "buy") is False, (
        f"'open' bypassed the guard for {hostile}"
    )


@pytest.mark.parametrize("trade_side", ["open", "buy_single"])
def test_b4b_clean_rows_still_accepted_for_both_shapes(trade_side):
    assert owned_opening_fill_side(row(tradeSide=trade_side), "buy") is True


@pytest.mark.parametrize("pos_mode", ["one_way_mode", "hedge_mode"])
def test_b4b2_hedge_mode_open_is_a_legitimate_real_fill(pos_mode):
    """Hedge mode genuinely reports tradeSide 'open'; it must NOT be rejected.

    This is the case an over-strict posMode fix would break: rejecting it would
    refuse authentic provider evidence and fail closed into non-trading.
    """
    assert owned_opening_fill_side(row(tradeSide="open", posMode=pos_mode), "buy") is True


def test_b4b3_single_shape_still_requires_one_way_mode():
    """buy_single means buy/sell, which only exists in one-way mode."""
    assert (
        owned_opening_fill_side(row(tradeSide="buy_single", posMode="hedge_mode"), "buy") is False
    )


def test_b4c_reduce_only_rejected_on_both_shapes():
    for trade_side in ("open", "buy_single"):
        assert owned_opening_fill_side(row(tradeSide=trade_side, reduceOnly="YES"), "buy") is False


def test_b4d_unknown_trade_side_still_rejected():
    for ts in ("close", "reduce", None, "", "buy_open"):
        assert owned_opening_fill_side(row(tradeSide=ts), "buy") is False


def test_b4e_sell_intent_still_bound_to_sell():
    assert owned_opening_fill_side(row(side="sell", tradeSide="sell_single"), "sell") is True
    assert owned_opening_fill_side(row(side="sell", tradeSide="sell_single"), "buy") is False
    assert (
        owned_opening_fill_side(row(side="buy", tradeSide="buy_single", reduceOnly="YES"), "buy")
        is False
    )


def test_b4f_sell_open_cannot_prove_a_buy_intent():
    assert owned_opening_fill_side(row(side="sell", tradeSide="open"), "buy") is False


# ------------------------ BLOCKER B1: no hardcoded provider-mutation literal


def _compose() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()


def test_b1_no_mutation_gate_is_hardcoded_on_in_compose():
    """B1: every mutation knob must default CLOSED and be env-overridable."""
    text = _compose()
    offenders = [
        line.strip()
        for line in text.splitlines()
        if "MUTATIONS_ENABLED" in line and ": " in line and '"1"' in line
    ]
    assert not offenders, f"hardcoded mutation gates remain: {offenders}"


def test_b1b_source_management_mutation_gate_is_interpolated():
    out = _compose()
    line = next(
        raw.strip()
        for raw in out.splitlines()
        if "BITGET_OPERATOR_MUTATIONS_ENABLED" in raw
        and "source-management" not in raw
        and ":" in raw
        and raw.strip().startswith("BITGET")
    )
    assert line.startswith("BITGET_OPERATOR_MUTATIONS_ENABLED: ${"), line


def test_b1c_gate_defaults_closed_everywhere():
    """No MUTATIONS_ENABLED may default to 1 anywhere in Compose."""
    import re

    out = _compose()

    bad = [
        m.group(0)
        for m in re.finditer(r"^[A-Z_]*MUTATIONS_ENABLED:[^\n]*", out, re.M)
        if not re.search(r":\s*\$\{[^}]*:-0\}\s*$", m.group(0)) and '"0"' not in m.group(0)
    ]
    assert not bad, f"mutation gates not defaulting closed: {bad}"


# ------------------------------- recovery ledger integrity (B3 supporting)


def test_b3f_entry_epoch_still_requires_full_identity():
    from fatty_trader.execution.bitget_protection_recovery import _entry_position_epoch

    intent = LiveIntentRecord(
        exchange="bitget",
        client_oid="c1",
        symbol="BTCUSDT",
        side="BUY",
        role="ENTRY",
        requested_qty=Decimal("2"),
        filled_qty=Decimal("2"),
        provider_order_id="O1",
        provider_fill_ids=("30",),
        provider_fills=(normalize_fill(row()),),
    )
    assert _entry_position_epoch(intent) == "1700000000000"
    broken = LiveIntentRecord(
        exchange="bitget",
        client_oid="c1",
        symbol="BTCUSDT",
        side="BUY",
        role="ENTRY",
        requested_qty=Decimal("2"),
        filled_qty=Decimal("2"),
        provider_order_id="O1",
        provider_fill_ids=(),
        provider_fills=(normalize_fill(row()),),
    )
    assert _entry_position_epoch(broken) is None
