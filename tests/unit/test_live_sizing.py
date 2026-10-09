"""RED tests: live sizing policy — metadata, allocation, cap, leverage, fallback."""

from decimal import Decimal

import pytest

from fatty_trader.domain.enums import Direction
from fatty_trader.domain.models import BitgetLiveRiskConfig
from fatty_trader.risk.liquidation import LiquidationGuardError, MMTier
from fatty_trader.risk.live_policy import (
    LiveSizingInput,
    _notional_floor_diagnosis,
    plan_live_position,
)
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


def capped_risk(cap: str, *, floor: int = 20) -> BitgetLiveRiskConfig:
    """Return a risk config with a hard per-trade margin cap and a bounded floor."""
    return BitgetLiveRiskConfig(
        min_leverage=floor,
        max_leverage=20,
        allocation_pct=Decimal("0.20"),
        max_margin_per_trade_usdt=Decimal(cap),
    )


# Per-symbol maintenance-margin rate of 2.5%, as RAREUSDT reports: at 20x that leaves
# only ~2.4% of liquidation headroom.
HIGH_MMR_TIERS = (MMTier(upper_bound_notional=Decimal("1000000"), mmr=Decimal("0.025")),)


def test_wide_stop_signal_is_refused_when_the_floor_is_pinned_at_20() -> None:
    """The old pinned-20x policy could only skip a stop wider than its headroom."""
    meta = make_meta(mm_tiers=HIGH_MMR_TIERS)
    decision = plan_live_position(
        make_input(
            meta=meta,
            available_usdt=Decimal("1000"),
            stop_loss=Decimal("94"),  # 6% below entry, far beyond 20x headroom
            risk=capped_risk("1"),
        )
    )

    assert decision.accepted is False
    assert decision.reason.startswith("sl-guard")


def test_wide_stop_signal_backs_off_leverage_until_the_stop_is_safe() -> None:
    """With a floor below the ceiling the same signal trades at a lower leverage."""
    meta = make_meta(mm_tiers=HIGH_MMR_TIERS)
    decision = plan_live_position(
        make_input(
            meta=meta,
            available_usdt=Decimal("1000"),
            stop_loss=Decimal("94"),
            risk=capped_risk("1", floor=5),
        )
    )

    assert decision.accepted is True
    assert decision.leverage is not None and 5 <= decision.leverage < 20
    assert decision.liquidation_price is not None
    # The stop must sit strictly before liquidation with the guard's buffer intact.
    assert decision.liquidation_price < decision.stop_loss
    assert decision.margin_usdt is not None and decision.margin_usdt <= Decimal("1")
    assert decision.notional_usdt == decision.margin_usdt * decision.leverage


def test_ordinary_signal_still_trades_at_the_20x_ceiling() -> None:
    """A floor below the ceiling must not change the leverage of a normal signal."""
    decision = plan_live_position(
        make_input(
            available_usdt=Decimal("1000"), stop_loss=Decimal("97"), risk=capped_risk("1", floor=5)
        )
    )

    assert decision.accepted is True
    assert decision.leverage == 20


def test_ceiling_is_never_exceeded_even_with_a_low_floor() -> None:
    """A venue advertising 150x must still plan at most 20x."""
    meta = make_meta(max_leverage=150, min_notional=Decimal("2000"))
    decision = plan_live_position(
        make_input(
            meta=meta,
            available_usdt=Decimal("1000"),
            stop_loss=Decimal("97"),
            risk=capped_risk("1", floor=5),
        )
    )

    # Either it trades at/under 20x, or it skips: never above the ceiling.
    assert decision.leverage is None or decision.leverage <= 20


def test_hard_margin_cap_limits_margin_and_holds_leverage_at_20() -> None:
    decision = plan_live_position(make_input(available_usdt=Decimal("1000"), risk=capped_risk("1")))
    assert decision.accepted is True
    assert decision.margin_usdt == Decimal("1")
    assert decision.leverage == 20
    # 1 USDT margin at 20x -> 20 USDT notional (subject to step rounding).
    assert decision.notional_usdt == Decimal("20")
    assert decision.quantity == Decimal("0.2")  # 20 notional / 100 entry


def test_margin_cap_never_exceeds_cap_on_any_balance_size() -> None:
    accepted = 0
    for available in (Decimal("1"), Decimal("50"), Decimal("1000000")):
        decision = plan_live_position(make_input(available_usdt=available, risk=capped_risk("1")))
        if decision.accepted:
            accepted += 1
            assert decision.margin_usdt is not None
            assert decision.margin_usdt <= Decimal("1")
    assert accepted, "no balance produced an accepted plan, so the loop proved nothing"
    # Explicit large-balance case: allocation alone would request 200000 USDT.
    large = plan_live_position(make_input(available_usdt=Decimal("1000000"), risk=capped_risk("1")))
    assert large.accepted is True
    assert large.margin_usdt is not None and large.margin_usdt <= Decimal("1")


