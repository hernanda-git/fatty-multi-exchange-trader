import asyncio
from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.read_model import (
    BitgetReadModelError,
    read_account_state,
    read_position_state,
)


class Client:
    async def get_account(self, symbol: str):
        assert symbol == "BTCUSDT"
        return {
            "available": "12.34",
            "marginMode": "isolated",
            "posMode": "one_way_mode",
            "isolatedLongLever": "20",
            "isolatedShortLever": "20",
        }

    async def get_single_position(self, symbol: str):
        assert symbol == "BTCUSDT"
        return [
            {
                "symbol": "BTCUSDT",
                "holdSide": "long",
                "total": "0.01",
                "openPriceAvg": "60000",
                "marginMode": "isolated",
                "leverage": "20",
                "stopLossId": "sl-1",
                "takeProfitId": "tp-1",
            }
        ]


def test_documented_account_and_position_fields_are_normalized() -> None:
    client = Client()
    account = asyncio.run(read_account_state(client, "BTCUSDT"))
    position = asyncio.run(read_position_state(client, "BTCUSDT"))

    assert account.available == Decimal("12.34")
    assert account.margin_mode == "isolated"
    assert account.position_mode == "one_way_mode"
    assert account.long_leverage == Decimal("20")
    assert position.quantity == Decimal("0.01")
    assert position.stop_loss_id == "sl-1"
    assert position.take_profit_id == "tp-1"


def test_missing_required_account_field_is_fail_closed() -> None:
    class InvalidClient:
        async def get_account(self, symbol: str):
            return {"marginMode": "isolated"}

    with pytest.raises(BitgetReadModelError, match="available"):
        asyncio.run(read_account_state(InvalidClient(), "BTCUSDT"))


def test_zero_decimal_position_is_not_treated_as_open() -> None:
    class FlatClient:
        async def get_single_position(self, symbol: str):
            return [{"total": "0.0"}]

    assert asyncio.run(read_position_state(FlatClient(), "BTCUSDT")) is None
