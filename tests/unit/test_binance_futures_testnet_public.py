from decimal import Decimal

import httpx
import pytest

from fatty_trader.exchanges.binance.public_market_data import (
    BinanceFuturesTestnetPublicMarketData,
    BinanceMarketDataError,
)


def btcusdt_exchange_info() -> dict[str, object]:
    return {
        "timezone": "UTC",
        "serverTime": 1_725_350_400_000,
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "pair": "BTCUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "quantityPrecision": 3,
                "pricePrecision": 2,
                "filters": [
                    {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "100"},
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_fetches_typed_btcusdt_perpetual_trading_metadata_from_testnet() -> None:
    requested: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        return httpx.Response(200, json=btcusdt_exchange_info())

    adapter = BinanceFuturesTestnetPublicMarketData(transport=httpx.MockTransport(handler))

    metadata = await adapter.get_btcusdt_metadata()

    assert metadata.symbol == "BTCUSDT"
    assert metadata.contract_type == "PERPETUAL"
    assert metadata.status == "TRADING"
    assert metadata.qty_step == Decimal("0.001")
    assert metadata.min_qty == Decimal("0.001")
    assert metadata.min_notional == Decimal("100")
    assert metadata.max_leverage is None
    assert [(request.method, request.url.path) for request in requested] == [
        ("GET", "/fapi/v1/exchangeInfo")
    ]


@pytest.mark.asyncio
async def test_fetches_typed_public_server_time_from_testnet() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/fapi/v1/time"
        return httpx.Response(200, json={"serverTime": 1_725_350_400_123})

    adapter = BinanceFuturesTestnetPublicMarketData(transport=httpx.MockTransport(handler))

    server_time = await adapter.get_server_time()

    assert server_time.server_time_ms == 1_725_350_400_123


@pytest.mark.asyncio
async def test_uses_an_injected_httpx_client_without_closing_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url == "https://testnet.binancefuture.com/fapi/v1/time"
        return httpx.Response(200, json={"serverTime": 1_725_350_400_123})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = BinanceFuturesTestnetPublicMarketData(client=client)

    assert (await adapter.get_server_time()).server_time_ms == 1_725_350_400_123
    await adapter.aclose()
    assert not client.is_closed
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"symbols": []}, "BTCUSDT metadata is missing"),
        ({"symbols": [{"symbol": "BTCUSDT"}]}, "invalid Binance exchange-info response"),
        (
            {
                **btcusdt_exchange_info(),
                "symbols": [
                    {
                        **btcusdt_exchange_info()["symbols"][0],  # type: ignore[index]
                        "filters": [],
                    }
                ],
            },
            "BTCUSDT sizing metadata is incomplete",
        ),
    ],
)
async def test_rejects_missing_or_malformed_btcusdt_metadata(
    payload: dict[str, object], error: str
) -> None:
    adapter = BinanceFuturesTestnetPublicMarketData(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )

    with pytest.raises(BinanceMarketDataError, match=error):
        await adapter.get_btcusdt_metadata()


@pytest.mark.asyncio
async def test_rejects_malformed_public_server_time() -> None:
    adapter = BinanceFuturesTestnetPublicMarketData(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
    )

    with pytest.raises(BinanceMarketDataError, match="invalid Binance server-time response"):
        await adapter.get_server_time()