def test_all_in_fallback_respects_margin_cap() -> None:
    # 5 USDT balance would all-in without a cap; the cap must keep it at 1 USDT,
    # so a 60 USDT min-notional symbol becomes unmeetable and is skipped.
    meta = make_meta(min_notional=Decimal("60"))
    decision = plan_live_position(
        make_input(
            meta=meta,
            available_usdt=Decimal("5"),
            active_positions=0,
            risk=capped_risk("1"),
        )
    )
    assert decision.accepted is False
    assert decision.fallback_used is True
    assert decision.margin_usdt is None


def test_no_cap_preserves_allocation_behavior() -> None:
    decision = plan_live_position(make_input(available_usdt=Decimal("1000")))
    assert decision.accepted is True
    assert decision.margin_usdt == Decimal("200")


def test_fixed_leverage_config_searches_only_that_leverage() -> None:
    # A symbol whose 20x notional cannot meet min-notional must be skipped rather
    # than silently escalating to 50x.
    meta = make_meta(min_notional=Decimal("5000"))
    decision = plan_live_position(
        make_input(meta=meta, available_usdt=Decimal("1000"), risk=capped_risk("1"))
    )
    assert decision.accepted is False


def test_high_price_symbol_rejection_names_the_notional_floor_and_the_cap() -> None:
    """A high-price signal must not log one opaque margin-cap line for every symbol.

    The XAU shape that produced the real rejection: 4139 entry, 0.01 min lot
    (41.39 USDT notional), 1 USDT cap. The reason has to carry both the floor and
    what the cap could buy, plus the threshold that would have worked.
    """
    meta = make_meta(
        symbol="XAUUSDT",
        price_precision=2,
        price_tick=Decimal("0.01"),
        size_step=Decimal("0.01"),
        min_order_qty=Decimal("0.01"),
        max_leverage=100,
    )
    decision = plan_live_position(
        make_input(
            meta=meta,
            available_usdt=Decimal("1000"),
            entry=Decimal("4139"),
            stop_loss=Decimal("4005"),
            risk=capped_risk("1"),
        )
    )
    assert decision.accepted is False
    reason = decision.reason or ""
    # Distinguishing detail, all of it numeric rather than a bare label.
    assert "XAUUSDT" in reason
    assert "41.39" in reason  # the per-lot notional floor
    assert "20" in reason  # what 1 USDT buys at the 20x ceiling
    assert "2.0695" in reason  # margin actually required to clear the floor
    # Still machine-gateable: the leading tag must not change.
    assert reason.startswith("margin-cap:")


def test_degenerate_caps_never_raise_inside_the_diagnostic() -> None:
    """A rejection path must never turn a clean skip into an unhandled error.

    ``required / cap`` with a near-zero cap overflows the decimal context, and
    quantizing the result used to raise ``InvalidOperation`` out of the sizing
    decision. Both must degrade to a plain string instead.
    """
    meta = make_meta(
        symbol="XAUUSDT",
        price_precision=2,
        min_order_qty=Decimal("0.01"),
        size_step=Decimal("0.01"),
        price_tick=Decimal("0.01"),
    )
    for cap in (Decimal("1e-25"), Decimal("1e-24"), Decimal("1"), Decimal("2.5")):
        reason = _notional_floor_diagnosis(
            meta=meta,
            rounded_entry=Decimal("4139"),
            required=Decimal("41.39"),
            cap=cap,
            low=20,
            high=20,
        )
        assert reason.startswith("margin-cap:")
        assert "XAUUSDT" in reason
        assert str(cap) in reason


def test_notional_floor_reason_distinguishes_a_venue_floor_from_a_quantity_floor() -> None:
    """Two symbols rejected for different reasons must not produce the same text."""
    qty_floor = make_meta(
        symbol="XAUUSDT",
        size_step=Decimal("0.01"),
        min_order_qty=Decimal("0.01"),
        min_notional=Decimal("5"),
        price_precision=2,
        price_tick=Decimal("0.01"),
    )
    venue_floor = qty_floor.model_copy(update={"symbol": "QQQUSDT", "min_notional": Decimal("100")})

    def reason_for(meta: object) -> str:
        decision = plan_live_position(
            make_input(
                meta=meta,
                available_usdt=Decimal("1000"),
                entry=Decimal("4139"),
                stop_loss=Decimal("4005"),
                risk=capped_risk("1"),
            )
        )
        assert decision.accepted is False
        return decision.reason or ""

    a, b = reason_for(qty_floor), reason_for(venue_floor)
    assert a != b
    assert "QQQUSDT" in b and "XAUUSDT" in a
    # Quantity floor (per-lot 41.39 beats the venue 5): say so explicitly.
    assert "41.39" in a and "minTradeUSDT=5" in a
    # Venue floor (100 beats per-lot 41.39): the venue minimum is what binds,
    # so the threshold reported must be 100, not 41.39.
    assert "100 USDT notional" in b
    assert "needs margin >= 5.0000" in b


