from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from fatty_trader.operator.command_parser import CommandError
from fatty_trader.operator.live_commands import (
    OperatorCommandService,
)


class FakeLiveGateway:
    """Minimal LiveGateway fake for operator command tests."""

    def __init__(self) -> None:
        self.balance = Decimal("1000")
        self.price = Decimal("60000")
        self.positions: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.cancel_calls: list[str] = []
        self.close_calls: list[str] = []
        self.last_open: dict[str, Any] | None = None

    def get_price(self, symbol: str) -> Decimal:
        return self.price

    def get_balance(self) -> Decimal:
        return self.balance

    def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            return [p for p in self.positions if p["symbol"] == symbol]
        return self.positions

    def get_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            return [o for o in self.orders if o["symbol"] == symbol]
        return self.orders

    def open_position(
        self,
        *,
        symbol: str,
        direction: str,
        quantity: Decimal,
        leverage: int,
        entry: str,
        stop_loss: Decimal | str,
        take_profits: tuple[Decimal | str, ...],
    ) -> dict[str, Any]:
        result = {
            "order_id": f"oid-{uuid4().hex[:8]}",
            "symbol": symbol,
            "side": direction,
            "qty": quantity,
            "leverage": leverage,
            "entry": entry,
            "state": "filled",
            "fill_price": self.price,
            "sl": stop_loss,
            "tp": take_profits,
        }
        self.last_open = result
        self.positions.append({
            "symbol": symbol,
            "side": direction,
            "size": quantity,
            "entry": self.price,
        })
        return result

    def cancel_order(self, target: str) -> dict[str, Any]:
        self.cancel_calls.append(target)
        return {"cancelled": target, "count": 1}

    def cancel_all(self) -> dict[str, Any]:
        self.cancel_calls.append("all")
        n = len(self.orders)
        self.orders = []
        return {"cancelled": "all", "count": n}

    def close_position(self, target: str) -> dict[str, Any]:
        self.close_calls.append(target)
        return {"closed": target}

    def close_all(self) -> dict[str, Any]:
        self.close_calls.append("all")
        n = len(self.positions)
        self.positions = []
        return {"closed": "all", "count": n}


def make_service() -> tuple[OperatorCommandService, FakeLiveGateway]:
    gw = FakeLiveGateway()
    svc = OperatorCommandService(gateway=gw, operator_id=1, require_confirmation=True)
    return svc, gw


def test_price_command_returns_formatted_alert() -> None:
    svc, gw = make_service()
    alert = svc.handle("/price BTCUSDT", sender_id=1, is_private=True, is_forwarded=False)
    assert "BTCUSDT" in alert
    assert "60000" in alert


def test_balance_command_shows_available() -> None:
    svc, gw = make_service()
    alert = svc.handle("/balance", sender_id=1, is_private=True, is_forwarded=False)
    assert "1000" in alert


def test_positions_empty() -> None:
    svc, gw = make_service()
    alert = svc.handle("/positions", sender_id=1, is_private=True, is_forwarded=False)
    assert "position" in alert.lower()


def test_open_market_alert_contains_required_fields() -> None:
    svc, gw = make_service()
    alert = svc.handle(
        "/open BTCUSDT LONG margin=auto leverage=20 entry=market sl=auto tp=auto",
        sender_id=1, is_private=True, is_forwarded=False,
    )
    assert "BTCUSDT" in alert
    assert "LONG" in alert
    # Never leak secrets
    for banned in ("api_key", "secret", "passphrase", "signature"):
        assert banned not in alert.lower()


def test_cancel_all_requires_confirmation_first() -> None:
    svc, gw = make_service()
    gw.orders = [
        {
            "symbol": "BTCUSDT", "order_id": "a",
            "side": "BUY", "price": Decimal("60000"), "size": Decimal("0.01"),
        },
    ]
    first = svc.handle("/cancel all", sender_id=1, is_private=True, is_forwarded=False)
    assert "confirm" in first.lower()
    # Second call with confirm token
    token = svc._pending.token if svc._pending else ""
    second = svc.handle(
        f"/cancel all confirm={token}", sender_id=1, is_private=True, is_forwarded=False,
    )
    assert "cancel" in second.lower()


def test_close_all_requires_confirmation_first() -> None:
    svc, gw = make_service()
    gw.positions = [
        {"symbol": "BTCUSDT", "side": "LONG", "size": Decimal("0.01"), "entry": Decimal("60000")},
    ]
    first = svc.handle("/close all", sender_id=1, is_private=True, is_forwarded=False)
    assert "confirm" in first.lower()
    token = svc._pending.token if svc._pending else ""
    second = svc.handle(
        f"/close all confirm={token}", sender_id=1, is_private=True, is_forwarded=False,
    )
    assert "close" in second.lower()


def test_close_position_by_id() -> None:
    svc, gw = make_service()
    gw.positions = [
        {"symbol": "BTCUSDT", "side": "LONG", "size": Decimal("0.01"), "entry": Decimal("60000")},
    ]
    alert = svc.handle("/close position_id=pos-1", sender_id=1, is_private=True, is_forwarded=False)
    assert "pos-1" in alert


def test_orders_lists_pending() -> None:
    svc, gw = make_service()
    gw.orders = [
        {
            "symbol": "ETHUSDT", "order_id": "x",
            "side": "SELL", "price": Decimal("3000"), "size": Decimal("0.1"),
        },
    ]
    alert = svc.handle("/orders", sender_id=1, is_private=True, is_forwarded=False)
    assert "ETHUSDT" in alert


def test_unauthorized_sender_rejected() -> None:
    svc, _ = make_service()
    with pytest.raises(PermissionError):
        svc.handle("/balance", sender_id=999, is_private=True, is_forwarded=False)


def test_public_sender_rejected() -> None:
    svc, _ = make_service()
    with pytest.raises(PermissionError):
        svc.handle("/balance", sender_id=1, is_private=False, is_forwarded=False)


def test_forwarded_sender_rejected() -> None:
    svc, _ = make_service()
    with pytest.raises(PermissionError):
        svc.handle("/balance", sender_id=1, is_private=True, is_forwarded=True)


def test_unknown_command_raises() -> None:
    svc, _ = make_service()
    with pytest.raises(CommandError):
        svc.handle("/foobar", sender_id=1, is_private=True, is_forwarded=False)


def test_secrets_never_in_alerts() -> None:
    svc, gw = make_service()
    # Inject a position with suspicious-looking data; alert must still not leak secret-like strings
    gw.positions = [
        {"symbol": "BTCUSDT", "side": "LONG", "size": Decimal("0.01"), "entry": Decimal("60000")},
    ]
    alert = svc.handle("/positions", sender_id=1, is_private=True, is_forwarded=False)
    lower = alert.lower()
    assert "api_key" not in lower
    assert "secret" not in lower
    assert "passphrase" not in lower
    assert "signature" not in lower
