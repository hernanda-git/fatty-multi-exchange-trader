"""Fail-closed proofs for the Bitget LIVE safety policy.

These lock in the two invariants that must never silently degrade:
  * every applicable live trade is capped at 1 USDT committed margin;
  * leverage is fixed at exactly 20x.

A missing, blank, malformed, or non-conforming value must raise at startup
rather than disable the protection.
"""

from decimal import Decimal

import pytest

from fatty_trader.domain.models import BitgetLiveRiskConfig
from fatty_trader.exchanges.bitget.metadata import mm_tiers_from_position_lever
from fatty_trader.risk.liquidation import select_mmr
from fatty_trader.service import _bitget_dispatch_preflight


def _preflight(environ):
    return _bitget_dispatch_preflight(object(), environ)


# --------------------------------------------------------------------------
# Margin cap: mandatory and fail-closed
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "environ",
    [
        pytest.param({}, id="missing"),
        pytest.param({"BITGET_MAX_MARGIN_PER_TRADE_USDT": ""}, id="empty"),
        pytest.param({"BITGET_MAX_MARGIN_PER_TRADE_USDT": "   "}, id="blank"),
    ],
)
def test_missing_or_blank_margin_cap_refuses_to_build_preflight(environ) -> None:
    """No cap means no protection, so construction must fail closed."""
    with pytest.raises(ValueError, match="BITGET_MAX_MARGIN_PER_TRADE_USDT is required"):
        _preflight(environ)


@pytest.mark.parametrize(
    "raw",
    ["abc", "NaN", "Infinity", "-Infinity", "0", "-1", "1e999999"],
)
def test_invalid_margin_cap_values_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        _preflight({"BITGET_MAX_MARGIN_PER_TRADE_USDT": raw})


def test_valid_margin_cap_builds_preflight() -> None:
    assert _preflight({"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1"}) is not None


# --------------------------------------------------------------------------
# Leverage: fixed at exactly 20x
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "environ",
    [
        pytest.param(
            {
                "BITGET_MIN_LEVERAGE": "20",
                "BITGET_MAX_LEVERAGE": "50",
                "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
            },
            id="20-50-range",
        ),
        pytest.param(
            {
                "BITGET_MIN_LEVERAGE": "20",
                "BITGET_MAX_LEVERAGE": "30",
                "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
            },
            id="20-30-range",
        ),
        pytest.param(
            {
                "BITGET_MIN_LEVERAGE": "10",
                "BITGET_MAX_LEVERAGE": "20",
                "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
            },
            id="10-20-range",
        ),
        pytest.param(
            {
                "BITGET_MIN_LEVERAGE": "50",
                "BITGET_MAX_LEVERAGE": "50",
                "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
            },
            id="50x-only",
        ),
    ],
)
def test_leverage_ranges_other_than_20x_are_rejected(environ) -> None:
    with pytest.raises(ValueError, match="fixed at 20x"):
        _preflight(environ)


def test_unset_leverage_environment_resolves_to_20x_not_50x() -> None:
    """An unset environment must be 20x, never the old 50x default.

    50 is now a startup error, so building the preflight with the leverage env
    absent proves the default resolved to 20; the contrast case pins it.
    """
    environ = {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1"}
    assert _preflight(environ) is not None
    with pytest.raises(ValueError, match="fixed at 20x"):
        _preflight({**environ, "BITGET_MAX_LEVERAGE": "50"})


def test_live_risk_config_pins_leverage_ceiling_to_20() -> None:
    config = BitgetLiveRiskConfig()
    assert config.min_leverage == 20
    assert config.max_leverage == 20


def test_live_risk_config_rejects_leverage_above_20() -> None:
    with pytest.raises(ValueError):
        BitgetLiveRiskConfig(min_leverage=20, max_leverage=50)


def test_live_risk_config_rejects_non_finite_margin_cap() -> None:
    with pytest.raises(ValueError):
        BitgetLiveRiskConfig(max_margin_per_trade_usdt=Decimal("NaN"))


# --------------------------------------------------------------------------
# MMR tiers: the previously silent signal killer
# --------------------------------------------------------------------------


def test_position_lever_rows_build_usable_mmr_tiers() -> None:
    # Shape taken from live BTCUSDT: the final tier carries a real bound, NOT an
    # `endUnit == 0` sentinel, so the widest tier has to be forced to the catch-all.
    rows = [
        {
            "symbol": "BTCUSDT",
            "level": "1",
            "startUnit": "0",
            "endUnit": "200000",
            "keepMarginRate": "0.005",
        },
        {
            "symbol": "BTCUSDT",
            "level": "2",
            "startUnit": "200000",
            "endUnit": "1200000000",
            "keepMarginRate": "0.01",
        },
    ]
    tiers = mm_tiers_from_position_lever(rows, "BTCUSDT")
    assert len(tiers) == 2
    assert tiers[-1].upper_bound_notional is None
    assert select_mmr(Decimal("1000"), tiers) == Decimal("0.005")
    assert select_mmr(Decimal("200000"), tiers) == Decimal("0.005")
    assert select_mmr(Decimal("200001"), tiers) == Decimal("0.01")
    # Far beyond the declared bounds the catch-all must still resolve.
    assert select_mmr(Decimal("999999999999"), tiers) == Decimal("0.01")


def test_position_lever_rejects_unknown_symbol() -> None:
    with pytest.raises(ValueError, match="unknown Bitget position-lever symbol"):
        mm_tiers_from_position_lever([], "BTCUSDT")


def test_position_lever_rejects_out_of_range_mmr() -> None:
    rows = [
        {
            "symbol": "BTCUSDT",
            "startUnit": "0",
            "endUnit": "0",
            "keepMarginRate": "2",
        }
    ]
    with pytest.raises(ValueError, match="keepMarginRate"):
        mm_tiers_from_position_lever(rows, "BTCUSDT")
