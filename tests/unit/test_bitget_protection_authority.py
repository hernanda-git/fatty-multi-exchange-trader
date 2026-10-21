"""One authority for native protection truth (fakes only; no network).

The venue keeps position-level plan ids off the position row, so a reader that
trusts ``position.stopLossId`` reports ``missing-stop-loss`` for a position whose
stop is resting at the venue.  On 2026-10-20 that produced a live alert telling the
operator to manage a protected position by hand, while the dispatcher recorded the
same position as ``VERIFIED``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from fatty_trader.exchanges.bitget.reconciliation_live import confirm_native_stop_protection
from fatty_trader.execution.bitget_monitor import BitgetMonitor
from fatty_trader.execution.protection import ProtectionState
from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository

POSITION = {
    "symbol": "WLDUSDT",
    "total": "39",
    "size": "39",
    "holdSide": "short",
    "marginMode": "isolated",
    "posMode": "one_way_mode",
    # The venue leaves both plan ids empty on the position row.
    "stopLossId": "",
    "takeProfitId": "",
}

LIVE_STOP_PLAN = {
    "symbol": "WLDUSDT",
    "holdSide": "short",
    "planType": "pos_loss",
    "orderId": "1496442792033873920",
    "clientOid": "1496442792033873920",
    "stopLossTriggerPrice": "0.5752",
    "triggerPrice": "0.5752",
    "triggerType": "mark_price",
    "planStatus": "live",
    "size": "",
}


class Venue:
    def __init__(self, plans: list[dict[str, Any]] | None = None) -> None:
        self.plans = plans if plans is not None else [LIVE_STOP_PLAN]

    async def get_single_position(self, symbol: str) -> list[dict[str, Any]]:
        return [POSITION] if symbol == "WLDUSDT" else []

    async def get_pending_plan_orders(self, symbol: str) -> list[dict[str, Any]]:
        return [plan for plan in self.plans if plan.get("symbol") == symbol]


async def _confirm(venue: Venue) -> Any:
    return await confirm_native_stop_protection(
        lambda: venue.get_single_position("WLDUSDT"),
        lambda: venue.get_pending_plan_orders("WLDUSDT"),
        symbol="WLDUSDT",
        expected_quantity=Decimal("39"),
    )


@pytest.mark.asyncio
async def test_a_live_venue_stop_is_protected_even_when_the_position_row_has_no_ids() -> None:
    report = await _confirm(Venue())

    assert report.state is ProtectionState.VENUE_PROTECTED
    assert report.reason is None
    assert report.observed_quantity == Decimal("39")


@pytest.mark.asyncio
async def test_without_a_venue_plan_the_stop_is_missing() -> None:
    report = await _confirm(Venue(plans=[]))

    assert report.state is ProtectionState.DEGRADED
    assert report.reason == "missing-stop-loss"


@pytest.mark.asyncio
async def test_a_plan_for_another_symbol_does_not_protect_this_position() -> None:
    report = await _confirm(Venue(plans=[{**LIVE_STOP_PLAN, "symbol": "BTCUSDT"}]))

    assert report.state is ProtectionState.DEGRADED
    assert report.reason == "missing-stop-loss"


@pytest.mark.asyncio
async def test_an_inactive_plan_does_not_protect_the_position() -> None:
    report = await _confirm(Venue(plans=[{**LIVE_STOP_PLAN, "planStatus": "cancelled"}]))

    assert report.state is ProtectionState.DEGRADED
    assert report.reason == "missing-stop-loss"


@pytest.mark.asyncio
async def test_a_stop_for_the_wrong_side_does_not_protect_the_position() -> None:
    report = await _confirm(Venue(plans=[{**LIVE_STOP_PLAN, "holdSide": "buy"}]))

    assert report.state is ProtectionState.DEGRADED
    assert report.reason == "missing-stop-loss"


@pytest.mark.asyncio
async def test_the_monitor_does_not_alert_a_protected_position_as_unprotected() -> None:
    class MonitorVenue:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_all_positions(self) -> list[dict[str, Any]]:
            self.calls.append("get_all_positions")
            return [POSITION]

        async def get_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
            self.calls.append("get_pending_orders")
            return []

        async def get_pending_plan_orders(self, symbol: str) -> list[dict[str, Any]]:
            self.calls.append("get_pending_plan_orders")
            return [LIVE_STOP_PLAN]

        async def get_single_position(self, symbol: str) -> list[dict[str, Any]]:
            self.calls.append("get_single_position")
            return [POSITION]

        async def get_order_detail(self, symbol: str, *, client_oid: str) -> dict[str, Any]:
            self.calls.append("get_order_detail")
            return {}

        async def get_fills(self, symbol: str | None = None) -> list[dict[str, Any]]:
            self.calls.append("get_fills")
            return []

        async def get_clock_skew_ms(self) -> int:
            self.calls.append("get_clock_skew_ms")
            return 0

    venue = MonitorVenue()
    repository = InMemoryReconciliationRepository(expected_symbols={"WLDUSDT"})

    report = await BitgetMonitor(venue, repository).run_once()

    assert "missing-stop-loss" not in report.reasons
    assert repository.alerts == []
    assert repository.kill_switch_active("bitget") is False
