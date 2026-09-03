"""Read-only public market data for Binance USD-M Futures testnet."""

from __future__ import annotations

from decimal import Decimal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class BinanceMarketDataError(ValueError):
    """Raised when a public Binance response cannot be trusted."""


class BinanceFuturesSymbolMetadata(BaseModel):
    """Validated metadata required to safely size a BTCUSDT perpetual order."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    contract_type: str
    status: str
    quantity_precision: int = Field(ge=0)
    price_precision: int = Field(ge=0)
    qty_step: Decimal = Field(gt=0)
    min_qty: Decimal = Field(gt=0)
    min_notional: Decimal = Field(gt=0)
    max_leverage: int | None = None


class BinanceServerTime(BaseModel):
    """Validated public server clock response."""

    model_config = ConfigDict(frozen=True)

    server_time_ms: int = Field(gt=0)


class _ExchangeFilter(BaseModel):
    model_config = ConfigDict(extra="ignore")

    filterType: str
    minQty: Decimal | None = None
    stepSize: Decimal | None = None
    notional: Decimal | None = None


class _ExchangeSymbol(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbol: str
    contractType: str
    status: str
    quantityPrecision: int = Field(ge=0)
    pricePrecision: int = Field(ge=0)
    filters: list[_ExchangeFilter]


class _ExchangeInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbols: list[_ExchangeSymbol]


class _ServerTimeResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    serverTime: int = Field(gt=0)


class BinanceFuturesTestnetPublicMarketData:
    """Read-only adapter for Binance USD-M Futures testnet public endpoints.

    This adapter deliberately exposes no authenticated or order-submission API.
    """

    base_url = "https://testnet.binancefuture.com"

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if transport is not None and client is not None:
            raise ValueError("provide either transport or client, not both")
        self._client = client or httpx.AsyncClient(base_url=self.base_url, transport=transport)
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close only a client created by this adapter."""
        if self._owns_client:
            await self._client.aclose()

    async def get_btcusdt_metadata(self) -> BinanceFuturesSymbolMetadata:
        """Fetch and fail-close validate active BTCUSDT perpetual metadata."""
        response = await self._client.get(f"{self.base_url}/fapi/v1/exchangeInfo")
        response.raise_for_status()
        try:
            payload = _ExchangeInfo.model_validate(response.json())
        except (ValidationError, ValueError) as error:
            raise BinanceMarketDataError("invalid Binance exchange-info response") from error

        symbol = next((item for item in payload.symbols if item.symbol == "BTCUSDT"), None)
        if symbol is None:
            raise BinanceMarketDataError("BTCUSDT metadata is missing")
        if symbol.contractType != "PERPETUAL" or symbol.status != "TRADING":
            raise BinanceMarketDataError("BTCUSDT is not an active perpetual contract")

        lot_size = next((item for item in symbol.filters if item.filterType == "LOT_SIZE"), None)
        min_notional = next(
            (item for item in symbol.filters if item.filterType == "MIN_NOTIONAL"), None
        )
        if (
            lot_size is None
            or lot_size.minQty is None
            or lot_size.stepSize is None
            or min_notional is None
            or min_notional.notional is None
        ):
            raise BinanceMarketDataError("BTCUSDT sizing metadata is incomplete")

        try:
            return BinanceFuturesSymbolMetadata(
                symbol=symbol.symbol,
                contract_type=symbol.contractType,
                status=symbol.status,
                quantity_precision=symbol.quantityPrecision,
                price_precision=symbol.pricePrecision,
                qty_step=lot_size.stepSize,
                min_qty=lot_size.minQty,
                min_notional=min_notional.notional,
            )
        except ValidationError as error:
            raise BinanceMarketDataError("BTCUSDT sizing metadata is invalid") from error

    async def get_server_time(self) -> BinanceServerTime:
        """Fetch and validate Binance testnet's public server clock."""
        response = await self._client.get(f"{self.base_url}/fapi/v1/time")
        response.raise_for_status()
        try:
            payload = _ServerTimeResponse.model_validate(response.json())
        except (ValidationError, ValueError) as error:
            raise BinanceMarketDataError("invalid Binance server-time response") from error
        return BinanceServerTime(server_time_ms=payload.serverTime)
