from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest

from fatty_trader.exchanges.bitget.reconciliation import (
    KillSwitchTrigger,
    PnL,
    Reconciler,
    ReconcilerConfig,
    ReconcilerStatus,
)


class FakeReconcilerClient:
    """Minimal fake satisfying the Reconciler's client protocol."""

    def __init__(self) -> None:
        self.balance = Decimal("1000")
        self.price = Decimal("60000")
        self.positions: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []
        self.margin_mode = "isolated"
        self.leverage = "20"

    def get_available_balance(self) -> Decimal:
        return self.balance

    def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            return [p for p in self.positions if p.get("symbol") == symbol]
        return self.positions

    def get_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            return [o for o in self.orders if o.get("symbol") == symbol]
        return self.orders

    def get_fills(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            return [f for f in self.fills if f.get("symbol") == symbol]
        return self.fills

    def get_current_price(self, symbol: str) -> Decimal:
        return self.price

    def get_margin_mode(self, symbol: str) -> str:
        return self.margin_mode

    def get_leverage(self, symbol: str) -> str:
        return self.leverage


def make_reconciler(
    **overrides: object,
) -> tuple[Reconciler, FakeReconcilerClient, ReconcilerConfig]:
    cfg = ReconcilerConfig(**overrides) if overrides else ReconcilerConfig()
    client = FakeReconcilerClient()
    rec = Reconciler(client=client, config=cfg)
    return rec, client, cfg


def test_pnl_calculation() -> None:
    pnl = PnL(
        realized_profit=Decimal("100"),
        realized_loss=Decimal("50"),
        fees=Decimal("10"),
        unrealized_pnl=Decimal("30"),
    )
    assert pnl.net_pnl == Decimal("70")


def test_initial_status_is_starting() -> None:
    rec, _, _ = make_reconciler()
    assert rec.status == ReconcilerStatus.STARTING


def test_tick_without_positions_passes() -> None:
    rec, _, _ = make_reconciler()
    rec.tick()
    assert rec.status == ReconcilerStatus.OK


def test_tick_detects_unknown_orders() -> None:
    rec, client, _ = make_reconciler()
    # Reconciler knows about nothing, but venue has an order → unknown
    client.orders = [
        {"order_id": "ext-1", "symbol": "BTCUSDT", "side": "BUY", "state": "open"},
    ]
    rec.tick()
    assert rec.last_mismatch_count >= 1
    assert any("unknown" in m.lower() for m in rec.last_mismatches)


def test_tick_ignores_known_orders() -> None:
    rec, client, _ = make_reconciler()
    client.orders = [
        {"order_id": "ext-1", "symbol": "BTCUSDT", "side": "BUY", "state": "open"},
    ]
    rec.register_known_order("ext-1")
    rec.tick()
    assert rec.last_mismatch_count == 0


def test_tick_detects_protection_mismatch() -> None:
    rec, client, _ = make_reconciler(kill_on_missing_protection=False)
    client.positions = [
        {"symbol": "BTCUSDT", "side": "LONG", "size": Decimal("0.01"), "entry": Decimal("60000")},
    ]
    # No SL/TP orders → protection missing
    rec.tick()
    assert any("protection" in m.lower() for m in rec.last_mismatches)


def test_tick_passes_with_protected_position() -> None:
    rec, client, _ = make_reconciler()
    client.positions = [
        {"symbol": "BTCUSDT", "side": "LONG", "size": Decimal("0.01"), "entry": Decimal("60000")},
    ]
    client.orders = [
        {"order_id": "sl-1", "symbol": "BTCUSDT", "side": "SELL", "state": "open", "role": "SL"},
        {"order_id": "tp-1", "symbol": "BTCUSDT", "side": "SELL", "state": "open", "role": "TP"},
    ]
    rec.register_known_order("sl-1")
    rec.register_known_order("tp-1")
    rec.tick()
    assert rec.last_mismatch_count == 0


def test_kill_switch_on_stale_data() -> None:
    rec, _, cfg = make_reconciler(stale_threshold_seconds=0.0001)
    rec.tick()
    rec._last_tick_seconds = time.time() - 1000  # force stale
    with pytest.raises(KillSwitchTrigger) as exc:
        rec.tick()
    assert exc.value.reason == KillSwitchTrigger.STALE_RECONCILIATION


def test_kill_switch_on_wrong_margin_mode() -> None:
    rec, client, _ = make_reconciler()
    client.margin_mode = "crossed"
    with pytest.raises(KillSwitchTrigger) as exc:
        rec.tick()
    assert "margin" in exc.value.reason.lower()


def test_kill_switch_on_missing_protection() -> None:
    rec, client, _ = make_reconciler(kill_on_missing_protection=False)
    client.positions = [
        {"symbol": "BTCUSDT", "side": "LONG", "size": Decimal("0.01"), "entry": Decimal("60000")},
    ]
    rec.tick()
    # Manually trigger protection-missing kill if configured
    rec.config.kill_on_missing_protection = True
    with pytest.raises(KillSwitchTrigger):
        rec._check_protection_or_kill(["protection missing"])


def test_tick_handles_network_failure_gracefully() -> None:
    class FailingClient(FakeReconcilerClient):
        def get_margin_mode(self, symbol: str) -> str:
            raise ConnectionError("network down")

    cfg = ReconcilerConfig()
    rec = Reconciler(client=FailingClient(), config=cfg)
    rec.tick()
    assert rec.status == ReconcilerStatus.DEGRADED
    assert rec.last_mismatch_count >= 0  # degraded, not crashed


def test_pnl_with_no_trades_is_zero() -> None:
    pnl = PnL()
    assert pnl.net_pnl == Decimal("0")


def test_status_callback_fires_on_tick() -> None:
    rec, _, _ = make_reconciler()
    calls: list[ReconcilerStatus] = []
    rec.on_status_change(lambda s: calls.append(s))
    rec.tick()
    assert ReconcilerStatus.OK in calls
