from decimal import Decimal

import pytest

from fatty_trader.domain.enums import Direction, Exchange
from fatty_trader.execution.protection import (
    ProtectionPlan,
    ProtectionState,
    reconcile_protection,
)


def plan() -> ProtectionPlan:
    return ProtectionPlan(
        exchange=Exchange.BINANCE,
        symbol="BTCUSDT",
        direction=Direction.LONG,
        quantity=Decimal("0.01"),
        stop_loss=Decimal("63000"),
        take_profits=(Decimal("65000"),),
    )


def test_protection_report_preserves_explicit_degraded_state() -> None:
    class Adapter:
        def reconcile_protection(self, _: ProtectionPlan):
            from fatty_trader.execution.protection import ProtectionReport
            return ProtectionReport(ProtectionState.DEGRADED, Decimal("0"), "stream stale")

    report = reconcile_protection(Adapter(), plan())
    assert report.state is ProtectionState.DEGRADED
    assert report.reason == "stream stale"


def test_protection_rejects_negative_venue_quantity() -> None:
    class Adapter:
        def reconcile_protection(self, _: ProtectionPlan):
            from fatty_trader.execution.protection import ProtectionReport
            return ProtectionReport(ProtectionState.FAILED, Decimal("-1"))

    with pytest.raises(ValueError, match="negative"):
        reconcile_protection(Adapter(), plan())
