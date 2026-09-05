from __future__ import annotations

from decimal import Decimal
from typing import Any

from fatty_trader.exchanges.bitget.client import BitgetUnknownResultError
from fatty_trader.exchanges.bitget.live import (
    InMemoryLiveIntentStore,
    LiveEntryRequest,
    LiveOrderStatus,
    enter_live_position,
)
from fatty_trader.risk.sizing import SymbolMetadata


class RestartVenue:
    def __init__(self) -> None:
        self.posts = 0
        self.orders: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []
        self.timeout_after_post = True

    def get_available_balance(self) -> Decimal:
        return Decimal("1000")

    def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return []

    def get_symbol_metadata(self, symbol: str) -> SymbolMetadata:
        return SymbolMetadata(
            symbol=symbol,
            price_precision=2,
            price_tick=Decimal("0.01"),
            size_step=Decimal("0.001"),
            min_order_qty=Decimal("0.001"),
            contract_value=Decimal("1"),
            max_leverage=50,
            min_notional=Decimal("5"),
        )

    def get_current_price(self, symbol: str) -> Decimal:
        return Decimal("60000")

    def get_position_mode(self) -> str:
        return "one_way"

    def get_margin_mode(self, symbol: str) -> str:
        return "isolated"

    def get_leverage(self, symbol: str) -> str:
        return "20"

    def set_margin_mode(self, symbol: str, mode: str) -> None:
        assert mode == "isolated"

    def set_leverage(self, symbol: str, leverage: str) -> None:
        assert leverage == "20"

    def place_entry_order(self, **kwargs: Any) -> dict[str, Any]:
        self.posts += 1
        order = {
            "orderId": "provider-1",
            "clientOid": kwargs["client_oid"],
            "status": "filled",
            "size": str(kwargs["quantity"]),
        }
        self.orders.append(order)
        self.fills.append(
            {
                "fillId": "fill-1",
                "orderId": "provider-1",
                "size": str(kwargs["quantity"]),
                "price": "60000",
                "fee": "0.3",
            }
        )
        if self.timeout_after_post:
            raise BitgetUnknownResultError("timeout after provider accepted POST")
        return order

    def get_order_detail(self, symbol: str, client_oid: str) -> dict[str, Any]:
        return next(
            (order for order in self.orders if order["clientOid"] == client_oid),
            {"status": "unknown"},
        )

    def get_fills(self, symbol: str, client_oid: str) -> list[dict[str, Any]]:
        return list(self.fills)

    def place_protection_orders(self, **kwargs: Any) -> Any:
        from fatty_trader.execution.protection import ProtectionConfirmation

        return ProtectionConfirmation(sl_order_id="sl-1", tp_order_ids=[], confirmed=True)

    def read_protection_state(self, **kwargs: Any) -> Any:
        from fatty_trader.execution.protection import ProtectionReport, ProtectionState

        return ProtectionReport(ProtectionState.VENUE_PROTECTED, Decimal("0.01"))


def test_timeout_then_restart_reconciles_by_symbol_without_a_second_post() -> None:
    venue = RestartVenue()
    store = InMemoryLiveIntentStore()
    request = LiveEntryRequest(
        symbol="BTCUSDT",
        side="BUY",
        quantity=Decimal("0.01"),
        leverage=20,
        client_oid="restart-oid",
    )

    first = enter_live_position(venue, store, request)
    assert first.status is LiveOrderStatus.FILLED
    assert venue.posts == 1

    restarted = enter_live_position(venue, store, request)
    assert restarted.status is LiveOrderStatus.FILLED
    assert restarted.filled_qty == Decimal("0.01")
    assert venue.posts == 1
