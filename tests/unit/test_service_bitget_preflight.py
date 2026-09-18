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
    preflight = _bitget_dispatch_preflight(Venue("0"), {})

    with pytest.raises(ValueError, match="available USDT margin is zero"):
        await preflight("PENDLEUSDT")


@pytest.mark.asyncio
async def test_positive_live_balance_builds_positive_risk_config() -> None:
    preflight = _bitget_dispatch_preflight(Venue("100"), {})

    spec, risk = await preflight("PENDLEUSDT")

    assert spec.symbol == "PENDLEUSDT"
    assert risk.base_margin_usdt == Decimal("20.00")
    assert risk.max_auto_margin_usdt == Decimal("20.00")
    assert risk.max_position_notional_usdt == Decimal("1000.00")
    assert risk.margin_mode is MarginMode.ISOLATED
