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


class AsyncBitgetClient(Protocol):
    async def get_account(self, symbol: str) -> Any: ...
    async def get_single_position(self, symbol: str) -> Any: ...
    async def get_contracts(self) -> Any: ...
    async def get_ticker(self, symbol: str) -> Any: ...


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
