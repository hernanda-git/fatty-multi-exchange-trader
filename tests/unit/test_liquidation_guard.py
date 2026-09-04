"""RED tests: isolated liquidation-price estimate + SL-before-liquidation guard."""

from decimal import Decimal

import pytest

from fatty_trader.domain.enums import Direction
from fatty_trader.risk.liquidation import (
    LiquidationGuardError,
    MMTier,
    check_sl_before_liquidation,
    estimate_liquidation_price,
)

TIERS = (MMTier(upper_bound_notional=Decimal("100000"), mmr=Decimal("0.004")),)


def test_long_liquidation_below_entry_and_short_above() -> None:
    liq_long = estimate_liquidation_price(
        direction=Direction.LONG,
        entry=Decimal("100"),
        quantity=Decimal("1"),
        leverage=20,
        margin_usdt=Decimal("5"),
        mm_tiers=TIERS,
    )
    liq_short = estimate_liquidation_price(
        direction=Direction.SHORT,
        entry=Decimal("100"),
        quantity=Decimal("1"),
        leverage=20,
        margin_usdt=Decimal("5"),
        mm_tiers=TIERS,
    )
    assert liq_long < Decimal("100") < liq_short


def test_higher_mmr_moves_liq_closer_to_entry() -> None:
    loose = (MMTier(upper_bound_notional=None, mmr=Decimal("0.004")),)
    tight = (MMTier(upper_bound_notional=None, mmr=Decimal("0.025")),)
    liq_loose = estimate_liquidation_price(
        direction=Direction.LONG,
        entry=Decimal("100"),
        quantity=Decimal("1"),
        leverage=20,
        margin_usdt=Decimal("5"),
        mm_tiers=loose,
    )
    liq_tight = estimate_liquidation_price(
        direction=Direction.LONG,
        entry=Decimal("100"),
        quantity=Decimal("1"),
        leverage=20,
        margin_usdt=Decimal("5"),
        mm_tiers=tight,
    )
    assert liq_tight > liq_loose


def test_taker_fee_allowance_pushes_liq_toward_entry() -> None:
    base = estimate_liquidation_price(
        direction=Direction.LONG,
        entry=Decimal("100"),
        quantity=Decimal("1"),
        leverage=20,
        margin_usdt=Decimal("5"),
        mm_tiers=TIERS,
        taker_fee_rate=Decimal("0"),
    )
    with_fee = estimate_liquidation_price(
        direction=Direction.LONG,
        entry=Decimal("100"),
        quantity=Decimal("1"),
        leverage=20,
        margin_usdt=Decimal("5"),
        mm_tiers=TIERS,
        taker_fee_rate=Decimal("0.0006"),
    )
    assert with_fee > base


def test_missing_mm_tiers_is_hard_failure() -> None:
    with pytest.raises(LiquidationGuardError):
        estimate_liquidation_price(
            direction=Direction.LONG,
            entry=Decimal("100"),
            quantity=Decimal("1"),
            leverage=20,
            margin_usdt=Decimal("5"),
            mm_tiers=(),
        )


def test_guard_accepts_valid_geometry_with_buffer() -> None:
    assert (
        check_sl_before_liquidation(
            direction=Direction.LONG,
            entry=Decimal("100"),
            stop_loss=Decimal("97"),
            liquidation_price=Decimal("90"),
            buffer=Decimal("0.10"),
        )
        is True
    )
    assert (
        check_sl_before_liquidation(
            direction=Direction.SHORT,
            entry=Decimal("100"),
            stop_loss=Decimal("103"),
            liquidation_price=Decimal("110"),
            buffer=Decimal("0.10"),
        )
        is True
    )


def test_guard_rejects_equality_and_wrong_side() -> None:
    # Equality fails.
    assert (
        check_sl_before_liquidation(
            direction=Direction.LONG,
            entry=Decimal("100"),
            stop_loss=Decimal("90"),
            liquidation_price=Decimal("90"),
            buffer=Decimal("0.10"),
        )
        is False
    )
    # SL on the wrong side of entry fails.
    assert (
        check_sl_before_liquidation(
            direction=Direction.LONG,
            entry=Decimal("100"),
            stop_loss=Decimal("101"),
            liquidation_price=Decimal("90"),
            buffer=Decimal("0.10"),
        )
        is False
    )
    # SL beyond liquidation fails.
    assert (
        check_sl_before_liquidation(
            direction=Direction.SHORT,
            entry=Decimal("100"),
            stop_loss=Decimal("115"),
            liquidation_price=Decimal("110"),
            buffer=Decimal("0.10"),
        )
        is False
    )


def test_guard_rejects_buffer_too_tight() -> None:
    assert (
        check_sl_before_liquidation(
            direction=Direction.LONG,
            entry=Decimal("100"),
            stop_loss=Decimal("90.5"),
            liquidation_price=Decimal("90"),
            buffer=Decimal("0.10"),
        )
        is False
    )
