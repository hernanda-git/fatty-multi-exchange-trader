"""Offline tests for the latency-aware liquidation buffer."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fatty_trader.domain.enums import Direction
from fatty_trader.risk.liquidation import check_sl_before_liquidation


def test_long_stop_must_clear_percentage_gap_from_liquidation() -> None:
    assert (
        check_sl_before_liquidation(
            direction=Direction.LONG,
            entry=Decimal("100"),
            stop_loss=Decimal("98"),
            liquidation_price=Decimal("95"),
            buffer=Decimal("0.10"),
            minimum_gap_pct=Decimal("0.02"),
        )
        is True
    )
    assert (
        check_sl_before_liquidation(
            direction=Direction.LONG,
            entry=Decimal("100"),
            stop_loss=Decimal("95.5"),
            liquidation_price=Decimal("95"),
            buffer=Decimal("0.10"),
            minimum_gap_pct=Decimal("0.02"),
        )
        is False
    )


def test_short_stop_uses_the_inverse_latency_gap() -> None:
    assert (
        check_sl_before_liquidation(
            direction=Direction.SHORT,
            entry=Decimal("100"),
            stop_loss=Decimal("102"),
            liquidation_price=Decimal("105"),
            buffer=Decimal("0.10"),
            minimum_gap_pct=Decimal("0.02"),
        )
        is True
    )
    assert (
        check_sl_before_liquidation(
            direction=Direction.SHORT,
            entry=Decimal("100"),
            stop_loss=Decimal("104.5"),
            liquidation_price=Decimal("105"),
            buffer=Decimal("0.10"),
            minimum_gap_pct=Decimal("0.02"),
        )
        is False
    )


def test_minimum_ticks_and_latency_allowance_are_both_enforced() -> None:
    assert (
        check_sl_before_liquidation(
            direction=Direction.LONG,
            entry=Decimal("100"),
            stop_loss=Decimal("96"),
            liquidation_price=Decimal("95"),
            buffer=Decimal("0.10"),
            minimum_ticks=20,
            price_tick=Decimal("0.05"),
            latency_slippage_allowance=Decimal("1.2"),
        )
        is False
    )
    assert (
        check_sl_before_liquidation(
            direction=Direction.LONG,
            entry=Decimal("100"),
            stop_loss=Decimal("97"),
            liquidation_price=Decimal("95"),
            buffer=Decimal("0.10"),
            minimum_ticks=20,
            price_tick=Decimal("0.05"),
            latency_slippage_allowance=Decimal("1.2"),
        )
        is True
    )


def test_tick_floor_requires_a_positive_tick_size() -> None:
    with pytest.raises(ValueError, match="price_tick"):
        check_sl_before_liquidation(
            direction=Direction.LONG,
            entry=Decimal("100"),
            stop_loss=Decimal("96"),
            liquidation_price=Decimal("95"),
            minimum_ticks=1,
            price_tick=Decimal("0"),
        )
