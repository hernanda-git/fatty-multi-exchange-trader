from __future__ import annotations

from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue


class Client:
    def __init__(self, leverage: str = "20") -> None:
        self.leverage = leverage
        self.calls: list[str] = []

    async def set_leverage(self, symbol: str, leverage: str) -> dict[str, str]:
        self.calls.append(f"set:{symbol}:{leverage}")
        self.leverage = leverage
        return {"leverage": leverage}

    async def get_account(self, symbol: str) -> dict[str, str]:
        self.calls.append(f"account:{symbol}")
        return {
            "available": "100", "accountEquity": "100", "usdtEquity": "100",
            "marginCoin": "USDT", "marginMode": "isolated", "posMode": "one_way_mode",
            "isolatedLongLever": self.leverage, "isolatedShortLever": self.leverage,
        }


@pytest.mark.asyncio
async def test_ensure_leverage_sets_then_reads_back_exact_planned_value() -> None:
    client = Client("10")
    venue = AsyncBitgetVenue(client)

    account = await venue.ensure_leverage("BTCUSDT", 20)

    assert account.long_leverage == Decimal("20")
    assert account.short_leverage == Decimal("20")
    assert client.calls == ["set:BTCUSDT:20", "account:BTCUSDT"]


@pytest.mark.asyncio
async def test_ensure_leverage_rejects_mismatch_before_order_path() -> None:
    class MismatchClient(Client):
        async def set_leverage(self, symbol: str, leverage: str) -> dict[str, str]:
            self.calls.append(f"set:{symbol}:{leverage}")
            return {"leverage": leverage}

    client = MismatchClient("10")
    venue = AsyncBitgetVenue(client)

    with pytest.raises(ValueError, match="planned leverage"):
        await venue.ensure_leverage("BTCUSDT", 20, propagation_delay_seconds=0)
