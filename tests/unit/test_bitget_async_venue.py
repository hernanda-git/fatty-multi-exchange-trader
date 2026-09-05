from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue


class Client:
    async def get_account(self, symbol: str):
        return {
            "available": "100",
            "marginMode": "isolated",
            "posMode": "one_way_mode",
            "isolatedLongLever": "20",
            "isolatedShortLever": "20",
        }

    async def get_single_position(self, symbol: str):
        return []

    async def get_contracts(self):
        return [
            {
                "symbol": "BTCUSDT",
                "pricePlace": "2",
                "priceEndStep": "0.01",
                "sizeMultiplier": "0.001",
                "minTradeNum": "0.001",
                "maxTradeNum": "100",
                "minTradeUSDT": "5",
                "maxLever": "50",
                "contractValue": "1",
            }
        ]

    async def get_ticker(self, symbol: str):
        return {"lastPr": "60000"}


@pytest.mark.asyncio
async def test_preflight_uses_documented_account_position_contract_and_price_reads() -> None:
    snapshot = await AsyncBitgetVenue(Client()).preflight("BTCUSDT")

    assert snapshot.available_balance == Decimal("100")
    assert snapshot.position is None
    assert snapshot.metadata.symbol == "BTCUSDT"
    assert snapshot.current_price == Decimal("60000")
