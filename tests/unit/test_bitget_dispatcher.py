from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import uuid4

import pytest

from fatty_trader.domain.enums import Exchange, MarginMode
from fatty_trader.domain.models import InstrumentSpec, VenueRiskConfig
from fatty_trader.execution.bitget_dispatch_repository import BitgetDispatch
from fatty_trader.execution.bitget_dispatcher import BitgetDispatcher, DispatchGate


class Repository:
    def __init__(self, dispatch: BitgetDispatch | None) -> None:
        self.dispatch = dispatch
        self.transitions: list[tuple[str, str, str | None]] = []
        self.alerts: list[str] = []

    def claim(self, worker_id: str, lease_seconds: int) -> BitgetDispatch | None:
        assert worker_id == "worker"
        assert lease_seconds == 30
        return self.dispatch

    def canary_entry_count(self, exchange: str) -> int:
        assert exchange == "bitget"
        return 0

    def reserve_canary_entry(self, dispatch_id: object, exchange: str, max_orders: int) -> bool:
        assert self.dispatch is not None and dispatch_id == self.dispatch.id
        assert exchange == "bitget"
        assert max_orders > 0
        return True

    def transition(
        self,
        dispatch_id: object,
        *,
        expected_state: str,
        target_state: str,
        reason: str | None = None,
    ) -> None:
        assert self.dispatch is not None and dispatch_id == self.dispatch.id
        self.transitions.append((expected_state, target_state, reason))

    def alert(self, dispatch_id: object, reason: str) -> None:
        assert self.dispatch is not None and dispatch_id == self.dispatch.id
        self.alerts.append(reason)


@dataclass
class Execution:
    post_count: int = 0

    async def submit_entry(self, dispatch: BitgetDispatch, quantity: Decimal) -> str:
        self.post_count += 1
        assert dispatch.pair_token == "BTCUSDT"
        assert quantity == Decimal("0.002")
        return "FILLED"


def _dispatch(*, take_profits: tuple[Decimal, ...] = (Decimal("65000"),)) -> BitgetDispatch:
    return BitgetDispatch(
        id=uuid4(),
        state="QUEUED",
        claimed_by="worker",
        attempts=1,
        pair_token="BTCUSDT",
        direction="LONG",
        entry_price=Decimal("64000"),
        stop_loss=Decimal("63000"),
        take_profits=take_profits,
    )


def _spec() -> InstrumentSpec:
    return InstrumentSpec(
        exchange=Exchange.BITGET,
        symbol="BTCUSDT",
        qty_step=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("5"),
        max_leverage=20,
    )


def _risk() -> VenueRiskConfig:
    return VenueRiskConfig(
        exchange=Exchange.BITGET,
        base_margin_usdt=Decimal("10"),
        default_leverage=20,
        max_leverage=20,
        max_auto_margin_usdt=Decimal("20"),
        free_margin_usdt=Decimal("100"),
        free_margin_headroom_pct=Decimal("0.5"),
        max_position_notional_usdt=Decimal("1000"),
        margin_mode=MarginMode.ISOLATED,
    )


def test_pair_token_is_normalized_to_bitget_usdt_symbol() -> None:
    from fatty_trader.execution.bitget_dispatcher import _bitget_symbol

    assert _bitget_symbol("pump") == "PUMPUSDT"
    assert _bitget_symbol("SUSHIUSDT") == "SUSHIUSDT"


@pytest.mark.asyncio
async def test_closed_gate_blocks_invalid_dispatch_without_provider_post() -> None:
    repository = Repository(_dispatch(take_profits=()))
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=False),
        execution=execution,
        preflight=lambda _: (_spec(), _risk()),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "cutover-gated"
    assert execution.post_count == 0
    assert repository.transitions == [("QUEUED", "REJECTED", "cutover-gated")]
    assert repository.alerts == []


