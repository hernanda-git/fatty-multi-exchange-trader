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

    async def active_position_snapshot(self):
        return SimpleNamespace(symbols=(), observed_at=datetime.now(UTC))


class Reservations:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    def reserve(self, **kwargs: object):
        import inspect

        from fatty_trader.storage.balance_reservations import (
            PostgresBitgetMarginReservationRepository,
        )

        inspect.signature(PostgresBitgetMarginReservationRepository.reserve).bind(self, **kwargs)
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
    assert reservations.kwargs["symbol"] == venue.snapshot.metadata.symbol
    assert reservations.kwargs["environment"] == "DEMO"
    assert reservations.kwargs["max_positions"] == 5
    assert reservations.kwargs["provider_active_symbols"] == ()
    assert reservations.kwargs["max_snapshot_age"].total_seconds() == 5
    assert reservations.kwargs["max_future_skew"].total_seconds() == 1


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
        async def active_position_snapshot(self):
            return SimpleNamespace(symbols=("BTCUSDT",) * 5, observed_at=datetime.now(UTC))

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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,offset", [("account", -60), ("account", 60), ("positions", -60), ("positions", 60)]
)
async def test_stale_or_future_evidence_cannot_reserve(source, offset):
    from datetime import timedelta

    venue = Venue()
    stamp = datetime.now(UTC) + timedelta(seconds=offset)
    if source == "account":
        venue.snapshot.account.observed_at = stamp
    else:

        async def positions():
            return SimpleNamespace(symbols=(), observed_at=stamp)

        venue.active_position_snapshot = positions
    reservations = Reservations()
    with pytest.raises(ValueError, match="stale|future"):
        await _bitget_dispatch_preflight(
            venue, {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1"}, reservation_repository=reservations
        )(_dispatch())
    assert reservations.kwargs is None


@pytest.mark.parametrize(
    "key,value",
    [
        ("BITGET_MODE", "PAPER"),
        ("BITGET_MODE", ""),
        ("BITGET_MAX_NORMAL_POSITIONS", "0"),
        ("BITGET_MAX_NORMAL_POSITIONS", "-1"),
        ("BITGET_MAX_NORMAL_POSITIONS", "1.5"),
        ("BITGET_MAX_NORMAL_POSITIONS", "true"),
        ("BITGET_BALANCE_MAX_AGE_SECONDS", "Infinity"),
        ("BITGET_BALANCE_MAX_FUTURE_SKEW_SECONDS", "-1"),
        ("BITGET_BALANCE_MAX_FUTURE_SKEW_SECONDS", "NaN"),
    ],
)
def test_invalid_admission_policy_refuses_before_venue_reads(key, value):
    with pytest.raises(ValueError):
        _bitget_dispatch_preflight(
            Venue(),
            {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1", key: value},
            reservation_repository=Reservations(),
        )


@pytest.mark.asyncio
async def test_one_snapshot_supplies_duplicate_symbols_and_explicit_policy():
    from datetime import timedelta

    venue = Venue()
    stamp = datetime.now(UTC) - timedelta(seconds=1)
    calls = 0

    async def positions():
        nonlocal calls
        calls += 1
        return SimpleNamespace(symbols=("BTCUSDT", "BTCUSDT"), observed_at=stamp)

    venue.active_position_snapshot = positions
    reservations = Reservations()
    await _bitget_dispatch_preflight(
        venue,
        {
            "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
            "BITGET_MODE": "LIVE",
            "BITGET_MAX_NORMAL_POSITIONS": "3",
            "BITGET_BALANCE_MAX_AGE_SECONDS": "4",
            "BITGET_BALANCE_MAX_FUTURE_SKEW_SECONDS": "0.5",
        },
        reservation_repository=reservations,
    )(_dispatch())
    assert calls == 1
    assert reservations.kwargs is not None
    assert reservations.kwargs["provider_active_symbols"] == ("BTCUSDT", "BTCUSDT")
    assert reservations.kwargs["environment"] == "LIVE"
    assert reservations.kwargs["max_positions"] == 3
    assert reservations.kwargs["observed_at"] == stamp
    assert reservations.kwargs["max_snapshot_age"] == timedelta(seconds=4)
    assert reservations.kwargs["max_future_skew"] == timedelta(seconds=0.5)


@pytest.mark.asyncio
async def test_preflight_validates_and_carries_tick_prices_without_rewriting_source(monkeypatch):
    from dataclasses import replace

    from fatty_trader.risk import live_policy

    dispatch = replace(
        _dispatch(),
        stop_loss=Decimal("2.25005"),
        take_profits=(Decimal("2.60005"), Decimal("2.70004")),
    )
    seen = []
    original = live_policy.plan_live_position

    def capture(data):
        seen.append(data.stop_loss)
        return original(data)

    monkeypatch.setattr(live_policy, "plan_live_position", capture)
    admission = await _bitget_dispatch_preflight(
        Venue(),
        {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1"},
        reservation_repository=Reservations(),
    )(dispatch)

    assert seen == [Decimal("2.2501")]
    assert admission.submission.planned_stop_loss == Decimal("2.2501")
    assert admission.submission.planned_take_profits == (Decimal("2.6001"), Decimal("2.7000"))
    assert dispatch.stop_loss == Decimal("2.25005")
    assert dispatch.take_profits == (Decimal("2.60005"), Decimal("2.70004"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stop,targets",
    [
        ("2.31996", ("2.60",)),
        ("2.25", ("2.32004",)),
        ("2.25", ("2.60001", "2.60004")),
    ],
)
async def test_rounded_invalid_geometry_is_rejected_before_reservation(stop, targets):
    from dataclasses import replace

    reservations = Reservations()
    dispatch = replace(
        _dispatch(),
        stop_loss=Decimal(stop),
        take_profits=tuple(Decimal(target) for target in targets),
    )
    with pytest.raises(ValueError, match="rounded"):
        await _bitget_dispatch_preflight(
            Venue(),
            {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1"},
            reservation_repository=reservations,
        )(dispatch)
    assert reservations.kwargs is None


@pytest.mark.asyncio
async def test_stop_rounded_across_liquidation_buffer_is_rejected_before_reservation(monkeypatch):
    from dataclasses import replace

    from fatty_trader.domain.enums import Direction
    from fatty_trader.domain.models import BitgetLiveRiskConfig
    from fatty_trader.risk import live_policy

    venue = Venue()
    venue.snapshot.metadata = venue.snapshot.metadata.model_copy(
        update={"price_tick": Decimal("0.01"), "price_precision": 2}
    )
    # A known liquidation price makes the real guard boundary deterministic:
    # 2.20 + 10% * (2.32 - 2.20) = 2.212. Raw 2.214 passes; tick 2.21 fails.
    monkeypatch.setattr(live_policy, "estimate_liquidation_price", lambda **kwargs: Decimal("2.20"))
    dispatch = replace(_dispatch(), stop_loss=Decimal("2.214"))
    raw_decision = live_policy.plan_live_position(
        live_policy.LiveSizingInput(
            meta=venue.snapshot.metadata,
            risk=BitgetLiveRiskConfig(max_margin_per_trade_usdt=Decimal("1")),
            available_usdt=Decimal("100"),
            entry=Decimal("2.32"),
            direction=Direction.LONG,
            active_positions=0,
            stop_loss=dispatch.stop_loss,
        )
    )
    assert raw_decision.accepted
    reservations = Reservations()
    with pytest.raises(ValueError, match="sl-guard"):
        await _bitget_dispatch_preflight(
            venue,
            {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1"},
            reservation_repository=reservations,
        )(dispatch)
    assert reservations.kwargs is None


@pytest.mark.asyncio
async def test_recent_departed_entry_admits_exact_legal_split_with_actual_risk_values():
    from dataclasses import replace

    venue = Venue()
    venue.snapshot.current_price = Decimal("2.34")
    venue.snapshot.metadata = venue.snapshot.metadata.model_copy(
        update={
            "size_step": Decimal("0.1"),
            "min_order_qty": Decimal("0.1"),
            "min_notional": Decimal("1"),
        }
    )
    dispatch = replace(_dispatch(), source_received_at=datetime.now(UTC))
    reservations = Reservations()
    admission = await _bitget_dispatch_preflight(
        venue,
        {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1"},
        reservation_repository=reservations,
    )(dispatch)

    submission = admission.submission
    assert submission.quantity == Decimal("8.4")
    assert submission.entry_route.market_quantity == Decimal("2.1")
    assert submission.entry_route.limit_quantity == Decimal("6.3")
    assert submission.planned_notional_usdt == Decimal("19.656")
    assert submission.planned_margin_usdt == Decimal("0.9828")
    assert reservations.kwargs is not None
    assert reservations.kwargs["planned_margin_usdt"] == Decimal("0.9828")


@pytest.mark.asyncio
async def test_split_child_minimum_cannot_be_bypassed_by_total_notional_or_reservation():
    from dataclasses import replace

    venue = Venue()
    venue.snapshot.current_price = Decimal("2.34")
    venue.snapshot.metadata = venue.snapshot.metadata.model_copy(
        update={"size_step": Decimal("0.1"), "min_order_qty": Decimal("0.1")}
    )
    dispatch = replace(_dispatch(), source_received_at=datetime.now(UTC))
    reservations = Reservations()
    with pytest.raises(ValueError, match="split|min-notional"):
        await _bitget_dispatch_preflight(
            venue,
            {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1"},
            reservation_repository=reservations,
        )(dispatch)
    assert reservations.kwargs is None


@pytest.mark.asyncio
async def test_position_read_crossing_route_window_cannot_expand_market_child(monkeypatch):
    from dataclasses import replace
    from datetime import timedelta

    from fatty_trader import service
    from fatty_trader.execution import entry_routing

    clock = [datetime(2026, 10, 13, tzinfo=UTC)]

    class ObservedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz is not None else clock[0].replace(tzinfo=None)

    clock[0] = ObservedClock(2026, 10, 13, tzinfo=UTC)

    venue = Venue()
    venue.snapshot.account.observed_at = clock[0]
    venue.snapshot.current_price = Decimal("2.34")
    venue.snapshot.metadata = venue.snapshot.metadata.model_copy(
        update={
            "size_step": Decimal("0.1"),
            "min_order_qty": Decimal("0.1"),
            "min_notional": Decimal("1"),
            "max_market_order_qty": Decimal("2.1"),
            "max_order_qty": Decimal("6.3"),
        }
    )

    async def delayed_position_read():
        clock[0] += timedelta(seconds=2)
        return SimpleNamespace(symbols=(), observed_at=clock[0])

    venue.active_position_snapshot = delayed_position_read
    monkeypatch.setattr(service, "datetime", ObservedClock)
    monkeypatch.setattr(entry_routing, "datetime", ObservedClock)
    dispatch = replace(_dispatch(), source_received_at=clock[0] - timedelta(seconds=59))
    admission = await _bitget_dispatch_preflight(
        venue,
        {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1"},
        reservation_repository=Reservations(),
    )(dispatch)
    assert admission.submission.entry_route.market_quantity == Decimal("2.1")
    assert admission.submission.entry_route.limit_quantity == Decimal("6.3")
    assert admission.submission.quantity == Decimal("8.4")


def _budget_venue_and_dispatch(available: str, equity: str):
    from dataclasses import replace

    venue = Venue()
    venue.snapshot.account.available = Decimal(available)
    venue.snapshot.account.equity = Decimal(equity)
    venue.snapshot.account.total_balance = Decimal(equity)
    venue.snapshot.current_price = Decimal("1")
    venue.snapshot.metadata = venue.snapshot.metadata.model_copy(
        update={
            "size_step": Decimal("0.01"),
            "min_order_qty": Decimal("0.01"),
            "min_notional": Decimal("0.1"),
        }
    )
    dispatch = replace(
        _dispatch(),
        entry_price=Decimal("1"),
        stop_loss=Decimal("0.97"),
        take_profits=(Decimal("1.10"),),
    )
    return venue, dispatch


@pytest.mark.asyncio
@pytest.mark.parametrize("durable", [False, True])
@pytest.mark.parametrize(
    "mode,available,equity,percentage,fixed,leverage,expected",
    [
        ("PERCENTAGE", "0.8", "3", "0.20", "1", "20", "0.6"),
        ("PERCENTAGE", "0.8", "30", "0.20", "1", "20", "0.8"),
        ("PERCENTAGE", "3", "30", "0.20", "1", "20", "1"),
        ("FIXED_USDT", "1", "10", "0.01", "0.8", "20", "0.8"),
        ("FIXED_USDT", "0.5", "10", "0.01", "0.8", "20", "0.5"),
        ("PERCENTAGE", "1", "3", "0.20", "1", "10", "0.6"),
        (None, "2", "3", "0.20", "1", "20", "0.4"),
        (None, "100", "200", "0.20", "1", "20", "1"),
    ],
)
async def test_automated_budget_is_resolved_once_for_legacy_and_durable_admission(
    durable, mode, available, equity, percentage, fixed, leverage, expected
):
    venue, dispatch = _budget_venue_and_dispatch(available, equity)
    reservations = Reservations() if durable else None
    environ = {
        "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
        "BITGET_ALLOCATION_PCT": percentage,
        "BITGET_FIXED_MARGIN_USDT": fixed,
        "BITGET_DEFAULT_LEVERAGE": leverage,
    }
    if mode is not None:
        environ["BITGET_MARGIN_MODE_TYPE"] = mode
    result = await _bitget_dispatch_preflight(
        venue, environ, reservation_repository=reservations
    )(dispatch)

    if durable:
        assert result.submission.planned_margin_usdt == Decimal(expected)
        assert result.submission.effective_leverage == int(leverage)
        assert reservations.kwargs["planned_margin_usdt"] == Decimal(expected)
        # Reservation compares all outstanding budgets to available funds, not
        # to another percentage of them; immutable equity evidence stays intact.
        assert reservations.kwargs["headroom"] == Decimal("1")
        assert reservations.kwargs["available_balance"] == Decimal(available)
        assert reservations.kwargs["equity"] == Decimal(equity)
        assert reservations.kwargs["max_margin_per_trade_usdt"] == Decimal("1")
    else:
        spec, risk = result
        assert risk.base_margin_usdt == Decimal(expected)
        assert risk.max_auto_margin_usdt == Decimal(expected)
        assert risk.default_leverage == int(leverage)
        assert risk.max_leverage == int(leverage)
        assert spec.max_leverage == int(leverage)
        assert risk.free_margin_headroom_pct == (
            Decimal("1") if mode is not None else Decimal(percentage)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["PERCENTAGE", "FIXED_USDT"])
@pytest.mark.parametrize("explicit", [False, True])
async def test_automated_defaults_do_not_change_manual_margin_or_leverage(mode, explicit):
    from dataclasses import replace

    venue, dispatch = _budget_venue_and_dispatch("3", "0.5")
    dispatch = replace(
        dispatch,
        is_manual=True,
        manual_margin=Decimal("0.5") if explicit else None,
        manual_leverage=10 if explicit else None,
    )
    admission = await _bitget_dispatch_preflight(
        venue,
        {
            "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
            "BITGET_MARGIN_MODE_TYPE": mode,
            "BITGET_ALLOCATION_PCT": "0.20",
            "BITGET_FIXED_MARGIN_USDT": "0.1",
            "BITGET_DEFAULT_LEVERAGE": "5",
        },
        reservation_repository=Reservations(),
    )(dispatch)

    assert admission.submission.planned_margin_usdt == Decimal("0.5" if explicit else "0.6")
    assert admission.submission.effective_leverage == (10 if explicit else 20)


@pytest.mark.parametrize(
    "key,value",
    [
        ("BITGET_DEFAULT_LEVERAGE", "4"),
        ("BITGET_DEFAULT_LEVERAGE", "21"),
        ("BITGET_DEFAULT_LEVERAGE", "10.5"),
        ("BITGET_FIXED_MARGIN_USDT", "0"),
        ("BITGET_FIXED_MARGIN_USDT", "-1"),
        ("BITGET_FIXED_MARGIN_USDT", "NaN"),
        ("BITGET_FIXED_MARGIN_USDT", "Infinity"),
        ("BITGET_FIXED_MARGIN_USDT", "1.01"),
        ("BITGET_MAX_MARGIN_PER_TRADE_USDT", "2"),
    ],
)
@pytest.mark.parametrize("mode", ["PERCENTAGE", "FIXED_USDT"])
def test_configured_sizing_cannot_bypass_pinned_safety_policy(key, value, mode):
    with pytest.raises(ValueError):
        _bitget_dispatch_preflight(
            Venue(),
            {
                "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
                "BITGET_MARGIN_MODE_TYPE": mode,
                key: value,
            },
            reservation_repository=Reservations(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"manual_margin": Decimal("1.01")},
        {"manual_leverage": 4},
        {"manual_leverage": 21},
    ],
)
async def test_automated_configuration_cannot_relax_manual_safety_bounds(overrides):
    from dataclasses import replace

    venue, dispatch = _budget_venue_and_dispatch("3", "10")
    reservations = Reservations()
    dispatch = replace(dispatch, is_manual=True, **overrides)
    with pytest.raises(ValueError, match="manual"):
        await _bitget_dispatch_preflight(
            venue,
            {
                "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
                "BITGET_MARGIN_MODE_TYPE": "FIXED_USDT",
                "BITGET_FIXED_MARGIN_USDT": "1",
                "BITGET_DEFAULT_LEVERAGE": "5",
            },
            reservation_repository=reservations,
        )(dispatch)
    assert reservations.kwargs is None
