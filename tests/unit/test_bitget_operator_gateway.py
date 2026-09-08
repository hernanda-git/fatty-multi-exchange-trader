from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from fatty_trader.exchanges.bitget.client import BitgetUnknownResultError
from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore
from fatty_trader.operator.bitget_gateway import BitgetOperatorGateway


class FakeBitgetClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.position_rows: list[dict[str, Any]] = [
            {
                "symbol": "BTCUSDT",
                "holdSide": "long",
                "total": "0.01",
                "openPriceAvg": "60000",
                "marginMode": "isolated",
                "apiSecret": "must-not-leak",
            }
        ]
        self.close_result: dict[str, Any] | Exception = {"orderId": "close-1"}

    async def get_ticker(self, symbol: str) -> dict[str, Any]:
        self.calls.append(("get_ticker", symbol))
        return {"symbol": symbol, "lastPr": "60001", "ACCESS-SIGN": "must-not-leak"}

    async def get_account(self) -> dict[str, Any]:
        self.calls.append(("get_account", None))
        return {"available": "99.5", "equity": "100", "apiKey": "must-not-leak"}

    async def get_all_positions(self) -> list[dict[str, Any]]:
        self.calls.append(("get_all_positions", None))
        return self.position_rows

    async def get_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        self.calls.append(("get_pending_orders", symbol))
        return [
            {
                "symbol": "BTCUSDT",
                "orderId": "order-1",
                "side": "buy",
                "price": "50000",
                "size": "0.01",
                "apiSecret": "must-not-leak",
            }
        ]

    async def place_market_close(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("place_market_close", kwargs))
        if isinstance(self.close_result, Exception):
            raise self.close_result
        self.position_rows = []
        return self.close_result

    async def place_position_tpsl(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("place_position_tpsl", kwargs))
        if kwargs.get("stop_loss") is not None:
            self.position_rows[0]["stopLossTriggerPrice"] = kwargs["stop_loss"]
        if kwargs.get("take_profit") is not None:
            self.position_rows[0]["stopSurplusTriggerPrice"] = kwargs["take_profit"]
        return {"stopLossId": "sl-1", "stopSurplusId": "tp-1"}

    async def cancel_all_orders(self, symbol: str | None = None) -> dict[str, Any]:
        self.calls.append(("cancel_all_orders", symbol))
        return {"successList": [{"orderId": "order-1"}]}

    async def cancel_order(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("cancel_order", kwargs))
        return {"orderId": kwargs.get("order_id", "order-1")}

    async def aclose(self) -> None:
        self.calls.append(("aclose", None))


def make_gateway() -> tuple[BitgetOperatorGateway, FakeBitgetClient, InMemoryLiveIntentStore]:
    client = FakeBitgetClient()
    store = InMemoryLiveIntentStore()
    gateway = BitgetOperatorGateway(client, store, client_oid_factory=lambda: "operator-close-1")
    return gateway, client, store


def test_read_only_methods_return_sanitized_provider_dtos() -> None:
    gateway, client, _ = make_gateway()

    assert gateway.get_price("BTCUSDT") == Decimal("60001")
    assert gateway.get_balance() == Decimal("99.5")
    assert gateway.get_positions() == [
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "size": Decimal("0.01"),
            "entry": Decimal("60000"),
            "stop_loss": None,
            "take_profit": None,
        }
    ]
    assert gateway.get_orders() == [
        {
            "symbol": "BTCUSDT",
            "order_id": "order-1",
            "side": "BUY",
            "price": Decimal("50000"),
            "size": Decimal("0.01"),
        }
    ]
    assert all("must-not-leak" not in str(value) for _, value in client.calls)


def test_gateway_reuses_one_event_loop_and_closes_client_on_that_loop() -> None:
    gateway, client, _ = make_gateway()

    gateway.get_price("BTCUSDT")
    first_loop = gateway._loop
    gateway.get_balance()
    gateway.close()

    assert first_loop is not None
    assert gateway._loop is first_loop
    assert ("aclose", None) in client.calls


def test_cancel_symbol_uses_provider_cancel_all_for_exact_symbol() -> None:
    gateway, client, _ = make_gateway()

    result = gateway.cancel_order("BTCUSDT")

    assert result == {"cancelled": "BTCUSDT", "count": 1}
    assert ("cancel_all_orders", "BTCUSDT") in client.calls


def test_cancel_order_id_resolves_provider_symbol_before_cancellation() -> None:
    gateway, client, _ = make_gateway()

    result = gateway.cancel_order("order_id=order-1")

    assert result == {"cancelled": "order-1"}
    assert ("cancel_order", {"symbol": "BTCUSDT", "order_id": "order-1"}) in client.calls


def test_close_position_persists_reduce_only_close_intent_and_reads_back_flat() -> None:
    gateway, client, store = make_gateway()

    result = gateway.close_position("BTCUSDT")

    assert result == {"closed": "BTCUSDT", "state": "closed"}
    intent = store.get("operator-close-1")
    assert intent is not None
    assert intent.role == "CLOSE"
    assert intent.side == "SELL"
    assert intent.requested_qty == Decimal("0.01")
    assert intent.state == "reconciled"
    close = next(value for name, value in client.calls if name == "place_market_close")
    assert isinstance(close, dict)
    assert close["symbol"] == "BTCUSDT"
    assert close["side"] == "SELL"
    assert close["quantity"] == "0.01"
    assert close["client_oid"] == "operator-close-1"


def test_unknown_close_result_is_reconciliation_pending_not_success() -> None:
    gateway, client, store = make_gateway()
    client.close_result = BitgetUnknownResultError("unknown")

    result = gateway.close_position("BTCUSDT")

    assert result == {"closed": "BTCUSDT", "state": "reconciliation-pending"}
    intent = store.get("operator-close-1")
    assert intent is not None
    assert intent.state == "unknown"


def test_set_stop_loss_submits_native_protection_and_reads_back() -> None:
    gateway, client, _ = make_gateway()

    result = gateway.set_position_protection(
        "BTCUSDT", stop_loss=Decimal("59900"), take_profit=None
    )

    assert result == {"symbol": "BTCUSDT", "state": "reconciled"}
    request = next(value for name, value in client.calls if name == "place_position_tpsl")
    assert request == {
        "symbol": "BTCUSDT",
        "hold_side": "long",
        "quantity": "0.01",
        "stop_loss": "59900",
        "take_profit": None,
    }
    assert gateway.get_positions()[0]["stop_loss"] == Decimal("59900")


def test_close_without_matching_open_position_makes_no_close_request() -> None:
    gateway, client, _ = make_gateway()
    client.position_rows = []

    result = gateway.close_position("BTCUSDT")

    assert result == {"closed": "BTCUSDT", "state": "not-open"}
    assert not any(name == "place_market_close" for name, _ in client.calls)


def test_gateway_rejects_calls_from_an_active_async_loop() -> None:
    gateway, _, _ = make_gateway()

    async def invoke() -> None:
        with pytest.raises(RuntimeError, match="synchronous operator gateway"):
            gateway.get_balance()

    asyncio.run(invoke())