@pytest.mark.asyncio
async def test_invalid_geometry_or_missing_take_profit_never_posts_when_gate_is_open() -> None:
    repository = Repository(_dispatch(take_profits=()))
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=execution,
        preflight=lambda _: (_spec(), _risk()),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "rejected"
    assert execution.post_count == 0
    assert repository.transitions == [("QUEUED", "REJECTED", "missing-take-profits")]


@pytest.mark.asyncio
async def test_persistent_kill_switch_blocks_before_provider_post() -> None:
    class KillSwitch:
        def is_active(self, scope: str) -> bool:
            return scope == "bitget"

    repository = Repository(_dispatch())
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=execution,
        preflight=lambda _: (_spec(), _risk()),
        kill_switch=KillSwitch(),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "kill-switch-latched"
    assert execution.post_count == 0
    assert repository.transitions == [("QUEUED", "REJECTED", "kill-switch-latched")]
    assert repository.alerts == ["kill-switch-latched"]


@pytest.mark.asyncio
async def test_valid_dispatch_persists_only_the_explicit_state_order() -> None:
    repository = Repository(_dispatch())
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=execution,
        preflight=lambda _: (_spec(), _risk()),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "filled"
    assert execution.post_count == 1
    assert repository.transitions == [
        ("QUEUED", "PREFLIGHT", None),
        ("PREFLIGHT", "SIZED", None),
        ("SIZED", "VALIDATED", None),
        ("VALIDATED", "SUBMITTING", None),
        ("SUBMITTING", "FILLED", None),
    ]


@pytest.mark.asyncio
async def test_provider_rejection_returns_rejected_dispatcher_status() -> None:
    class RejectedExecution:
        async def submit_entry(self, dispatch: BitgetDispatch, quantity: Decimal) -> str:
            return "REJECTED"

    repository = Repository(_dispatch())
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=RejectedExecution(),
        preflight=lambda _: (_spec(), _risk()),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "rejected"
    assert repository.transitions[-1] == ("SUBMITTING", "REJECTED", None)


@pytest.mark.asyncio
async def test_explicit_bounded_canary_rejects_non_canary_symbol_before_preflight_or_post() -> None:
    repository = Repository(_dispatch())
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True, canary_max_orders=1, canary_symbol="ETHUSDT"),
        execution=execution,
        preflight=lambda _: (_ for _ in ()).throw(AssertionError("must not preflight")),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "rejected"
    assert execution.post_count == 0
    assert repository.transitions == [("QUEUED", "REJECTED", "canary-symbol-mismatch")]


@pytest.mark.asyncio
async def test_bounded_demo_cap_without_symbol_allows_dynamic_contract() -> None:
    repository = Repository(replace(_dispatch(), pair_token="NOT"))

    @dataclass
    class DynamicExecution:
        post_count: int = 0

        async def submit_entry(self, dispatch: BitgetDispatch, quantity: Decimal) -> str:
            self.post_count += 1
            assert dispatch.pair_token == "NOTUSDT"
            return "FILLED"

    execution = DynamicExecution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True, canary_max_orders=100, canary_symbol=None),
        execution=execution,
        preflight=lambda _: (_spec().model_copy(update={"symbol": "NOTUSDT"}), _risk()),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "filled"
    assert execution.post_count == 1


@pytest.mark.asyncio
async def test_atomic_canary_reservation_rejects_at_cap_before_provider_post() -> None:
    class CappedRepository(Repository):
        def reserve_canary_entry(self, dispatch_id: object, exchange: str, max_orders: int) -> bool:
            super().reserve_canary_entry(dispatch_id, exchange, max_orders)
            return False

    repository = CappedRepository(_dispatch())
    execution = Execution()
    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True, canary_max_orders=1, canary_symbol="BTCUSDT"),
        execution=execution,
        preflight=lambda _: (_spec(), _risk()),
    )

    result = await dispatcher.run_once("worker", 30)

    assert result == "rejected"
    assert execution.post_count == 0
    assert repository.transitions[-1] == ("VALIDATED", "REJECTED", "canary-order-cap-reached")
