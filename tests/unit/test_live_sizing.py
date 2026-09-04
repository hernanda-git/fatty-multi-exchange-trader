"""RED tests: live sizing policy — metadata, allocation, cap, leverage, fallback."""

from decimal import Decimal

import pytest

from fatty_trader.domain.enums import Direction
from fatty_trader.domain.models import BitgetLiveRiskConfig
from fatty_trader.risk.liquidation import LiquidationGuardError, MMTier
from fatty_trader.risk.live_policy import LiveSizingInput, plan_live_position
from fatty_trader.risk.sizing import (
    SymbolMetadata,
    SymbolMetadataCache,
    derive_sl_tp,
    round_price_to_tick,
    round_qty_to_step,
)

TIERS = (MMTier(upper_bound_notional=Decimal("1000000"), mmr=Decimal("0.004")),)


def make_meta(**overrides: object) -> SymbolMetadata:
    base: dict[str, object] = {
        "symbol": "BTCUSDT",
        "price_precision": 1,
        "price_tick": Decimal("0.1"),
        "size_step": Decimal("0.001"),
        "min_order_qty": Decimal("0.001"),
        "contract_value": Decimal("1"),
        "max_leverage": 50,
        "min_notional": Decimal("5"),
        "mm_tiers": TIERS,
    }
    base.update(overrides)
    return SymbolMetadata(**base)  # type: ignore[arg-type]


def make_input(**overrides: object) -> LiveSizingInput:
    base: dict[str, object] = {
        "meta": make_meta(),
        "risk": BitgetLiveRiskConfig(),
        "available_usdt": Decimal("1000"),
        "entry": Decimal("100"),
        "direction": Direction.LONG,
        "active_positions": 0,
        "stop_loss": Decimal("97"),
    }
    base.update(overrides)
    return LiveSizingInput(**base)  # type: ignore[arg-type]


def test_metadata_cache_register_get_and_missing() -> None:
    cache = SymbolMetadataCache()
    cache.register(make_meta(symbol="BTCUSDT"))
    assert cache.get("BTCUSDT").symbol == "BTCUSDT"
    with pytest.raises(KeyError):
        cache.get("NOPEUSDT")


def test_tick_step_rounding_helpers() -> None:
    assert round_price_to_tick(Decimal("100.07"), Decimal("0.1")) == Decimal("100.1")
    assert round_qty_to_step(Decimal("1.2349"), Decimal("0.001")) == Decimal("1.234")


def test_dynamic_allocation_margin() -> None:
    decision = plan_live_position(make_input(available_usdt=Decimal("1000")))
    assert decision.accepted is True
    assert decision.margin_usdt == Decimal("200")  # 0.20 * 1000


def test_five_position_cap_rejects_sixth() -> None:
    decision = plan_live_position(make_input(active_positions=5))
    assert decision.accepted is False
    assert "cap" in decision.reason.lower() or "5" in decision.reason


def test_leverage_search_respects_symbol_max() -> None:
    low_max = plan_live_position(make_input(meta=make_meta(max_leverage=25)))
    assert low_max.accepted is True
    assert low_max.leverage is not None and low_max.leverage <= 25
    assert low_max.leverage is not None and low_max.leverage >= 20


def test_leverage_never_exceeds_50() -> None:
    decision = plan_live_position(make_input(meta=make_meta(max_leverage=125)))
    assert decision.accepted is True
    assert decision.leverage is not None and decision.leverage <= 50


def test_all_in_fallback_only_when_zero_active() -> None:
    # Dust balance: 20% allocation cannot meet min notional at any leverage
    # in [20, 50] (0.01 USDT margin -> max 0.5 USDT notional < 5 USDT min),
    # and even the full 0.05 USDT balance all-in (max 2.5 USDT) cannot.
    dust_zero = plan_live_position(make_input(available_usdt=Decimal("0.05"), active_positions=0))
    assert dust_zero.accepted is False  # even all-in can't meet 5 USDT
    assert dust_zero.fallback_used is True

    dust_busy = plan_live_position(make_input(available_usdt=Decimal("0.05"), active_positions=2))
    assert dust_busy.accepted is False
    assert dust_busy.fallback_used is False


def test_all_in_fallback_saves_zero_active_position() -> None:
    # Allocation gives 1 USDT margin (max 50 notional at lev 50); min_notional
    # 60 needs the full 5 USDT balance all-in (5*20=100 >= 60).
    meta = make_meta(min_notional=Decimal("60"))
    skipped = plan_live_position(
        make_input(meta=meta, available_usdt=Decimal("5"), active_positions=2)
    )
    assert skipped.accepted is False
    assert skipped.fallback_used is False
    saved = plan_live_position(
        make_input(meta=meta, available_usdt=Decimal("5"), active_positions=0)
    )
    assert saved.accepted is True
    assert saved.fallback_used is True
    assert saved.margin_usdt == Decimal("5")


def test_sl_guard_rejection_skips_position() -> None:
    decision = plan_live_position(make_input(stop_loss=Decimal("50")))
    assert decision.accepted is False
    assert "sl" in decision.reason.lower() or "liquidation" in decision.reason.lower()


def test_missing_mm_tiers_raises() -> None:
    meta = make_meta(mm_tiers=())
    with pytest.raises(LiquidationGuardError):
        plan_live_position(make_input(meta=meta))


def test_derive_sl_tp_deterministic_fallback() -> None:
    sl1, tp1 = derive_sl_tp(Decimal("100"), Direction.LONG, Decimal("2"))
    sl2, tp2 = derive_sl_tp(Decimal("100"), Direction.LONG, Decimal("2"))
    assert (sl1, tp1) == (sl2, tp2)
    assert sl1 < Decimal("100") < tp1
    ssl, stp = derive_sl_tp(Decimal("100"), Direction.SHORT, Decimal("2"))
    assert stp < Decimal("100") < ssl


def test_signal_without_sl_uses_atr_fallback() -> None:
    decision = plan_live_position(
        make_input(stop_loss=None, atr=Decimal("2"), entry=Decimal("100"))
    )
    assert decision.accepted is True
    assert decision.stop_loss is not None


def test_signal_without_sl_or_atr_skips() -> None:
    decision = plan_live_position(make_input(stop_loss=None, atr=None))
    assert decision.accepted is False
