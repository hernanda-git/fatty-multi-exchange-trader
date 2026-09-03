from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from fatty_trader.domain.enums import DispatchState, Exchange
from fatty_trader.domain.models import CanonicalSignal, SizingPlan
from fatty_trader.execution.service import (
    DurableExecutionService,
    PaperOrderRequest,
    PaperOrderResult,
)
from fatty_trader.storage.memory import InMemoryDispatchRepository


def signal() -> CanonicalSignal:
    return CanonicalSignal(
        source_message_id=42,
        source_revision="a" * 64,
        pair_token="BTCUSDT",
        direction="LONG",
        entry_price=Decimal("64000"),
        stop_loss=Decimal("63000"),
        take_profits=(Decimal("65000"),),
    )


def test_claim_lease_is_exclusive_and_expired_leases_are_reclaimable() -> None:
    repo = InMemoryDispatchRepository()
    dispatch = repo.create(signal(), Exchange.BINANCE)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    claimed = repo.claim("worker-a", now=now, lease_seconds=30)
    assert claimed is not None and claimed.id == dispatch.id
    assert repo.claim("worker-b", now=now, lease_seconds=30) is None
    assert repo.claim("worker-b", now=now + timedelta(seconds=31), lease_seconds=30) is not None
    assert repo.get(dispatch.id).attempts == 2


def test_service_persists_transitions_and_returns_paper_fill() -> None:
    repo = InMemoryDispatchRepository()
    dispatch = repo.create(signal(), Exchange.BINANCE)
    plan = SizingPlan(
        effective_leverage=5,
        effective_margin_usdt=Decimal("20"),
        notional_usdt=Decimal("100"),
        quantity=Decimal("0.001"),
        required_min_notional_usdt=Decimal("64"),
    )
    def adapter(request: PaperOrderRequest) -> PaperOrderResult:
        return PaperOrderResult(
            client_order_id=request.client_order_id,
            venue_order_id="paper-binance-1",
            filled_quantity=request.sizing.quantity,
            average_price=request.signal.entry_price,
        )

    result = DurableExecutionService(repo, {Exchange.BINANCE: adapter}).execute_once(
        "worker", now=datetime(2026, 1, 1, tzinfo=UTC), sizing=plan
    )

    assert result.venue_order_id == "paper-binance-1"
    assert repo.get(dispatch.id).state is DispatchState.FILLED
    assert repo.get(dispatch.id).claimed_by is None


def test_paper_request_has_stable_idempotency_id() -> None:
    first = PaperOrderRequest(signal(), Exchange.BITGET, SizingPlan(
        effective_leverage=3, effective_margin_usdt=Decimal("10"), notional_usdt=Decimal("30"),
        quantity=Decimal("1"), required_min_notional_usdt=Decimal("10"),
    ))
    second = PaperOrderRequest(first.signal, first.exchange, first.sizing)
    assert first.client_order_id == second.client_order_id


def test_service_rejects_missing_adapter_without_claim_leak() -> None:
    repo = InMemoryDispatchRepository()
    dispatch = repo.create(signal(), Exchange.BINANCE)
    with pytest.raises(ValueError, match="adapter"):
        DurableExecutionService(repo, {}).execute_once("worker")
    assert repo.get(dispatch.id).state is DispatchState.QUEUED
