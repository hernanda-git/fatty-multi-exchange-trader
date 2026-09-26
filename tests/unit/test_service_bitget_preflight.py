from datetime import UTC, datetime
from decimal import Decimal

import pytest

from fatty_trader.domain.enums import MarginMode
from fatty_trader.exchanges.bitget.async_venue import BitgetPreflightSnapshot
from fatty_trader.exchanges.bitget.read_model import BitgetAccountState
from fatty_trader.risk.sizing import SymbolMetadata
from fatty_trader.service import _bitget_dispatch_preflight


class Venue:
    def __init__(self, available: str) -> None:
        self.snapshot = BitgetPreflightSnapshot(
            account=BitgetAccountState(
                available=Decimal(available),
                total_balance=Decimal(available),
                equity=Decimal(available),
                margin_coin="USDT",
                observed_at=datetime.now(UTC),
                margin_mode="isolated",
                position_mode="one_way_mode",
                long_leverage=Decimal("20"),
                short_leverage=Decimal("20"),
            ),
            position=None,
            metadata=SymbolMetadata(
                symbol="PENDLEUSDT",
                price_precision=4,
                price_tick=Decimal("0.0001"),
                size_step=Decimal("1"),
                min_order_qty=Decimal("1"),
                max_leverage=50,
            ),
            current_price=Decimal("2.32"),
        )

    async def preflight(self, symbol: str) -> BitgetPreflightSnapshot:
        assert symbol == "PENDLEUSDT"
        return self.snapshot


@pytest.mark.asyncio
async def test_zero_live_balance_is_rejected_before_pydantic_risk_config() -> None:
    preflight = _bitget_dispatch_preflight(
        Venue("0"),
        {
            "BITGET_MIN_LEVERAGE": "20",
            "BITGET_MAX_LEVERAGE": "20",
            "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
        },
    )

    with pytest.raises(ValueError, match="available USDT margin is zero"):
        await preflight("PENDLEUSDT")


@pytest.mark.asyncio
async def test_positive_live_balance_is_capped_to_one_usdt() -> None:
    preflight = _bitget_dispatch_preflight(Venue("100"), {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1"})

    spec, risk = await preflight("PENDLEUSDT")

    # 100 USDT available * 20% = 20 USDT, but the hard cap is 1 USDT.
    assert spec.symbol == "PENDLEUSDT"
    assert risk.base_margin_usdt == Decimal("1")
    assert risk.max_auto_margin_usdt == Decimal("1")
    assert risk.max_position_notional_usdt == Decimal("20")
    assert risk.margin_mode is MarginMode.ISOLATED


@pytest.mark.asyncio
async def test_env_margin_cap_and_fixed_leverage_reach_venue_risk_config() -> None:
    preflight = _bitget_dispatch_preflight(
        Venue("1000"),
        {
            "BITGET_MIN_LEVERAGE": "20",
            "BITGET_MAX_LEVERAGE": "20",
            "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
            "BITGET_ALLOCATION_PCT": "0.20",
        },
    )

    spec, risk = await preflight("PENDLEUSDT")

    assert risk.default_leverage == 20
    assert risk.max_leverage == 20
    assert risk.base_margin_usdt == Decimal("1.00")
    assert risk.max_auto_margin_usdt == Decimal("1.00")
    assert risk.max_position_notional_usdt == Decimal("20.00")
    assert spec.max_leverage == 20


@pytest.mark.asyncio
async def test_env_margin_cap_never_exceeds_cap_at_large_balance() -> None:
    preflight = _bitget_dispatch_preflight(
        Venue("100000"),
        {
            "BITGET_MIN_LEVERAGE": "20",
            "BITGET_MAX_LEVERAGE": "20",
            "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
        },
    )

    _spec, risk = await preflight("PENDLEUSDT")

    assert risk.base_margin_usdt == Decimal("1.00")
    assert risk.max_auto_margin_usdt == Decimal("1.00")


@pytest.mark.asyncio
async def test_invalid_margin_cap_is_rejected_fail_closed() -> None:
    with pytest.raises(ValueError, match="BITGET_MAX_MARGIN_PER_TRADE_USDT"):
        _bitget_dispatch_preflight(
            Venue("100"),
            {
                "BITGET_MIN_LEVERAGE": "20",
                "BITGET_MAX_LEVERAGE": "20",
                "BITGET_MAX_MARGIN_PER_TRADE_USDT": "0",
            },
        )


@pytest.mark.asyncio
async def test_negative_margin_cap_is_rejected_fail_closed() -> None:
    with pytest.raises(ValueError, match="BITGET_MAX_MARGIN_PER_TRADE_USDT"):
        _bitget_dispatch_preflight(
            Venue("100"),
            {
                "BITGET_MIN_LEVERAGE": "20",
                "BITGET_MAX_LEVERAGE": "20",
                "BITGET_MAX_MARGIN_PER_TRADE_USDT": "-1",
            },
        )


@pytest.mark.asyncio
async def test_leverage_above_twenty_is_rejected_fail_closed() -> None:
    with pytest.raises(ValueError, match="fixed at 20x"):
        _bitget_dispatch_preflight(
            Venue("100"),
            {
                "BITGET_MIN_LEVERAGE": "20",
                "BITGET_MAX_LEVERAGE": "50",
                "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
            },
        )


@pytest.mark.asyncio
async def test_missing_margin_cap_is_rejected_fail_closed() -> None:
    with pytest.raises(ValueError, match="BITGET_MAX_MARGIN_PER_TRADE_USDT is required"):
        _bitget_dispatch_preflight(
            Venue("100"),
            {"BITGET_MIN_LEVERAGE": "20", "BITGET_MAX_LEVERAGE": "20"},
        )


@pytest.mark.asyncio
async def test_blank_margin_cap_is_rejected_fail_closed() -> None:
    with pytest.raises(ValueError, match="BITGET_MAX_MARGIN_PER_TRADE_USDT is required"):
        _bitget_dispatch_preflight(
            Venue("100"),
            {
                "BITGET_MIN_LEVERAGE": "20",
                "BITGET_MAX_LEVERAGE": "20",
                "BITGET_MAX_MARGIN_PER_TRADE_USDT": "   ",
            },
        )
