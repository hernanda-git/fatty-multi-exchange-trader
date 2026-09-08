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
    def place_market_close(
        self, *, symbol: str, side: str, quantity: str, client_oid: str
    ) -> Any: ...
    def cancel_all_orders(self, symbol: str | None = None) -> Any: ...
    def cancel_order(self, *, symbol: str, order_id: str) -> Any: ...
    def place_position_tpsl(
        self,
        *,
        symbol: str,
        hold_side: str,
        quantity: str,
        stop_loss: str | None,
        take_profit: str | None,
    ) -> Any: ...
    def aclose(self) -> Any: ...


def _decimal(value: object, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Bitget {field} is invalid") from exc


def _optional_decimal(value: object, field: str) -> Decimal | None:
    if value is None or str(value).strip() in {"", "0", "0.0"}:
        return None
    return _decimal(value, field)


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
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False

    def _run(self, value: Any) -> Any:
        if not inspect.isawaitable(value):
            return value
        if not isinstance(value, Coroutine):
            raise RuntimeError("operator gateway requires a coroutine provider client")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if self._closed:
                value.close()
                raise RuntimeError("operator gateway is closed") from None
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
            return self._loop.run_until_complete(value)
        value.close()
        raise RuntimeError("synchronous operator gateway cannot run inside an active event loop")

    def close(self) -> None:
        """Close the async HTTP client on its owning loop."""
        if self._closed:
            return
        self._closed = True
        if self._loop is None:
            return
        try:
            self._loop.run_until_complete(self._client.aclose())
        finally:
            self._loop.close()

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
                    "stop_loss": _optional_decimal(
                        row.get("stopLossTriggerPrice", row.get("presetStopLossPrice")),
                        "position stop loss",
                    ),
                    "take_profit": _optional_decimal(
                        row.get("stopSurplusTriggerPrice", row.get("presetStopSurplusPrice")),
                        "position take profit",
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
            symbol = target.upper()
            response = self._run(self._client.cancel_all_orders(symbol=symbol))
            if not isinstance(response, Mapping):
                raise ValueError("Bitget cancel-all response is invalid")
            successes = response.get("successList", response.get("success", []))
            return {
                "cancelled": symbol,
                "count": len(successes) if isinstance(successes, list) else 0,
            }
        order_id = target.split("=", 1)[1]
        if not order_id:
            raise ValueError("order id is required")
        matching = [order for order in self.get_orders() if order["order_id"] == order_id]
        if len(matching) != 1:
            raise ValueError("Bitget pending order id was not found uniquely")
        self._run(self._client.cancel_order(symbol=matching[0]["symbol"], order_id=order_id))
        return {"cancelled": order_id}

    def cancel_all(self) -> dict[str, Any]:
        response = self._run(self._client.cancel_all_orders())
        if not isinstance(response, Mapping):
            raise ValueError("Bitget cancel-all response is invalid")
        successes = response.get("successList", response.get("success", []))
        return {"count": len(successes) if isinstance(successes, list) else 0}

    def set_position_protection(
        self,
        symbol: str,
        *,
        stop_loss: Decimal | None,
        take_profit: Decimal | None,
    ) -> dict[str, Any]:
        if (stop_loss is None) == (take_profit is None):
            raise ValueError("set exactly one of stop_loss or take_profit")
        positions = self.get_positions(symbol)
        if len(positions) != 1:
            raise ValueError("Bitget protection target must resolve to exactly one open position")
        position = positions[0]
        submitted = self._run(
            self._client.place_position_tpsl(
                symbol=symbol,
                hold_side=position["side"].lower(),
                quantity=str(position["size"]),
                stop_loss=str(stop_loss) if stop_loss is not None else None,
                take_profit=str(take_profit) if take_profit is not None else None,
            )
        )
        if not isinstance(submitted, Mapping):
            return {"symbol": symbol, "state": "reconciliation-pending"}
        current = self.get_positions(symbol)
        if len(current) != 1:
            return {"symbol": symbol, "state": "reconciliation-pending"}
        current_position = current[0]
        verified = (
            current_position["stop_loss"] == stop_loss
            if stop_loss is not None
            else current_position["take_profit"] == take_profit
        )
        return {
            "symbol": symbol,
            "state": "reconciled" if verified else "reconciliation-pending",
        }

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
