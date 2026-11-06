from decimal import Decimal
import pytest

from fatty_trader.risk.price_scale import align_price_scale


def test_align_pepe_100x_multiplier_discrepancy() -> None:
    # Message #16457 real scenario: channel posted 100PEPE units, Bitget is 1PEPE
    entry, sl, tps, scale = align_price_scale(
        entry_price=Decimal("0.000502"),
        stop_loss=Decimal("0.0004902"),
        take_profits=(Decimal("0.000575"),),
        market_price=Decimal("0.00000518"),
        direction="LONG",
    )
    assert scale == Decimal("100")
    assert entry == Decimal("0.00000502")
    assert sl == Decimal("0.000004902")
    assert tps == (Decimal("0.00000575"),)


def test_align_pepe_1000x_multiplier_discrepancy() -> None:
    # Channel posted 1000PEPE units
    entry, sl, tps, scale = align_price_scale(
        entry_price=Decimal("0.00502"),
        stop_loss=Decimal("0.004902"),
        take_profits=(Decimal("0.00575"),),
        market_price=Decimal("0.00000518"),
        direction="LONG",
    )
    assert scale == Decimal("1000")
    assert entry == Decimal("0.00000502")
    assert sl == Decimal("0.000004902")
    assert tps == (Decimal("0.00000575"),)


def test_align_bonk_inverse_multiplier_discrepancy() -> None:
    # Signal posted in single BONK (0.0000185), Bitget contract is 1000BONK (0.0188)
    entry, sl, tps, scale = align_price_scale(
        entry_price=Decimal("0.0000185"),
        stop_loss=Decimal("0.0000178"),
        take_profits=(Decimal("0.0000210"),),
        market_price=Decimal("0.0188"),
        direction="LONG",
    )
    assert scale == Decimal("0.001")
    assert entry == Decimal("0.0185")
    assert sl == Decimal("0.0178")
    assert tps == (Decimal("0.0210"),)


def test_align_shib_short_multiplier_discrepancy() -> None:
    # 1000SHIB signal to single SHIB contract on Bitget
    entry, sl, tps, scale = align_price_scale(
        entry_price=Decimal("0.0182"),
        stop_loss=Decimal("0.0191"),
        take_profits=(Decimal("0.0165"),),
        market_price=Decimal("0.0000184"),
        direction="SHORT",
    )
    assert scale == Decimal("1000")
    assert entry == Decimal("0.0000182")
    assert sl == Decimal("0.0000191")
    assert tps == (Decimal("0.0000165"),)


def test_standard_coin_btc_is_not_scaled() -> None:
    entry, sl, tps, scale = align_price_scale(
        entry_price=Decimal("68000"),
        stop_loss=Decimal("67000"),
        take_profits=(Decimal("70000"),),
        market_price=Decimal("68150"),
        direction="LONG",
    )
    assert scale == Decimal("1")
    assert entry == Decimal("68000")
    assert sl == Decimal("67000")
    assert tps == (Decimal("70000"),)


def test_arbitrary_non_power_of_ten_error_is_rejected() -> None:
    # Discrepancy ratio 15x is not a power of 10
    entry, sl, tps, scale = align_price_scale(
        entry_price=Decimal("1500"),
        stop_loss=Decimal("1400"),
        take_profits=(Decimal("1700"),),
        market_price=Decimal("100"),
        direction="LONG",
    )
    assert scale == Decimal("1")
    assert entry == Decimal("1500")


def test_invalid_geometry_after_scaling_is_rejected() -> None:
    # Inverted stop loss for LONG
    entry, sl, tps, scale = align_price_scale(
        entry_price=Decimal("0.000502"),
        stop_loss=Decimal("0.000550"), # stop above entry
        take_profits=(Decimal("0.000575"),),
        market_price=Decimal("0.00000518"),
        direction="LONG",
    )
    assert scale == Decimal("1")
    assert entry == Decimal("0.000502")
