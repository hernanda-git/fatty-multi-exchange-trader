from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
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

    async def active_position_count(self) -> int:
        return 0


class Reservations:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

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
    preflight = _bitget_dispatch_preflight(
        venue, {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1"}, reservation_repository=reservations
    )

    admission = await preflight(_dispatch())

    assert isinstance(admission, BitgetAdmission)
    assert admission.submission.balance_snapshot_id == UUID(int=1)
    assert admission.submission.margin_reservation_id == UUID(int=2)
    assert reservations.kwargs is not None
    assert reservations.kwargs["planned_margin_usdt"] == admission.submission.planned_margin_usdt
    assert reservations.kwargs["client_order_id"].startswith("live-bitget-PENDLEUSDT-")


@pytest.mark.asyncio
async def test_env_margin_cap_binds_allocation_down_to_one_usdt_at_fixed_20x() -> None:
    venue = Venue()
    # 1000 USDT available: allocation (0.20) alone would request 200 USDT margin.
    venue.snapshot.account = venue.snapshot.account.__class__(
        available=Decimal("1000"),
        total_balance=Decimal("1000"),
        equity=Decimal("1000"),
        margin_coin="USDT",
        observed_at=datetime.now(UTC),
        margin_mode="isolated",
        position_mode="one_way_mode",
        long_leverage=Decimal("20"),
        short_leverage=Decimal("20"),
    )
    reservations = Reservations()
    preflight = _bitget_dispatch_preflight(
        venue,
        {
            "BITGET_MIN_LEVERAGE": "20",
            "BITGET_MAX_LEVERAGE": "20",
            "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
        },
        reservation_repository=reservations,
    )

    admission = await preflight(_dispatch())

    assert reservations.kwargs is not None
    # Ceiling, not a target: 1 USDT margin at 20x = 20 USDT notional, but size_step=1
    # floors 20/2.32=8.62 contracts to 8, so realised margin lands just under the cap.
    assert reservations.kwargs["max_margin_per_trade_usdt"] == Decimal("1")
    assert Decimal("0") < reservations.kwargs["planned_margin_usdt"] <= Decimal("1")
    assert reservations.kwargs["planned_margin_usdt"] == Decimal("0.928")
    assert admission.submission.planned_margin_usdt == Decimal("0.928")
    assert admission.submission.effective_leverage == 20
    # 8 contracts * 2.32 = 18.56 USDT notional: ~7.2% below the 20 USDT ceiling.
    assert admission.submission.planned_notional_usdt == Decimal("18.56")


@pytest.mark.asyncio
async def test_production_preflight_uses_authoritative_active_position_count() -> None:
    class ActiveVenue(Venue):
        async def active_position_count(self) -> int:
            return 5

    venue = ActiveVenue()
    venue.snapshot.account = venue.snapshot.account.__class__(
        available=Decimal("100"),
        total_balance=Decimal("100"),
        equity=Decimal("100"),
        margin_coin="USDT",
        observed_at=datetime.now(UTC),
        margin_mode="isolated",
        position_mode="one_way_mode",
        long_leverage=Decimal("20"),
        short_leverage=Decimal("20"),
    )
    with pytest.raises(ValueError, match="sizing rejected"):
        await _bitget_dispatch_preflight(
            venue,
            {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1"},
            reservation_repository=Reservations(),
        )(_dispatch())
