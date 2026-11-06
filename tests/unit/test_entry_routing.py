from datetime import UTC, datetime
from decimal import Decimal

from fatty_trader.domain.enums import Direction
from fatty_trader.execution.entry_routing import (
    EntryMode,
    LimitEntryContext,
    is_explicit_market_signal,
    route_entry,
)

def test_long_at_or_under_entry_uses_full_market_entry() -> None:
    route = route_entry(
        direction=Direction.LONG,
        signal_entry=Decimal("100"),
        market_price=Decimal("99.9"),
        total_quantity=Decimal("10"),
        late_threshold_pct=Decimal("0.005"),
    )

    assert route.mode is EntryMode.FULL_MARKET
    assert route.market_quantity == Decimal("10")
    assert route.limit_quantity == Decimal("0")


def test_long_slightly_above_entry_uses_full_market_entry() -> None:
    route = route_entry(
        direction=Direction.LONG,
        signal_entry=Decimal("100"),
        market_price=Decimal("100.4"),
        total_quantity=Decimal("10"),
        late_threshold_pct=Decimal("0.005"),
    )

    assert route.mode is EntryMode.FULL_MARKET
    assert route.market_quantity == Decimal("10")


def test_long_late_entry_splits_25_market_and_75_limit_at_signal_entry() -> None:
    route = route_entry(
        direction=Direction.LONG,
        signal_entry=Decimal("100"),
        market_price=Decimal("100.5"),
        total_quantity=Decimal("10"),
        late_threshold_pct=Decimal("0.005"),
    )

    assert route.mode is EntryMode.SPLIT_MARKET_LIMIT
    assert route.market_quantity == Decimal("2.5")
    assert route.limit_quantity == Decimal("7.5")
    assert route.limit_price == Decimal("100")


def test_short_late_entry_splits_when_market_is_below_signal_entry() -> None:
    route = route_entry(
        direction=Direction.SHORT,
        signal_entry=Decimal("100"),
        market_price=Decimal("99.5"),
        total_quantity=Decimal("10"),
        late_threshold_pct=Decimal("0.005"),
    )

    assert route.mode is EntryMode.SPLIT_MARKET_LIMIT
    assert route.market_quantity == Decimal("2.5")
    assert route.limit_quantity == Decimal("7.5")
    assert route.limit_price == Decimal("100")


def test_context_without_prior_limit_preserves_pullback_wait() -> None:
    route = route_entry(
        direction=Direction.LONG,
        signal_entry=Decimal("100"),
        market_price=Decimal("100.2"),
        total_quantity=Decimal("10"),
        late_threshold_pct=Decimal("0.005"),
        limit_context=LimitEntryContext(
            provider_acknowledged=False,
            was_working=False,
            observed_at=datetime.now(UTC),
            near_entry_mark=Decimal("100"),
            prior_mark=Decimal("100"),
        ),
    )
    assert route.mode is EntryMode.LIMIT_ONLY
    assert route.market_quantity == Decimal("0")
    assert route.limit_quantity == Decimal("10")
    assert route.limit_price == Decimal("100")


def test_context_just_departed_long_splits_near_limit() -> None:
    route = route_entry(
        direction=Direction.LONG,
        signal_entry=Decimal("100"),
        market_price=Decimal("100.2"),
        total_quantity=Decimal("10"),
        late_threshold_pct=Decimal("0.005"),
        limit_context=LimitEntryContext(
            provider_acknowledged=True,
            was_working=True,
            observed_at=datetime.now(UTC),
            near_entry_mark=Decimal("100"),
            prior_mark=Decimal("100.1"),
        ),
    )
    assert route.mode is EntryMode.NEAR_LIMIT_SPLIT_MARKET_LIMIT
    assert route.market_quantity == Decimal("2.5")
    assert route.limit_quantity == Decimal("7.5")
    assert route.limit_price == Decimal("100")


def test_context_without_prior_limit_does_not_split_far_adverse_move() -> None:
    route = route_entry(
        direction=Direction.LONG,
        signal_entry=Decimal("100"),
        market_price=Decimal("101"),
        total_quantity=Decimal("10"),
        late_threshold_pct=Decimal("0.005"),
        limit_context=LimitEntryContext(
            provider_acknowledged=False,
            was_working=False,
            observed_at=datetime.now(UTC),
            near_entry_mark=None,
            prior_mark=None,
        ),
    )
    assert route.mode is EntryMode.LIMIT_ONLY
    assert route.market_quantity == Decimal("0")


def test_is_explicit_market_signal_detects_market_phrases() -> None:
    assert is_explicit_market_signal("$SNDK longing here at daily support") is True
    assert is_explicit_market_signal("Longing $BTC here around 64000") is True
    assert is_explicit_market_signal("Shorting $WLD here around 0.385") is True
    assert is_explicit_market_signal("$ENA long here") is True
    assert is_explicit_market_signal("#Q $Q SHORT TRADE MARKET") is True
    assert is_explicit_market_signal("$PUMP placing short limit at 0.004195") is False
    assert is_explicit_market_signal("$PUMP placing short limit here at 0.004195") is False
    assert is_explicit_market_signal("#XPL $XPL LONG TRADE ENTRY: 0.098") is False
    assert is_explicit_market_signal(None) is False


def test_force_market_always_uses_full_market_and_zero_limit() -> None:
    # Even if market has crossed past entry within the recent window, force_market MUST yield FULL_MARKET.
    route = route_entry(
        direction=Direction.LONG,
        signal_entry=Decimal("100"),
        market_price=Decimal("105"),
        total_quantity=Decimal("10"),
        late_threshold_pct=Decimal("0.005"),
        source_received_at=datetime.now(UTC),
        force_market=True,
    )
    assert route.mode is EntryMode.FULL_MARKET
    assert route.market_quantity == Decimal("10")
    assert route.limit_quantity == Decimal("0")
    assert route.limit_price is None
    assert route.reason == "explicit-market-signal"
