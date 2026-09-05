"""TDD coverage for the production async Bitget execution adapter (fakes only)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from fatty_trader.exchanges.bitget.async_execution import AsyncBitgetExecution
from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue
from fatty_trader.exchanges.bitget.client import BitgetUnknownResultError
from fatty_trader.exchanges.bitget.live import LiveIntentRecord, LiveOrderStatus


class FakeAsyncClient:
    def __init__(self) -> None:
        self.entry_calls: list[dict[str, Any]] = []

    async def get_account(self, symbol: str) -> dict[str, str]:
        return {
            "available": "100",
            "marginMode": "isolated",
            "posMode": "one_way_mode",
            "isolatedLongLever": "20",
            "isolatedShortLever": "20",
        }

    async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
        return []

    async def get_contracts(self) -> list[dict[str, str]]:
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

    async def get_ticker(self, symbol: str) -> dict[str, str]:
        return {"lastPr": "50000.00"}

    async def get_clock_skew_ms(self) -> int:
        return 0

    async def place_entry_order(self, **kwargs: str) -> dict[str, str]:
        self.entry_calls.append(kwargs)
        return {"orderId": "provider-1", "clientOid": kwargs["client_oid"]}

    async def get_order_detail(self, symbol: str, *, client_oid: str) -> dict[str, str]:
        return {"status": "filled", "requestedQty": "0.001", "orderId": "provider-1"}

    async def get_fills(self, symbol: str) -> list[dict[str, str]]:
        return [{"fillId": "fill-1", "price": "50000", "size": "0.001", "fee": "0.2"}]

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_adapter_closes_the_owned_rest_client() -> None:
    client = FakeAsyncClient()
    client.closed = False
    await AsyncBitgetExecution(client, AsyncBitgetVenue(client)).aclose()
    assert client.closed is True


@pytest.mark.asyncio
async def test_submit_entry_preflights_serializes_decimals_and_reconciles_readback() -> None:
    client = FakeAsyncClient()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))
    intent = LiveIntentRecord(
        exchange="bitget",
        client_oid="live-bitget-BTCUSDT-0011223344556677",
        symbol="BTCUSDT",
        side="BUY",
        requested_qty=Decimal("0.001"),
    )

    result = await adapter.submit_entry(intent)

    assert client.entry_calls == [
        {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "quantity": "0.001",
            "client_oid": "live-bitget-BTCUSDT-0011223344556677",
        }
    ]
    assert result.status is LiveOrderStatus.FILLED
    assert result.filled_qty == Decimal("0.001")
    assert result.avg_price == Decimal("50000")
    assert result.fee == Decimal("0.2")
    assert result.provider_order_id == "provider-1"
    assert result.provider_fill_ids == ("fill-1",)


@pytest.mark.asyncio
async def test_unknown_post_result_reconciles_with_symbol_reads_without_a_second_post() -> None:
    class TimeoutClient(FakeAsyncClient):
        async def place_entry_order(self, **kwargs: str) -> dict[str, str]:
            self.entry_calls.append(kwargs)
            raise BitgetUnknownResultError("POST result unknown")

    client = TimeoutClient()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))
    intent = LiveIntentRecord(
        exchange="bitget",
        client_oid="live-bitget-BTCUSDT-0011223344556677",
        symbol="BTCUSDT",
        side="BUY",
        requested_qty=Decimal("0.001"),
    )

    result = await adapter.submit_entry(intent)

    assert len(client.entry_calls) == 1
    assert result.status is LiveOrderStatus.FILLED
