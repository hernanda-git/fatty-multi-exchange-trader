from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, cast

from fatty_trader.exchanges.bitget.metadata import (
    find_contract,
    metadata_from_contract,
    mm_tiers_from_position_lever,
)
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
    async def get_position_lever(self, symbol: str) -> Any: ...
    async def get_ticker(self, symbol: str) -> Any: ...
    async def get_clock_skew_ms(self) -> int: ...
    async def set_margin_mode(self, symbol: str, margin_mode: str) -> Any: ...
    async def set_leverage(self, symbol: str, leverage: str) -> Any: ...


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

    async def active_position_count(self) -> int:
        """Count non-flat provider positions across symbols for sizing admission."""
        get_all_positions = getattr(self._client, "get_all_positions", None)
        if not callable(get_all_positions):
            raise ValueError("Bitget client cannot read all positions for admission")
        payload = await get_all_positions()
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("Bitget all-positions response must be a list")
        count = 0
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Bitget all-positions response contains invalid row")
            try:
                if abs(Decimal(str(row.get("total", "0")))) > 0:
                    count += 1
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError("Bitget all-positions response has invalid total") from exc
        return count

    async def ensure_leverage(
        self, symbol: str, planned_leverage: int, *, propagation_delay_seconds: float = 2.0
    ) -> BitgetAccountState:
        """Set and prove the exact isolated one-way leverage before an entry POST."""
        if isinstance(planned_leverage, bool) or planned_leverage < 1:
            raise ValueError("planned leverage must be a positive integer")
        if propagation_delay_seconds < 0:
            raise ValueError("leverage propagation delay must not be negative")
        set_leverage = cast(
            Callable[..., Awaitable[Any]] | None, getattr(self._client, "set_leverage", None)
        )
        if not callable(set_leverage):
            raise ValueError("Bitget client cannot set planned leverage")
        await set_leverage(symbol, leverage=str(planned_leverage))
        account = await read_account_state(self._client, symbol)
        if self._leverage_matches(account, planned_leverage):
            return account
        if propagation_delay_seconds:
            import asyncio

            await asyncio.sleep(propagation_delay_seconds)
        account = await read_account_state(self._client, symbol)
        if not self._leverage_matches(account, planned_leverage):
            raise ValueError("Bitget account planned leverage was not confirmed")
        return account

    @staticmethod
    def _leverage_matches(account: BitgetAccountState, planned_leverage: int) -> bool:
        expected = Decimal(planned_leverage)
        return (
            account.margin_mode == "isolated"
            and account.position_mode.lower() in _SUPPORTED_POSITION_MODES
            and account.long_leverage == expected
            and account.short_leverage == expected
        )

    async def preflight(self, symbol: str) -> BitgetPreflightSnapshot:
        account = await read_account_state(self._client, symbol)
        position = await read_position_state(self._client, symbol)
        if account.margin_mode != "isolated":
            # Set margin mode; Bitget returns the confirmed mode in the response.
            # Trust it, but verify with a short-delayed read-back before rejecting.
            import asyncio

            set_margin_mode = cast(
                Callable[..., Awaitable[Any]] | None,
                getattr(self._client, "set_margin_mode", None),
            )
            if not callable(set_margin_mode):
                raise ValueError("Bitget account margin mode must be isolated")
            set_result = await set_margin_mode(symbol, margin_mode="isolated")
            if (
                isinstance(set_result, dict)
                and str(set_result.get("marginMode", "")).lower() == "isolated"
            ):
                account = await read_account_state(self._client, symbol)
                if account.margin_mode != "isolated":
                    # Propagation delay: wait and re-read once before rejecting.
                    await asyncio.sleep(2.0)
                    account = await read_account_state(self._client, symbol)
                    if account.margin_mode != "isolated":
                        raise ValueError("Bitget account margin mode must be isolated")
            else:
                account = await read_account_state(self._client, symbol)
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
        # Maintenance-margin tiers are NOT in /market/contracts; without them the
        # liquidation guard rejects every live signal. Fail closed on a bad payload.
        read_position_lever = cast(
            Callable[..., Awaitable[Any]] | None,
            getattr(self._client, "get_position_lever", None),
        )
        if not callable(read_position_lever):
            raise ValueError("Bitget client cannot read position-lever MMR tiers")
        lever = await read_position_lever(symbol)
        if not isinstance(lever, list) or not lever:
            raise ValueError("Bitget position-lever response must be a non-empty list")
        metadata = metadata.model_copy(
            update={"mm_tiers": mm_tiers_from_position_lever(lever, symbol)}
        )
        ticker = await self._client.get_ticker(symbol)
        if not isinstance(ticker, dict):
            raise ValueError("Bitget ticker response must be an object")
        try:
            entries = ticker.get("data", [ticker])
            if not isinstance(entries, list) or not entries:
                raise KeyError("no ticker entries")
            price = Decimal(str(entries[0]["lastPr"]))
        except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
            raise ValueError("Bitget ticker response has invalid lastPr") from exc
        if price <= 0:
            raise ValueError("Bitget ticker price must be positive")
        return BitgetPreflightSnapshot(account, position, metadata, price)
