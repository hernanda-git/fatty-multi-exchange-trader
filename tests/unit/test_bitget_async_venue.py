from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue


class Client:
    def __init__(
        self,
        *,
        margin_mode: str = "isolated",
        pos_mode: str = "one_way_mode",
        long_leverage: str = "20",
        short_leverage: str = "20",
        position: list[dict[str, str]] | None = None,
        clock_skew_ms: int = 0,
    ) -> None:
        self._account = {
            "available": "100",
            "marginMode": margin_mode,
            "posMode": pos_mode,
            "isolatedLongLever": long_leverage,
            "isolatedShortLever": short_leverage,
        }
        self._position = position or []
        self._clock_skew_ms = clock_skew_ms

    async def get_account(self, symbol: str):
        return self._account

    async def get_single_position(self, symbol: str):
        return self._position

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

    async def get_clock_skew_ms(self) -> int:
        return self._clock_skew_ms


@pytest.mark.asyncio
async def test_preflight_uses_documented_account_position_contract_and_price_reads() -> None:
    snapshot = await AsyncBitgetVenue(Client()).preflight("BTCUSDT")

    assert snapshot.available_balance == Decimal("100")
    assert snapshot.position is None
    assert snapshot.metadata.symbol == "BTCUSDT"
    assert snapshot.current_price == Decimal("60000")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client", "message"),
    [
        (Client(margin_mode="crossed"), "isolated"),
        (Client(pos_mode="hedge_mode"), "position mode"),
        (Client(long_leverage="20", short_leverage="10"), "leverage"),
        (Client(clock_skew_ms=30_001), "clock skew"),
        (
            Client(
                position=[
                    {
                        "symbol": "BTCUSDT",
                        "holdSide": "long",
                        "total": "0.001",
                        "openPriceAvg": "60000",
                        "marginMode": "isolated",
                        "leverage": "20",
                    }
                ]
            ),
            "active position",
        ),
    ],
)
async def test_preflight_rejects_unsafe_account_state(client: Client, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        await AsyncBitgetVenue(client).preflight("BTCUSDT")
