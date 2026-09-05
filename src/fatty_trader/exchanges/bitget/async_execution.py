"""Async Bitget entry execution with intent-first, GET-only reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue
from fatty_trader.exchanges.bitget.client import BitgetUnknownResultError
from fatty_trader.exchanges.bitget.live import (
    LiveIntentRecord,
    LiveOrderStatus,
    classify_live_order,
    summarize_fills,
)
from fatty_trader.exchanges.bitget.validation import validate_order


class AsyncBitgetExecutionClient(Protocol):
    async def place_entry_order(
        self, *, symbol: str, side: str, quantity: str, client_oid: str
    ) -> dict[str, Any]: ...

    async def get_order_detail(self, symbol: str, *, client_oid: str) -> Any: ...

    async def get_fills(self, symbol: str) -> Any: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class AsyncExecutionResult:
    client_oid: str
    status: LiveOrderStatus
    filled_qty: Decimal
    avg_price: Decimal | None
    fee: Decimal
    provider_order_id: str | None
    provider_fill_ids: tuple[str, ...]


class AsyncBitgetExecution:
    """Production async execution adapter; POST is followed only by read-back GETs."""

    def __init__(self, client: AsyncBitgetExecutionClient, venue: AsyncBitgetVenue) -> None:
        self._client = client
        self._venue = venue

    async def aclose(self) -> None:
        """Close the owned async transport exactly once through its client boundary."""
        await self._client.aclose()

    async def submit_entry(self, intent: LiveIntentRecord) -> AsyncExecutionResult:
        snapshot = await self._venue.preflight(intent.symbol)
        validate_order(
            intent.symbol,
            intent.side,
            snapshot.current_price,
            intent.requested_qty,
            snapshot.metadata,
        )
        try:
            submitted = await self._client.place_entry_order(
                symbol=intent.symbol,
                side=intent.side,
                quantity=str(intent.requested_qty),
                client_oid=intent.client_oid,
            )
        except (BitgetUnknownResultError, TimeoutError):
            return await self.reconcile_intent(intent)
        return await self.reconcile_intent(intent, submitted)

    async def reconcile_intent(
        self, intent: LiveIntentRecord, submitted: dict[str, Any] | None = None
    ) -> AsyncExecutionResult:
        detail = await self._client.get_order_detail(intent.symbol, client_oid=intent.client_oid)
        fills = await self._client.get_fills(intent.symbol)
        if not isinstance(detail, dict):
            raise ValueError("Bitget order detail response must be an object")
        if not isinstance(fills, list) or not all(isinstance(fill, dict) for fill in fills):
            raise ValueError("Bitget fills response must be a list of objects")
        typed_fills = [dict(fill) for fill in fills]
        filled_qty, avg_price, fee, fill_ids = summarize_fills(typed_fills)
        provider_order_id = detail.get("orderId")
        if provider_order_id is None and submitted is not None:
            provider_order_id = submitted.get("orderId")
        return AsyncExecutionResult(
            client_oid=intent.client_oid,
            status=classify_live_order(detail, typed_fills),
            filled_qty=filled_qty,
            avg_price=avg_price,
            fee=fee,
            provider_order_id=str(provider_order_id) if provider_order_id is not None else None,
            provider_fill_ids=fill_ids,
        )
