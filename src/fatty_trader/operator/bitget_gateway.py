"""Synchronous, fail-closed Bitget gateway for authenticated operator commands.

The Telegram command service is synchronous.  This adapter owns the narrow bridge to
Bitget's asynchronous REST client; it refuses to run from an already-active event
loop instead of silently spawning background work with an unknown lifecycle.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Coroutine, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import uuid4

from fatty_trader.exchanges.bitget.client import BitgetUnknownResultError
from fatty_trader.exchanges.bitget.live import LiveIntentRecord, LiveIntentStoreProtocol


class BitgetOperatorClient(Protocol):
    def get_ticker(self, symbol: str) -> Any: ...
    def get_account(self) -> Any: ...
    def get_all_positions(self) -> Any: ...
    def get_pending_orders(self, symbol: str | None = None) -> Any: ...
    def place_market_close(self, **kwargs: Any) -> Any: ...
    def cancel_all_orders(self) -> Any: ...
    def cancel_order(self, **kwargs: Any) -> Any: ...


def _decimal(value: object, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Bitget {field} is invalid") from exc


def _rows(value: object, field: str) -> Sequence[Mapping[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(row, Mapping) for row in value):
        raise ValueError(f"Bitget {field} response is invalid")
    return value


class BitgetOperatorGateway:
    """Provider adapter that exposes only sanitized operator DTOs.

    Every close persists its ``CLOSE`` intent before the reduce-only market POST.
    An ambiguous result remains ``reconciliation-pending``; it is never presented
    as a successful close.
    """

    def __init__(
        self,
        client: BitgetOperatorClient,
        intent_store: LiveIntentStoreProtocol,
        *,
        client_oid_factory: Callable[[], str] | None = None,
    ) -> None:
        self._client = client
        self._intent_store = intent_store
        self._client_oid_factory = client_oid_factory or (lambda: f"operator-close-{uuid4().hex}")

    @staticmethod
    def _run(value: Any) -> Any:
        if not inspect.isawaitable(value):
            return value
        if not isinstance(value, Coroutine):
            raise RuntimeError("operator gateway requires a coroutine provider client")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(value)
        value.close()
        raise RuntimeError("synchronous operator gateway cannot run inside an active event loop")

    def get_price(self, symbol: str) -> Decimal:
        payload = self._run(self._client.get_ticker(symbol.upper()))
        if not isinstance(payload, Mapping):
            raise ValueError("Bitget ticker response is invalid")
        return _decimal(payload.get("lastPr", payload.get("last")), "ticker price")

    def get_balance(self) -> Decimal:
        payload = self._run(self._client.get_account())
        if not isinstance(payload, Mapping):
            raise ValueError("Bitget account response is invalid")
        return _decimal(
            payload.get("available", payload.get("availableBalance")), "available balance"
        )

    def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        payload = self._run(self._client.get_all_positions())
        expected_symbol = symbol.upper() if symbol else None
        positions: list[dict[str, Any]] = []
        for row in _rows(payload, "positions"):
            row_symbol = str(row.get("symbol", "")).upper()
            if expected_symbol is not None and row_symbol != expected_symbol:
                continue
            size = _decimal(row.get("total", row.get("size", "0")), "position size")
            if size <= 0:
                continue
            hold_side = str(row.get("holdSide", row.get("side", ""))).lower()
            if hold_side not in {"long", "short"}:
                raise ValueError("Bitget position side is invalid")
            positions.append(
                {
                    "symbol": row_symbol,
                    "side": hold_side.upper(),
                    "size": size,
                    "entry": _decimal(
                        row.get("openPriceAvg", row.get("entryPrice", "0")), "position entry"
                    ),
                }
            )
        return positions

    def get_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        payload = self._run(self._client.get_pending_orders(symbol.upper() if symbol else None))
        orders: list[dict[str, Any]] = []
        for row in _rows(payload, "orders"):
            side = str(row.get("side", "")).upper()
            if side not in {"BUY", "SELL"}:
                raise ValueError("Bitget order side is invalid")
            orders.append(
                {
                    "symbol": str(row.get("symbol", "")).upper(),
                    "order_id": str(row.get("orderId", "")),
                    "side": side,
                    "price": _decimal(row.get("price", "0"), "order price"),
                    "size": _decimal(row.get("size", "0"), "order size"),
                }
            )
        return orders

    def open_position(self, **_: Any) -> dict[str, Any]:
        return {"error": "operator open is not enabled"}

    def cancel_order(self, target: str) -> dict[str, Any]:
        if not target.startswith("order_id="):
            return {"cancelled": target}
        order_id = target.split("=", 1)[1]
        if not order_id:
            raise ValueError("order id is required")
        self._run(self._client.cancel_order(order_id=order_id))
        return {"cancelled": order_id}

    def cancel_all(self) -> dict[str, Any]:
        response = self._run(self._client.cancel_all_orders())
        if not isinstance(response, Mapping):
            raise ValueError("Bitget cancel-all response is invalid")
        successes = response.get("successList", response.get("success", []))
        return {"count": len(successes) if isinstance(successes, list) else 0}

    def close_position(self, target: str) -> dict[str, Any]:
        if target.startswith("position_id="):
            raise ValueError("Bitget close requires a symbol")
        symbol = target.upper()
        positions = self.get_positions(symbol)
        if not positions:
            return {"closed": symbol, "state": "not-open"}
        if len(positions) != 1:
            raise ValueError("Bitget close target resolves to multiple positions")
        position = positions[0]
        close_side = "SELL" if position["side"] == "LONG" else "BUY"
        client_oid = self._client_oid_factory()
        intent = LiveIntentRecord(
            exchange="bitget",
            client_oid=client_oid,
            symbol=symbol,
            side=close_side,
            role="CLOSE",
            state="requested",
            requested_qty=position["size"],
        )
        self._intent_store.save(intent)
        try:
            submitted = self._run(
                self._client.place_market_close(
                    symbol=symbol,
                    side=close_side,
                    quantity=str(position["size"]),
                    client_oid=client_oid,
                )
            )
        except (BitgetUnknownResultError, TimeoutError):
            intent.state = "unknown"
            self._intent_store.update(intent)
            return {"closed": symbol, "state": "reconciliation-pending"}
        if not isinstance(submitted, Mapping):
            intent.state = "unknown"
            self._intent_store.update(intent)
            return {"closed": symbol, "state": "reconciliation-pending"}
        order_id = submitted.get("orderId")
        if order_id is not None:
            intent.provider_order_id = str(order_id)
        self._intent_store.update(intent)
        if self.get_positions(symbol):
            return {"closed": symbol, "state": "reconciliation-pending"}
        intent.state = "reconciled"
        self._intent_store.update(intent)
        return {"closed": symbol, "state": "closed"}

    def close_all(self) -> dict[str, Any]:
        positions = self.get_positions()
        states = [self.close_position(str(position["symbol"])) for position in positions]
        if any(state["state"] == "reconciliation-pending" for state in states):
            return {"count": len(states), "state": "reconciliation-pending"}
        return {"count": len(states), "state": "closed"}
