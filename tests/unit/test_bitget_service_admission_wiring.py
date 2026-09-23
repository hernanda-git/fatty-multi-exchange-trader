from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from fatty_trader.execution.bitget_dispatch_repository import BitgetDispatch
from fatty_trader.execution.bitget_dispatcher import BitgetAdmission
from fatty_trader.risk.sizing import MMTier, SymbolMetadata
from fatty_trader.service import _bitget_dispatch_preflight


class Venue:
    def __init__(self) -> None:
        self.snapshot = SimpleNamespace(
            account=SimpleNamespace(
                available=Decimal("100"),
                total_balance=Decimal("100"),
                equity=Decimal("100"),
                margin_coin="USDT",
                observed_at=datetime.now(UTC),
            ),
            metadata=SymbolMetadata(
                symbol="PENDLEUSDT",
                price_precision=4,
                price_tick=Decimal("0.0001"),
                size_step=Decimal("1"),
                min_order_qty=Decimal("1"),
                min_notional=Decimal("5"),
                max_leverage=50,
                contract_value=Decimal("1"),
                mm_tiers=(MMTier(upper_bound_notional=None, mmr=Decimal("0.005")),),
            ),
            current_price=Decimal("2.32"),
        )

    async def preflight(self, symbol: str):
        assert symbol == "PENDLEUSDT"
        return self.snapshot


class Reservations:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] | None = None

    def reserve(self, **kwargs: object):
        self.kwargs = kwargs
        from fatty_trader.storage.balance_reservations import BalanceAdmission

        return BalanceAdmission(True, snapshot_id=UUID(int=1), reservation_id=UUID(int=2))


def _dispatch() -> BitgetDispatch:
    return BitgetDispatch(
        id=UUID(int=3),
        state="QUEUED",
        claimed_by="worker",
        attempts=1,
        pair_token="PENDLEUSDT",
        direction="LONG",
        entry_price=Decimal("2.32"),
        stop_loss=Decimal("2.25"),
        take_profits=(Decimal("2.60"),),
    )


@pytest.mark.asyncio
async def test_production_preflight_reserves_the_exact_admission_before_dispatch() -> None:
    venue = Venue()
    venue.snapshot = venue.snapshot.__class__(
        account=venue.snapshot.account.__class__(
            available=Decimal("100"),
            total_balance=Decimal("100"),
            equity=Decimal("100"),
            margin_coin="USDT",
            observed_at=datetime.now(UTC),
            margin_mode="isolated",
            position_mode="one_way_mode",
            long_leverage=Decimal("20"),
            short_leverage=Decimal("20"),
        ),
        metadata=venue.snapshot.metadata,
        current_price=venue.snapshot.current_price,
    )
    reservations = Reservations()
    preflight = _bitget_dispatch_preflight(venue, {}, reservation_repository=reservations)

    admission = await preflight(_dispatch())

    assert isinstance(admission, BitgetAdmission)
    assert admission.submission.balance_snapshot_id == UUID(int=1)
    assert admission.submission.margin_reservation_id == UUID(int=2)
    assert reservations.kwargs is not None
    assert reservations.kwargs["planned_margin_usdt"] == admission.submission.planned_margin_usdt
    assert reservations.kwargs["client_order_id"].startswith("live-bitget-PENDLEUSDT-")
