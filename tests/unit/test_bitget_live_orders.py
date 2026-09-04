"""TDD: Bitget live order/protection workflow (fakes only, no network)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from fatty_trader.exchanges.bitget.live import (
    InMemoryLiveIntentStore,
    LiveEntryRequest,
    LiveOrderStatus,
    build_live_client_oid,
    enter_live_position,
)
from fatty_trader.execution.protection import ProtectionReport


class FakeLiveClient:
    """Scriptable stand-in for BitgetLiveClientProtocol."""

    def __init__(
        self,
        *,
        detail: dict[str, Any],
        fills: list[dict[str, Any]],
        place_behavior: str = "ok",
        protection_confirmed: bool = True,
    ) -> None:
        self._detail = detail
        self._fills = fills
        self._place_behavior = place_behavior
        self._protection_confirmed = protection_confirmed
        self.post_count = 0
        self.protection_qty: Decimal | None = None
        self.emergency_calls: list[dict[str, Any]] = []
        self.alerts: list[str] = []
        self.margin_set: list[str] = []
        self.leverage_set: list[str] = []
        self.calls: list[str] = []

    # -- pre-entry reads --
    def get_available_balance(self) -> Decimal:
        self.calls.append("balance")
        return Decimal("1000")

    def get_positions(self, symbol: str) -> list[dict[str, Any]]:
        self.calls.append(f"positions:{symbol}")
        return []

    def get_symbol_metadata(self, symbol: str) -> Any:
        from fatty_trader.risk.sizing import SymbolMetadata

        self.calls.append(f"metadata:{symbol}")
        return SymbolMetadata(
            symbol=symbol,
            price_precision=2,
            price_tick=Decimal("0.01"),
            size_step=Decimal("0.001"),
            min_order_qty=Decimal("0.001"),
            max_leverage=50,
        )

    def get_current_price(self, symbol: str) -> Decimal:
        self.calls.append(f"price:{symbol}")
        return Decimal("50000")

    def get_position_mode(self) -> str:
        self.calls.append("position_mode")
        return "one-way"

    def get_margin_mode(self, symbol: str) -> str:
        self.calls.append(f"margin_mode:{symbol}")
        return "isolated" if self.margin_set else "crossed"

    def get_leverage(self, symbol: str) -> str:
        self.calls.append(f"leverage:{symbol}")
        return self.leverage_set[-1] if self.leverage_set else "1"

    def set_margin_mode(self, symbol: str, mode: str) -> None:
        self.calls.append(f"set_margin:{symbol}:{mode}")
        self.margin_set.append(mode)

    def set_leverage(self, symbol: str, leverage: str) -> None:
        self.calls.append(f"set_leverage:{symbol}:{leverage}")
        self.leverage_set.append(leverage)

    # -- order path --
    def place_entry_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        client_oid: str,
        order_type: str = "market",
    ) -> dict[str, Any]:
        self.post_count += 1
        if self._place_behavior == "unknown":
            from fatty_trader.exchanges.bitget.client import BitgetUnknownResultError

            raise BitgetUnknownResultError("POST result unknown: transport failure")
        return {"orderId": f"venue-{client_oid[-6:]}", "clientOid": client_oid}

    def get_order_detail(self, client_oid: str) -> dict[str, Any]:
        self.calls.append(f"detail:{client_oid}")
        return dict(self._detail)

    def get_fills(self, client_oid: str) -> list[dict[str, Any]]:
        self.calls.append(f"fills:{client_oid}")
        return [dict(f) for f in self._fills]

    # -- protection path --
    def place_protection_orders(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        stop_loss: Decimal,
        take_profits: Sequence[Decimal],
        client_oid: str,
    ) -> Any:
        from fatty_trader.execution.protection import ProtectionConfirmation

        self.protection_qty = quantity
        if not self._protection_confirmed:
            return ProtectionConfirmation(sl_order_id=None, tp_order_ids=(), confirmed=False)
        return ProtectionConfirmation(
            sl_order_id=f"sl-{client_oid[-6:]}",
            tp_order_ids=(f"tp-{client_oid[-6:]}",),
            confirmed=True,
        )

    def read_protection_state(
        self,
        *,
        symbol: str,
        sl_order_id: str | None,
        tp_order_ids: Sequence[str],
    ) -> ProtectionReport:
        from fatty_trader.execution.protection import ProtectionState

        if not self._protection_confirmed or sl_order_id is None:
            return ProtectionReport(ProtectionState.FAILED, Decimal("0"), "unconfirmed")
        return ProtectionReport(ProtectionState.VENUE_PROTECTED, Decimal("0.01"))

    def place_market_close(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        client_oid: str,
    ) -> dict[str, Any]:
        self.emergency_calls.append(
            {"symbol": symbol, "side": side, "quantity": quantity, "oid": client_oid}
        )
        return {"orderId": f"close-{client_oid[-6:]}"}


def _request(**overrides: Any) -> LiveEntryRequest:
    base: dict[str, Any] = {
        "exchange": "bitget",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": Decimal("0.01"),
        "leverage": 20,
        "stop_loss": Decimal("49000"),
        "take_profits": (Decimal("51000"),),
    }
    base.update(overrides)
    return LiveEntryRequest(**base)


def _filled_detail(qty: str = "0.01") -> dict[str, Any]:
    return {"status": "filled", "requestedQty": qty, "clientOid": "x"}


def _fill(price: str = "50000", qty: str = "0.01", fee: str = "0.3") -> dict[str, Any]:
    return {
        "fillId": f"f-{price}-{qty}",
        "price": price,
        "quantity": qty,
        "fee": fee,
        "orderId": "venue-1",
    }


def test_client_oid_format_is_deterministic() -> None:
    oid = build_live_client_oid("bitget", "BTCUSDT", token_hex="ab12cd34ef56ab78")
    assert oid == "live-bitget-BTCUSDT-ab12cd34ef56ab78"


def test_happy_path_fill_and_protect() -> None:
    client = FakeLiveClient(detail=_filled_detail(), fills=[_fill()])
    store = InMemoryLiveIntentStore()
    result = enter_live_position(client, store, _request())
    assert result.status is LiveOrderStatus.FILLED
    assert result.filled_qty == Decimal("0.01")
    assert result.avg_price == Decimal("50000")
    assert result.provider_order_id is not None
    assert result.provider_fill_ids == ("f-50000-0.01",)
    assert client.protection_qty == Decimal("0.01")
    assert result.emergency_closed is False
    # pre-entry reads happened
    for marker in ("balance", "position_mode"):
        assert marker in client.calls


def test_partial_fill_sizes_protection_to_filled_qty() -> None:
    detail = {"status": "partially_filled", "requestedQty": "0.02", "clientOid": "x"}
    fills = [_fill(qty="0.008")]
    client = FakeLiveClient(detail=detail, fills=fills)
    store = InMemoryLiveIntentStore()
    result = enter_live_position(client, store, _request(quantity=Decimal("0.02")))
    assert result.status is LiveOrderStatus.PARTIAL
    assert result.filled_qty == Decimal("0.008")
    assert client.protection_qty == Decimal("0.008")


def test_unknown_result_reconciles_by_client_oid_without_blind_retry() -> None:
    client = FakeLiveClient(detail=_filled_detail(), fills=[_fill()], place_behavior="unknown")
    store = InMemoryLiveIntentStore()
    result = enter_live_position(client, store, _request())
    assert result.status is LiveOrderStatus.FILLED
    assert client.post_count == 1  # NEVER a blind second POST


def test_protection_failure_triggers_emergency_close_and_alert() -> None:
    client = FakeLiveClient(detail=_filled_detail(), fills=[_fill()], protection_confirmed=False)
    store = InMemoryLiveIntentStore()
    alerts: list[str] = []
    result = enter_live_position(client, store, _request(), alert=alerts.append)
    assert result.emergency_closed is True
    assert len(client.emergency_calls) == 1
    assert alerts, "expected an alert callback on unconfirmable protection"


def test_idempotent_retry_by_client_oid_posts_once() -> None:
    client = FakeLiveClient(detail=_filled_detail(), fills=[_fill()])
    store = InMemoryLiveIntentStore()
    oid = build_live_client_oid("bitget", "BTCUSDT", token_hex="0011223344556677")
    first = enter_live_position(client, store, _request(client_oid=oid))
    second = enter_live_position(client, store, _request(client_oid=oid))
    assert client.post_count == 1
    assert first.client_oid == second.client_oid == oid
    assert second.status is LiveOrderStatus.FILLED


def test_rejected_order_classified_and_no_protection() -> None:
    detail = {"status": "rejected", "requestedQty": "0.01", "clientOid": "x"}
    client = FakeLiveClient(detail=detail, fills=[])
    store = InMemoryLiveIntentStore()
    result = enter_live_position(client, store, _request())
    assert result.status is LiveOrderStatus.REJECTED
    assert result.filled_qty == Decimal("0")
    assert client.protection_qty is None
    assert result.emergency_closed is False
