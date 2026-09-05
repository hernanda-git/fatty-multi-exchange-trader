from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from fatty_trader.exchanges.bitget.metadata import find_contract, metadata_from_contract
from fatty_trader.exchanges.bitget.read_model import (
    BitgetAccountState,
    BitgetPositionState,
    read_account_state,
    read_position_state,
)
from fatty_trader.risk.sizing import SymbolMetadata

MAX_CLOCK_SKEW_MS = 30_000
_SUPPORTED_POSITION_MODES = {"one_way_mode", "one-way", "one_way"}


class AsyncBitgetClient(Protocol):
    async def get_account(self, symbol: str) -> Any: ...
    async def get_single_position(self, symbol: str) -> Any: ...
    async def get_contracts(self) -> Any: ...
    async def get_ticker(self, symbol: str) -> Any: ...
    async def get_clock_skew_ms(self) -> int: ...


@dataclass(frozen=True)
class BitgetPreflightSnapshot:
    account: BitgetAccountState
    position: BitgetPositionState | None
    metadata: SymbolMetadata
    current_price: Decimal

    @property
    def available_balance(self) -> Decimal:
        return self.account.available


class AsyncBitgetVenue:
    """Async, read-only Bitget venue boundary for production worker preflight."""

    def __init__(self, client: AsyncBitgetClient) -> None:
        self._client = client

    async def preflight(self, symbol: str) -> BitgetPreflightSnapshot:
        account = await read_account_state(self._client, symbol)
        position = await read_position_state(self._client, symbol)
        if account.margin_mode != "isolated":
            raise ValueError("Bitget account margin mode must be isolated")
        if account.position_mode.lower() not in _SUPPORTED_POSITION_MODES:
            raise ValueError("Bitget account has unsupported position mode")
        if account.long_leverage != account.short_leverage:
            raise ValueError("Bitget isolated long/short leverage must match")
        if position is not None:
            raise ValueError("Bitget symbol has an active position")
        clock_skew_ms = await self._client.get_clock_skew_ms()
        if abs(clock_skew_ms) > MAX_CLOCK_SKEW_MS:
            raise ValueError("Bitget clock skew exceeds safety limit")
        contracts = await self._client.get_contracts()
        if not isinstance(contracts, list):
            raise ValueError("Bitget contracts response must be a list")
        metadata = metadata_from_contract(find_contract(contracts, symbol))
        ticker = await self._client.get_ticker(symbol)
        if not isinstance(ticker, dict):
            raise ValueError("Bitget ticker response must be an object")
        try:
            price = Decimal(str(ticker["lastPr"]))
        except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
            raise ValueError("Bitget ticker response has invalid lastPr") from exc
        if price <= 0:
            raise ValueError("Bitget ticker price must be positive")
        return BitgetPreflightSnapshot(account, position, metadata, price)