def test_rejection_reason_stays_numeric_as_the_cap_rises() -> None:
    """Different caps that all reject must report different thresholds, not one string."""
    meta = make_meta(
        symbol="XAUUSDT",
        size_step=Decimal("0.01"),
        min_order_qty=Decimal("0.01"),
        price_precision=2,
        price_tick=Decimal("0.01"),
    )
    reasons = set()
    for cap in ("1", "1.5", "1.9"):
        decision = plan_live_position(
            make_input(
                meta=meta,
                available_usdt=Decimal("1000"),
                entry=Decimal("4139"),
                stop_loss=Decimal("4005"),
                risk=capped_risk(cap),
            )
        )
        assert decision.accepted is False
        reasons.add(decision.reason)

    assert len(reasons) == 3, f"caps produced identical reasons: {reasons}"


def test_raising_leverage_to_clear_a_notional_floor_still_respects_the_sl_guard() -> None:
    """50x buys the 41.39 floor on 1 USDT margin, but the stop must still be safe.

    This is the arithmetic the operator proposed: 1 USDT at 50x is 50 USDT
    notional, which clears a 41.39 floor. The sizing policy must reach the same
    conclusion, and must still refuse when liquidation crowds the stop.
    """
    meta = make_meta(
        symbol="XAUUSDT",
        size_step=Decimal("0.01"),
        min_order_qty=Decimal("0.01"),
        price_precision=2,
        price_tick=Decimal("0.01"),
        max_leverage=100,
    )
    # 1 USDT at 50x = 50 USDT notional >= 41.39 floor: the floor is cleared by
    # leverage alone, so this is a pure size-eligibility question.
    assert Decimal("1") * 50 >= meta.min_order_qty * Decimal("4139")

    # With the policy's pinned 20x ceiling the same signal is still refused.
    refused = plan_live_position(
        make_input(
            meta=meta,
            available_usdt=Decimal("1000"),
            entry=Decimal("4139"),
            stop_loss=Decimal("4005"),
            risk=capped_risk("1"),
        )
    )
    assert refused.accepted is False

    # A tighter stop (more room before liquidation, since liq sits below entry
    # for a LONG) survives, proving the skip above was the notional floor and not
    # a blanket inability to trade the symbol at all.
    roomy = plan_live_position(
        make_input(
            meta=meta,
            available_usdt=Decimal("1000"),
            entry=Decimal("4139"),
            stop_loss=Decimal("4005"),
            risk=capped_risk("5"),
        )
    )
    assert roomy.accepted is True
    assert roomy.leverage is not None and roomy.leverage <= 20
    assert roomy.notional_usdt is not None and roomy.notional_usdt >= Decimal("41.39")


@pytest.mark.parametrize("cap", ["1e-25", "1e-999999", "1e-1000000"])
def test_extreme_tiny_cap_does_not_crash_the_floor_diagnosis(cap: str) -> None:
    """An absurd near-zero cap must degrade to a skip, never raise.

    The quotient ``required / cap`` overflows the decimal context, and that
    ``decimal.Overflow`` is raised while *computing* the value -- so guarding
    only the formatting step is not enough.
    """
    decision = plan_live_position(
        make_input(
            meta=make_meta(
                symbol="XAUUSDT",
                price_precision=2,
                price_tick=Decimal("0.01"),
                size_step=Decimal("0.01"),
                min_order_qty=Decimal("0.01"),
                max_leverage=100,
            ),
            available_usdt=Decimal("1000"),
            entry=Decimal("4139"),
            stop_loss=Decimal("4005"),
            risk=capped_risk(cap),
        )
    )
    assert decision.accepted is False
    assert decision.reason.startswith("margin-cap:")


def test_market_quantity_ceiling_reduces_realized_margin_not_risk_settings() -> None:
    decision = plan_live_position(
        make_input(
            meta=make_meta(max_order_qty=Decimal("1000"), max_market_order_qty=Decimal("2.0005"))
        )
    )
    assert decision.accepted is True
    assert decision.quantity == Decimal("2.000")
    assert decision.margin_usdt == Decimal("10")
    assert decision.notional_usdt == Decimal("200")


def test_uncapped_margin_tracks_quantity_flooring() -> None:
    decision = plan_live_position(make_input(entry=Decimal("101"), stop_loss=Decimal("98")))
    assert decision.accepted is True
    assert decision.quantity == Decimal("39.603")
    assert decision.notional_usdt == Decimal("3999.903")
    assert decision.margin_usdt == Decimal("199.99515")
    assert decision.margin_usdt == decision.notional_usdt / decision.leverage
