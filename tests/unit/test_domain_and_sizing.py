from decimal import Decimal

import pytest

from fatty_trader.domain.enums import Direction, DispatchState, Exchange, MarginMode
from fatty_trader.domain.models import CanonicalSignal, InstrumentSpec, VenueRiskConfig
from fatty_trader.domain.state_machines import TransitionError, transition_dispatch
from fatty_trader.risk.sizing import SizingError, minimum_safe_plan


def valid_signal() -> CanonicalSignal:
    return CanonicalSignal(
        source_message_id=1842,
        source_revision="a" * 64,
        pair_token="BTC",
        direction=Direction.LONG,
        entry_price=Decimal("64210"),
        stop_loss=Decimal("64000"),
        take_profits=(Decimal("64630"),),
    )


def test_valid_signal_requires_stop_on_correct_side() -> None:
    signal = valid_signal()

    assert signal.pair_token == "BTC"

    with pytest.raises(ValueError, match="below entry"):
        CanonicalSignal(
            source_message_id=1,
            source_revision="b" * 64,
            pair_token="ETH",
            direction=Direction.LONG,
            entry_price=Decimal("100"),
            stop_loss=Decimal("101"),
        )


def test_dispatch_refuses_unknown_transition() -> None:
    assert (
        transition_dispatch(DispatchState.QUEUED, DispatchState.PREFLIGHT)
        is DispatchState.PREFLIGHT
    )

    with pytest.raises(TransitionError):
        transition_dispatch(DispatchState.QUEUED, DispatchState.ACTIVE)


def test_sizing_raises_leverage_before_margin() -> None:
    plan = minimum_safe_plan(
        spec=InstrumentSpec(
            exchange=Exchange.BINANCE,
            symbol="BTCUSDT",
            qty_step=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("5"),
            max_leverage=20,
        ),
        config=VenueRiskConfig(
            exchange=Exchange.BINANCE,
            base_margin_usdt=Decimal("1"),
            default_leverage=2,
            max_leverage=20,
            max_auto_margin_usdt=Decimal("5"),
            free_margin_usdt=Decimal("20"),
            free_margin_headroom_pct=Decimal("0.80"),
            max_position_notional_usdt=Decimal("100"),
            margin_mode=MarginMode.ISOLATED,
        ),
        reference_price=Decimal("100"),
    )

    assert plan.effective_leverage == 6
    assert plan.effective_margin_usdt == Decimal("1")
    assert plan.quantity == Decimal("0.051")


def test_sizing_rejects_when_minimum_margin_breaks_hard_cap() -> None:
    with pytest.raises(SizingError, match="auto margin cap"):
        minimum_safe_plan(
            spec=InstrumentSpec(
                exchange=Exchange.BITGET,
                symbol="LOWUSDT",
                qty_step=Decimal("1"),
                min_qty=Decimal("1"),
                min_notional=Decimal("50"),
                max_leverage=5,
            ),
            config=VenueRiskConfig(
                exchange=Exchange.BITGET,
                base_margin_usdt=Decimal("1"),
                default_leverage=2,
                max_leverage=5,
                max_auto_margin_usdt=Decimal("5"),
                free_margin_usdt=Decimal("20"),
                free_margin_headroom_pct=Decimal("0.80"),
                max_position_notional_usdt=Decimal("100"),
                margin_mode=MarginMode.CROSSED,
            ),
            reference_price=Decimal("1"),
        )
