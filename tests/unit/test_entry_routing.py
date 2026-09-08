from decimal import Decimal

from fatty_trader.domain.enums import Direction
from fatty_trader.execution.entry_routing import EntryMode, route_entry


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
