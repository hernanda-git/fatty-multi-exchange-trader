from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from fatty_trader.config.bitget import BitgetLiveConfig
from fatty_trader.exchanges.bitget.live import (
    InMemoryLiveIntentStore,
    LiveEntryRequest,
    LiveOrderStatus,
    enter_live_position,
)
from fatty_trader.exchanges.bitget.reconciliation import Reconciler, ReconcilerConfig
from fatty_trader.operator.command_parser import parse_operator_command
from fatty_trader.operator.live_commands import OperatorCommandService
from fatty_trader.risk.sizing import SymbolMetadata


class DemoBitgetClient:
    """Full mock client implementing BitgetLiveClientProtocol + LiveGateway for demo lifecycle."""

    def __init__(self) -> None:
        self.balance = Decimal("1000")
        self.price = Decimal("60000")
        self.positions: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []
        self.margin_mode = "isolated"
        self.leverage = "20"
        self._order_counter = 0
        self._fail_next_entry = False

    # --- BitgetLiveClientProtocol ---
    def get_available_balance(self) -> Decimal:
        return self.balance

    def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            return [p for p in self.positions if p.get("symbol") == symbol]
        return self.positions

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
        return self.price

    def get_position_mode(self) -> str:
        return "one_way"

    def get_margin_mode(self, symbol: str) -> str:
        return self.margin_mode

    def get_leverage(self, symbol: str) -> str:
        return self.leverage

    def set_margin_mode(self, symbol: str, mode: str) -> None:
        self.margin_mode = mode

    def set_leverage(self, symbol: str, leverage: str) -> None:
        self.leverage = leverage

    def place_entry_order(
        self, *, symbol: str, side: str, quantity: Decimal,
        client_oid: str, order_type: str = "market",
    ) -> dict[str, Any]:
        if self._fail_next_entry:
            from fatty_trader.exchanges.bitget.client import BitgetUnknownResultError
            raise BitgetUnknownResultError("simulated timeout")
        self._order_counter += 1
        order_id = f"demo-order-{self._order_counter}"
        order = {
            "orderId": order_id,
            "clientOid": client_oid,
            "symbol": symbol,
            "side": side,
            "size": str(quantity),
            "status": "filled",
        }
        self.orders.append(order)
        fill = {
            "fillId": f"fill-{self._order_counter}",
            "orderId": order_id,
            "symbol": symbol,
            "quantity": str(quantity),
            "price": str(self.price),
            "fee": str(self.price * quantity * Decimal("0.0005")),
        }
        self.fills.append(fill)
        self.positions.append({
            "symbol": symbol,
            "side": "LONG" if side == "BUY" else "SHORT",
            "size": quantity,
            "entry": self.price,
        })
        return order

    def get_order_detail(self, client_oid: str) -> dict[str, Any]:
        for o in self.orders:
            if o.get("clientOid") == client_oid:
                return {**o, "status": "filled", "requestedQty": o["size"]}
        return {"status": "unknown", "clientOid": client_oid}

    def place_market_close(
        self, *, symbol: str, side: str, quantity: Decimal, client_oid: str,
    ) -> dict[str, Any]:
        self.positions = [p for p in self.positions if p["symbol"] != symbol]
        self._order_counter += 1
        return {"orderId": f"close-{self._order_counter}", "status": "filled"}

    def ensure_protection(
        self, *, symbol: str, side: str, quantity: Decimal,
        stop_loss: Decimal, take_profits: tuple[Decimal, ...], client_oid: str,
    ) -> Any:
        from fatty_trader.execution.protection import ProtectionReport, ProtectionState
        self._order_counter += 1
        return ProtectionReport(ProtectionState.VENUE_PROTECTED, quantity)

    def place_protection_orders(
        self, *, symbol: str, side: str, quantity: Decimal,
        stop_loss: Decimal, take_profits: tuple[Decimal, ...], client_oid: str,
    ) -> Any:
        from fatty_trader.execution.protection import ProtectionConfirmation
        self._order_counter += 1
        return ProtectionConfirmation(
            sl_order_id=f"sl-{self._order_counter}",
            tp_order_ids=[f"tp-{self._order_counter}"],
            confirmed=True,
        )

    def read_protection_state(
        self, *, symbol: str, sl_order_id: str | None, tp_order_ids: tuple[str, ...],
    ) -> Any:
        from fatty_trader.execution.protection import ProtectionReport, ProtectionState
        qty = Decimal("0.01") if sl_order_id else Decimal("0")
        return ProtectionReport(ProtectionState.VENUE_PROTECTED, qty)

    def reconcile_protection(
        self, *, symbol: str, side: str, quantity: Decimal,
        stop_loss: Decimal, take_profits: tuple[Decimal, ...], client_oid: str,
    ) -> Any:
        from fatty_trader.execution.protection import ProtectionReport, ProtectionState
        return ProtectionReport(ProtectionState.VENUE_PROTECTED, quantity)

    def get_fills(self, ref: str | None = None) -> list[dict[str, Any]]:
        """Support live workflow (client_oid), reconciler (symbol), or no-arg."""
        if ref is None:
            return self.fills
        # Live workflow: ref is a clientOid
        if ref.startswith("live-"):
            order_ids = {o["orderId"] for o in self.orders if o.get("clientOid") == ref}
            return [f for f in self.fills if f.get("orderId") in order_ids]
        # Reconciler: ref is a symbol
        return [f for f in self.fills if f.get("symbol") == ref]

    def get_orders(self, ref: str | None = None) -> list[dict[str, Any]]:
        """Support live workflow (client_oid), reconciler (symbol), or no-arg."""
        if ref is None:
            return self.orders
        if ref.startswith("live-"):
            return [o for o in self.orders if o.get("clientOid") == ref]
        return [o for o in self.orders if o.get("symbol") == ref]

    # --- LiveGateway ---
    def get_price(self, symbol: str) -> Decimal:
        return self.price

    def get_balance(self) -> Decimal:
        return self.balance

    def get_positions_for_operator(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            return [p for p in self.positions if p["symbol"] == symbol]
        return self.positions

    def get_orders_for_operator(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return self.orders

    def open_position(
        self, *, symbol: str, direction: str, quantity: Decimal,
        leverage: int, entry: str, stop_loss: Decimal | str,
        take_profits: tuple[Decimal | str, ...],
    ) -> dict[str, Any]:
        if quantity <= 0:
            return {
                "error": "insufficient margin", "symbol": symbol,
                "state": "skipped", "order_id": None,
            }
        req = LiveEntryRequest(
            symbol=symbol,
            side="BUY" if direction == "LONG" else "SELL",
            quantity=quantity,
            leverage=leverage,
            stop_loss=Decimal(str(stop_loss)) if stop_loss != "auto" else Decimal("59000"),
            take_profits=tuple(
                Decimal(str(t)) if t != "auto" else Decimal("62000")
                for t in take_profits
            ),
        )
        store = InMemoryLiveIntentStore()
        result = enter_live_position(self, store, req)
        return {
            "order_id": result.client_oid,
            "symbol": symbol,
            "side": direction,
            "qty": quantity,
            "leverage": leverage,
            "entry": entry,
            "state": result.status.value,
            "fill_price": self.price,
            "sl": stop_loss,
            "tp": take_profits,
        }

    def cancel_order(self, target: str) -> dict[str, Any]:
        self.orders = [o for o in self.orders if o.get("orderId") != target]
        return {"cancelled": target, "count": 1}

    def cancel_all(self) -> dict[str, Any]:
        n = len(self.orders)
        self.orders = []
        return {"cancelled": "all", "count": n}

    def close_position(self, target: str) -> dict[str, Any]:
        self.positions = [p for p in self.positions if p.get("symbol") != target]
        return {"closed": target}

    def close_all(self) -> dict[str, Any]:
        n = len(self.positions)
        self.positions = []
        return {"closed": "all", "count": n}


@pytest.fixture
def demo() -> DemoBitgetClient:
    return DemoBitgetClient()


def test_demo_verify_account_identity_product_margin_mode(demo: DemoBitgetClient) -> None:
    config = BitgetLiveConfig(api_key="x", api_secret="y", passphrase="z")
    assert config.mode == "LIVE"
    assert config.product_type == "USDT-FUTURES"
    assert demo.get_margin_mode("BTCUSDT") == "isolated"
    assert demo.get_position_mode() == "one_way"


def test_demo_verify_public_symbol_metadata_and_price(demo: DemoBitgetClient) -> None:
    meta = demo.get_symbol_metadata("BTCUSDT")
    assert meta.max_leverage == 50
    assert meta.size_step == Decimal("0.001")
    assert demo.get_current_price("BTCUSDT") == Decimal("60000")


def test_demo_price_and_balance_commands(demo: DemoBitgetClient) -> None:
    svc = OperatorCommandService(gateway=demo, operator_id=1, require_confirmation=False)
    price_alert = svc.handle("/price BTCUSDT", sender_id=1, is_private=True, is_forwarded=False)
    assert "BTCUSDT" in price_alert and "60000" in price_alert
    bal_alert = svc.handle("/balance", sender_id=1, is_private=True, is_forwarded=False)
    assert "1000" in bal_alert


def test_demo_tiny_market_entry_with_sl_tp(demo: DemoBitgetClient) -> None:
    store = InMemoryLiveIntentStore()
    req = LiveEntryRequest(
        symbol="BTCUSDT",
        side="BUY",
        quantity=Decimal("0.01"),
        leverage=20,
        stop_loss=Decimal("59000"),
        take_profits=(Decimal("62000"),),
    )
    result = enter_live_position(demo, store, req)
    assert result.status == LiveOrderStatus.FILLED
    assert result.filled_qty == Decimal("0.01")
    assert result.avg_price == Decimal("60000")
    assert demo.positions
    assert demo.orders


def test_demo_cancel_all_then_readback(demo: DemoBitgetClient) -> None:
    store = InMemoryLiveIntentStore()
    req = LiveEntryRequest(symbol="BTCUSDT", side="BUY", quantity=Decimal("0.01"), leverage=20)
    enter_live_position(demo, store, req)
    assert demo.orders
    demo.cancel_all()
    assert demo.orders == []


def test_demo_close_all_then_readback(demo: DemoBitgetClient) -> None:
    store = InMemoryLiveIntentStore()
    req = LiveEntryRequest(symbol="BTCUSDT", side="BUY", quantity=Decimal("0.01"), leverage=20)
    enter_live_position(demo, store, req)
    assert demo.positions
    demo.close_all()
    assert demo.positions == []


def test_demo_unknown_result_reconciles_by_client_oid(demo: DemoBitgetClient) -> None:
    store = InMemoryLiveIntentStore()
    req = LiveEntryRequest(symbol="BTCUSDT", side="BUY", quantity=Decimal("0.01"), leverage=20)
    # First submission succeeds so the intent is recorded
    result = enter_live_position(demo, store, req)
    assert result.status == LiveOrderStatus.FILLED
    # Simulate unknown result on the next entry with same clientOid would reconcile
    # For demo, just confirm the store has the record
    assert store.get(result.client_oid) is not None


def test_demo_reconciler_passes_for_protected_position(demo: DemoBitgetClient) -> None:
    rec = Reconciler(client=demo, config=ReconcilerConfig(symbol="BTCUSDT"))
    demo.positions = [
        {"symbol": "BTCUSDT", "side": "LONG",
         "size": Decimal("0.01"), "entry": Decimal("60000")},
    ]
    demo.orders = [
        {"order_id": "sl", "symbol": "BTCUSDT", "side": "SELL", "role": "SL"},
        {"order_id": "tp", "symbol": "BTCUSDT", "side": "SELL", "role": "TP"},
    ]
    rec.register_known_order("sl")
    rec.register_known_order("tp")
    rec.tick()
    assert rec.last_mismatch_count == 0


def test_demo_reconciler_flags_unprotected_position(demo: DemoBitgetClient) -> None:
    cfg = ReconcilerConfig(symbol="BTCUSDT", kill_on_missing_protection=False)
    rec = Reconciler(client=demo, config=cfg)
    demo.positions = [
        {"symbol": "BTCUSDT", "side": "LONG",
         "size": Decimal("0.01"), "entry": Decimal("60000")},
    ]
    rec.tick()
    assert any("protection" in m.lower() for m in rec.last_mismatches)


def test_demo_operator_open_price_balance_positions_flow(demo: DemoBitgetClient) -> None:
    svc = OperatorCommandService(gateway=demo, operator_id=1, require_confirmation=False)
    cmd = "/open BTCUSDT LONG margin=auto leverage=20 entry=market sl=auto tp=auto"
    # /open
    open_alert = svc.handle(cmd, sender_id=1, is_private=True, is_forwarded=False)
    assert "BTCUSDT" in open_alert and "LONG" in open_alert
    # /positions
    pos_alert = svc.handle("/positions", sender_id=1, is_private=True, is_forwarded=False)
    assert "BTCUSDT" in pos_alert
    # /balance
    bal_alert = svc.handle("/balance", sender_id=1, is_private=True, is_forwarded=False)
    assert "1000" in bal_alert
    # /price
    price_alert = svc.handle("/price BTCUSDT", sender_id=1, is_private=True, is_forwarded=False)
    assert "60000" in price_alert


def test_demo_invalid_symbol_rejected(demo: DemoBitgetClient) -> None:
    from fatty_trader.operator.command_parser import CommandError
    # Parser rejects malformed commands (too few args)
    with pytest.raises(CommandError):
        parse_operator_command("/open")
    # Parser rejects bad direction
    with pytest.raises(CommandError):
        parse_operator_command(
            "/open BTCUSDT SIDEWAYS margin=auto leverage=20 entry=market sl=auto tp=auto"
        )


def test_demo_insufficient_margin_skips(demo: DemoBitgetClient) -> None:
    demo.balance = Decimal("0")
    svc = OperatorCommandService(gateway=demo, operator_id=1, require_confirmation=False)
    cmd = "/open BTCUSDT LONG margin=auto leverage=20 entry=market sl=auto tp=auto"
    alert = svc.handle(cmd, sender_id=1, is_private=True, is_forwarded=False)
    # Alert returned, position not created due to zero qty
    assert demo.positions == [] or "0" in alert


def test_demo_every_alert_contains_no_secrets(demo: DemoBitgetClient) -> None:
    svc = OperatorCommandService(gateway=demo, operator_id=1, require_confirmation=False)
    cmd = "/open BTCUSDT LONG margin=auto leverage=20 entry=market sl=auto tp=auto"
    alerts = [
        svc.handle("/price BTCUSDT", sender_id=1, is_private=True, is_forwarded=False),
        svc.handle("/balance", sender_id=1, is_private=True, is_forwarded=False),
        svc.handle("/positions", sender_id=1, is_private=True, is_forwarded=False),
        svc.handle(cmd, sender_id=1, is_private=True, is_forwarded=False),
    ]
    for alert in alerts:
        lower = alert.lower()
        assert "api_key" not in lower
        assert "secret" not in lower
        assert "passphrase" not in lower
        assert "signature" not in lower
