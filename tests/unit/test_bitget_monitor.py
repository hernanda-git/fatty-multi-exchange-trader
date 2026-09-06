from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.live import LiveIntentRecord
from fatty_trader.execution.bitget_monitor import BitgetMonitor
from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository


@dataclass
class ReadOnlyVenue:
    positions: list[dict[str, str]] = field(default_factory=list)
    orders: list[dict[str, str]] = field(default_factory=list)
    plans: list[dict[str, str]] = field(default_factory=list)
    details: dict[str, dict[str, str]] = field(default_factory=dict)
    fills: list[dict[str, str]] = field(default_factory=list)
    clock_skew_ms: int = 0
    calls: list[str] = field(default_factory=list)

    async def get_all_positions(self) -> list[dict[str, str]]:
        self.calls.append("get_all_positions")
        return self.positions

    async def get_pending_orders(self) -> list[dict[str, str]]:
        self.calls.append("get_pending_orders")
        return self.orders

    async def get_pending_plan_orders(self, symbol: str) -> list[dict[str, str]]:
        self.calls.append("get_pending_plan_orders")
        return self.plans

    async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
        self.calls.append("get_single_position")
        return [row for row in self.positions if row.get("symbol") == symbol]

    async def get_order_detail(self, symbol: str, *, client_oid: str) -> dict[str, str]:
        self.calls.append("get_order_detail")
        return self.details[client_oid]

    async def get_fills(self, symbol: str) -> list[dict[str, str]]:
        self.calls.append("get_fills")
        return self.fills

    async def get_clock_skew_ms(self) -> int:
        self.calls.append("get_clock_skew_ms")
        return self.clock_skew_ms


@pytest.mark.asyncio
async def test_clean_flat_account_is_ok_without_latching_kill_switch() -> None:
    venue = ReadOnlyVenue()
    repository = InMemoryReconciliationRepository()

    report = await BitgetMonitor(venue, repository).run_once()

    assert report.status == "ok"
    assert report.reasons == ()
    assert repository.kill_switch_active("bitget") is False
    assert repository.alerts == []
    assert all(call.startswith("get_") for call in venue.calls)


@pytest.mark.asyncio
async def test_empty_bitget_pending_order_envelope_is_normalized() -> None:
    venue = ReadOnlyVenue()
    venue.get_pending_orders = lambda: _empty_order_envelope()  # type: ignore[method-assign]
    repository = InMemoryReconciliationRepository()

    report = await BitgetMonitor(venue, repository).run_once()

    assert report.status == "ok"
    assert repository.kill_switch_active("bitget") is False


async def _empty_order_envelope() -> dict[str, object]:
    return {"entrustedList": None, "endId": None}


@pytest.mark.asyncio
async def test_unknown_intent_is_reconciled_by_get_only_without_latching_kill_switch() -> None:
    intent = LiveIntentRecord(
        exchange="bitget",
        client_oid="known-unknown",
        symbol="BTCUSDT",
        side="BUY",
        state="unknown",
        requested_qty=Decimal("0.01"),
    )
    venue = ReadOnlyVenue(
        details={"known-unknown": {"status": "filled", "orderId": "provider-1"}},
        fills=[{"fillId": "fill-1", "size": "0.01", "price": "60000"}],
    )
    repository = InMemoryReconciliationRepository(intents=[intent])

    report = await BitgetMonitor(venue, repository).run_once()

    assert report.status == "ok"
    assert repository.intents[0].state == "filled"
    assert repository.intents[0].provider_order_id == "provider-1"
    assert venue.calls.count("get_order_detail") == 1
    assert all(call.startswith("get_") for call in venue.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("positions", "orders", "plans", "clock_skew_ms", "reason"),
    [
        (
            [{"symbol": "BTCUSDT", "total": "0.01", "marginMode": "isolated"}],
            [],
            [],
            0,
            "missing-stop-loss",
        ),
        (
            [{"symbol": "BTCUSDT", "total": "0.01", "marginMode": "crossed"}],
            [],
            [],
            0,
            "margin-mode-not-isolated",
        ),
        ([], [{"clientOid": "foreign-order"}], [], 0, "unexpected-order:foreign-order"),
        ([], [], [], 10_001, "clock-skew-exceeded"),
    ],
)
async def test_unsafe_provider_read_latches_kill_switch_and_deduplicates_alert(
    positions: list[dict[str, str]],
    orders: list[dict[str, str]],
    plans: list[dict[str, str]],
    clock_skew_ms: int,
    reason: str,
) -> None:
    venue = ReadOnlyVenue(
        positions=positions, orders=orders, plans=plans, clock_skew_ms=clock_skew_ms
    )
    repository = InMemoryReconciliationRepository(
        expected_symbols={"BTCUSDT"} if positions else set()
    )
    monitor = BitgetMonitor(venue, repository, max_clock_skew_ms=10_000)

    first = await monitor.run_once()
    second = await monitor.run_once()

    assert first.status == second.status == "kill-switch-latched"
    assert reason in first.reasons
    assert repository.kill_switch_active("bitget") is True
    assert repository.alerts == [reason]
    assert all(call.startswith("get_") for call in venue.calls)


@pytest.mark.asyncio
async def test_unexpected_position_is_latched_even_when_native_protection_exists() -> None:
    venue = ReadOnlyVenue(
        positions=[{"symbol": "BTCUSDT", "total": "0.01", "marginMode": "isolated"}],
        plans=[
            {"planType": "loss_plan", "size": "0.01"},
            {"planType": "profit_plan", "size": "0.01"},
        ],
    )
    repository = InMemoryReconciliationRepository()

    report = await BitgetMonitor(venue, repository).run_once()

    assert report.status == "kill-switch-latched"
    assert report.reasons == ("unexpected-position:BTCUSDT",)
