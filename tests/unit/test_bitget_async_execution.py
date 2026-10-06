"""TDD coverage for the production async Bitget execution adapter (fakes only)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from fatty_trader.domain.enums import Direction, Exchange
from fatty_trader.exchanges.bitget.async_execution import AsyncBitgetExecution
from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue
from fatty_trader.exchanges.bitget.client import BitgetApiError, BitgetUnknownResultError
from fatty_trader.exchanges.bitget.live import LiveIntentRecord, LiveOrderStatus
from fatty_trader.execution.protection import ProtectionPlan, ProtectionState


class FakeAsyncClient:
    def __init__(self) -> None:
        self.entry_calls: list[dict[str, Any]] = []

    async def get_account(self, symbol: str) -> dict[str, str]:
        return {
            "available": "100",
            "usdtEquity": "100",
            "accountEquity": "100",
            "marginCoin": "USDT",
            "marginMode": "isolated",
            "posMode": "one_way_mode",
            "isolatedLongLever": "20",
            "isolatedShortLever": "20",
        }

    async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
        return []

    async def get_pending_orders(self, symbol: str) -> list[dict[str, str]]:
        return []

    async def get_contracts(self) -> list[dict[str, str]]:
        return [
            {
                "symbol": "BTCUSDT",
                "pricePlace": "2",
                "priceEndStep": "0.01",
                "sizeMultiplier": "0.001",
                "minTradeNum": "0.001",
                "maxTradeNum": "100",
                "minTradeUSDT": "5",
                "maxLever": "50",
                "contractValue": "1",
            }
        ]

    async def get_ticker(self, symbol: str) -> dict[str, str]:
        return {"lastPr": "50000.00"}

    async def get_clock_skew_ms(self) -> int:
        return 0

    async def set_margin_mode(self, symbol: str, margin_mode: str) -> dict[str, str]:
        return {"marginMode": margin_mode}

    async def set_leverage(self, symbol: str, leverage: str) -> dict[str, str]:
        self.leverage_set = (symbol, leverage)
        return {"leverage": leverage}

    async def place_entry_order(self, **kwargs: str) -> dict[str, str]:
        self.entry_calls.append(kwargs)
        return {"orderId": "provider-1", "clientOid": kwargs["client_oid"]}

    async def get_order_detail(self, symbol: str, *, client_oid: str) -> dict[str, str]:
        return {"status": "filled", "requestedQty": "0.001", "orderId": "provider-1"}

    async def get_fills(self, symbol: str) -> list[dict[str, str]]:
        return [
            {
                "fillId": "fill-1",
                "orderId": "provider-1",
                "price": "50000",
                "size": "0.001",
                "fee": "0.2",
            }
        ]

    async def aclose(self) -> None:
        self.closed = True


def _admitted_intent() -> LiveIntentRecord:
    from uuid import UUID

    return LiveIntentRecord(
        exchange="bitget",
        client_oid="live-bitget-BTCUSDT-0011223344556677",
        symbol="BTCUSDT",
        side="BUY",
        requested_qty=Decimal("0.001"),
        planned_leverage=20,
        planned_margin_usdt=Decimal("10"),
        planned_notional_usdt=Decimal("50"),
        margin_mode="ISOLATED",
        balance_snapshot_id=UUID("12345678-1234-5678-1234-567812345678"),
        margin_reservation_id=UUID("87654321-4321-8765-4321-876543218765"),
    )


@pytest.mark.asyncio
async def test_permission_fence_covers_post_but_not_readback() -> None:
    from contextlib import contextmanager

    held = False

    @contextmanager
    def permission():
        nonlocal held
        held = True
        try:
            yield
        finally:
            held = False

    class Client(FakeAsyncClient):
        async def place_entry_order(self, **kwargs):
            assert held, "ENTRY POST must hold the permission fence"
            return await super().place_entry_order(**kwargs)

        async def get_order_detail(self, symbol, *, client_oid):
            assert not held, "GET reconciliation must not hold the kill fence"
            return await super().get_order_detail(symbol, client_oid=client_oid)

    client = Client()
    await AsyncBitgetExecution(client, AsyncBitgetVenue(client)).submit_entry_guarded(
        _admitted_intent(), permission
    )
    assert len(client.entry_calls) == 1
    assert not held


@pytest.mark.asyncio
async def test_adapter_closes_the_owned_rest_client() -> None:
    client = FakeAsyncClient()
    client.closed = False
    await AsyncBitgetExecution(client, AsyncBitgetVenue(client)).aclose()
    assert client.closed is True


@pytest.mark.asyncio
async def test_submit_entry_preflights_serializes_decimals_and_reconciles_readback() -> None:
    client = FakeAsyncClient()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))
    intent = _admitted_intent()

    result = await adapter.submit_entry(intent)

    assert client.entry_calls == [
        {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "quantity": "0.001",
            "client_oid": "live-bitget-BTCUSDT-0011223344556677",
        }
    ]
    assert result.status is LiveOrderStatus.FILLED
    assert result.filled_qty == Decimal("0.001")
    assert result.avg_price == Decimal("50000")
    assert result.fee == Decimal("0.2")
    assert result.provider_order_id == "provider-1"


@pytest.mark.asyncio
async def test_submit_entry_sets_and_verifies_intent_leverage_before_post() -> None:
    client = FakeAsyncClient()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))
    intent = LiveIntentRecord(
        exchange="bitget",
        client_oid="live-bitget-BTCUSDT-0011223344556677",
        symbol="BTCUSDT",
        side="BUY",
        requested_qty=Decimal("0.001"),
        planned_leverage=20,
        planned_margin_usdt=Decimal("10"),
        planned_notional_usdt=Decimal("50"),
        margin_mode="ISOLATED",
        balance_snapshot_id=__import__("uuid").UUID("12345678-1234-5678-1234-567812345678"),
        margin_reservation_id=__import__("uuid").UUID("87654321-4321-8765-4321-876543218765"),
    )

    await adapter.submit_entry(intent)

    assert client.leverage_set == ("BTCUSDT", "20")
    assert len(client.entry_calls) == 1


@pytest.mark.asyncio
async def test_unknown_post_result_reconciles_with_symbol_reads_without_a_second_post() -> None:
    class TimeoutClient(FakeAsyncClient):
        async def place_entry_order(self, **kwargs: str) -> dict[str, str]:
            self.entry_calls.append(kwargs)
            raise BitgetUnknownResultError("POST result unknown")

    client = TimeoutClient()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))
    intent = _admitted_intent()

    result = await adapter.submit_entry(intent)

    assert len(client.entry_calls) == 1
    assert result.status is LiveOrderStatus.FILLED


@pytest.mark.asyncio
async def test_unreadable_order_detail_with_matching_fill_is_filled() -> None:
    class UnreadableDetailClient(FakeAsyncClient):
        async def get_order_detail(self, symbol: str, *, client_oid: str) -> dict[str, str]:
            raise BitgetApiError("Bitget error 40109: cannot be found", code="40109")

    client = UnreadableDetailClient()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))
    intent = _admitted_intent()

    result = await adapter.submit_entry(intent)

    assert result.status is LiveOrderStatus.FILLED
    assert result.filled_qty == Decimal("0.001")
    assert result.provider_order_id == "provider-1"


@pytest.mark.asyncio
async def test_40109_without_fill_is_rejected_only_after_flat_and_no_pending_reads() -> None:
    class MissingOrderClient(FakeAsyncClient):
        async def get_order_detail(self, symbol: str, *, client_oid: str) -> dict[str, str]:
            raise BitgetApiError("Bitget error 40109: cannot be found", code="40109")

        async def get_fills(self, symbol: str) -> list[dict[str, str]]:
            return []

        async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
            return []

        async def get_pending_orders(self, symbol: str) -> list[dict[str, str]]:
            return []

    client = MissingOrderClient()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))
    intent = _admitted_intent()

    result = await adapter.submit_entry(intent)

    assert result.status is LiveOrderStatus.REJECTED
    assert result.filled_qty == Decimal("0")


@pytest.mark.asyncio
async def test_40109_without_fill_and_non_flat_position_stays_unknown() -> None:
    class NonFlatClient(FakeAsyncClient):
        async def get_order_detail(self, symbol: str, *, client_oid: str) -> dict[str, str]:
            raise BitgetApiError("Bitget error 40109: cannot be found", code="40109")

        async def get_fills(self, symbol: str) -> list[dict[str, str]]:
            return []

        async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
            return [{"symbol": symbol, "total": "0.001"}]

    client = NonFlatClient()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))
    intent = LiveIntentRecord("bitget", "oid", "BTCUSDT", "BUY", requested_qty=Decimal("0.001"))

    result = await adapter.reconcile_intent(intent)

    assert result.status is LiveOrderStatus.UNKNOWN


@pytest.mark.asyncio
async def test_40109_without_fill_and_pending_order_stays_unknown() -> None:
    class PendingClient(FakeAsyncClient):
        async def get_order_detail(self, symbol: str, *, client_oid: str) -> dict[str, str]:
            raise BitgetApiError("Bitget error 40109: cannot be found", code="40109")

        async def get_fills(self, symbol: str) -> list[dict[str, str]]:
            return []

        async def get_pending_orders(self, symbol: str) -> list[dict[str, str]]:
            return [{"symbol": symbol, "orderId": "pending-1"}]

    client = PendingClient()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))
    intent = LiveIntentRecord("bitget", "oid", "BTCUSDT", "BUY", requested_qty=Decimal("0.001"))

    result = await adapter.reconcile_intent(intent)

    assert result.status is LiveOrderStatus.UNKNOWN


@pytest.mark.asyncio
async def test_40109_without_fill_and_read_failure_stays_unknown() -> None:
    class FailingPositionClient(FakeAsyncClient):
        async def get_order_detail(self, symbol: str, *, client_oid: str) -> dict[str, str]:
            raise BitgetApiError("Bitget error 40109: cannot be found", code="40109")

        async def get_fills(self, symbol: str) -> list[dict[str, str]]:
            return []

        async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
            raise BitgetApiError("position read failed")

    client = FailingPositionClient()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))
    intent = LiveIntentRecord("bitget", "oid", "BTCUSDT", "BUY", requested_qty=Decimal("0.001"))

    result = await adapter.reconcile_intent(intent)

    assert result.status is LiveOrderStatus.UNKNOWN


@pytest.mark.asyncio
async def test_matching_fill_can_terminalize_missing_detail_after_complete_reads() -> None:
    class MatchingFillClient(FakeAsyncClient):
        async def get_order_detail(self, symbol: str, *, client_oid: str) -> dict[str, str]:
            raise BitgetApiError("Bitget error 40109: cannot be found", code="40109")

        async def get_fills(self, symbol: str) -> list[dict[str, str]]:
            return [
                {
                    "fillId": "fill-1",
                    "orderId": "provider-1",
                    "price": "50000",
                    "size": "0.001",
                    "fee": "0",
                }
            ]

    client = MatchingFillClient()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))
    intent = LiveIntentRecord(
        "bitget",
        "oid",
        "BTCUSDT",
        "BUY",
        requested_qty=Decimal("0.001"),
        provider_order_id="provider-1",
    )

    result = await adapter.reconcile_intent(intent)

    assert result.status is LiveOrderStatus.FILLED


@pytest.mark.asyncio
async def test_matching_fill_with_unconsumed_fill_cursor_stays_unknown() -> None:
    class PaginatedFillClient(FakeAsyncClient):
        async def get_order_detail(self, symbol: str, *, client_oid: str) -> dict[str, str]:
            raise BitgetApiError("Bitget error 40109: cannot be found", code="40109")

        async def get_fills(self, symbol: str) -> dict[str, Any]:
            return {
                "fillList": [{"orderId": "provider-1", "price": "50000", "size": "0.001"}],
                "endId": "next-page",
            }

    client = PaginatedFillClient()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))
    intent = LiveIntentRecord(
        "bitget",
        "oid",
        "BTCUSDT",
        "BUY",
        requested_qty=Decimal("0.001"),
        provider_order_id="provider-1",
    )

    result = await adapter.reconcile_intent(intent)

    assert result.status is LiveOrderStatus.UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize("known_source", ["detail", "durable"])
async def test_partial_fill_page_cannot_shrink_known_fill_or_trigger_another_post(known_source):
    class PartialPageClient(FakeAsyncClient):
        async def get_order_detail(self, symbol, *, client_oid):
            return {
                "status": "partially_filled",
                "requestedQty": "10",
                "filledQty": "10" if known_source == "detail" else "2",
                "avgPrice": "100",
                "orderId": "provider-1",
            }

        async def get_fills(self, symbol):
            return [
                {
                    "tradeId": "page-fill",
                    "orderId": "provider-1",
                    "baseVolume": "2",
                    "price": "100",
                    "fee": "0.2",
                }
            ]

    client = PartialPageClient()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))
    intent = _admitted_intent()
    intent.requested_qty = Decimal("10")
    if known_source == "durable":
        intent.filled_qty = Decimal("10")
        intent.avg_price = Decimal("100")
    for _ in range(2):
        result = await adapter.reconcile_intent(intent)
        assert result.status is LiveOrderStatus.UNKNOWN
        assert result.filled_qty == Decimal("10")
        assert result.avg_price == Decimal("100")
        assert result.provider_order_id == "provider-1"
        assert result.provider_fill_ids == ("page-fill",)
        assert result.provider_fills[0]["baseVolume"] == "2"
        assert result.fee == Decimal("0.2")
    assert client.entry_calls == []


@pytest.mark.asyncio
async def test_incomplete_page_preserves_previous_real_fill_evidence():
    class PageClient(FakeAsyncClient):
        async def get_order_detail(self, symbol, *, client_oid):
            return {
                "status": "filled",
                "filledQty": "10",
                "avgPrice": "100",
                "orderId": "provider-1",
            }

        async def get_fills(self, symbol):
            return [
                {
                    "tradeId": "new-fill",
                    "orderId": "provider-1",
                    "size": "2",
                    "price": "100",
                    "fee": "0.2",
                }
            ]

    client = PageClient()
    intent = _admitted_intent()
    intent.requested_qty = intent.filled_qty = Decimal("10")
    intent.provider_fill_ids = ("old-fill",)
    intent.provider_fills = (
        {"tradeId": "old-fill", "orderId": "provider-1", "size": "8", "price": "100", "fee": "0.8"},
    )
    result = await AsyncBitgetExecution(client, AsyncBitgetVenue(client)).reconcile_intent(intent)
    assert result.status is LiveOrderStatus.UNKNOWN
    assert result.filled_qty == Decimal("10")
    assert set(result.provider_fill_ids) == {"old-fill", "new-fill"}
    assert len(result.provider_fills) == 2
    assert result.fee == Decimal("1")
    assert client.entry_calls == []


@pytest.mark.asyncio
async def test_nonempty_detail_does_not_discard_unconsumed_fill_cursor():
    class CursorClient(FakeAsyncClient):
        async def get_fills(self, symbol):
            return {"fillList": await super().get_fills(symbol), "endId": "next-page"}

    client = CursorClient()
    adapter = AsyncBitgetExecution(client, AsyncBitgetVenue(client))
    result = await adapter.reconcile_intent(_admitted_intent())
    assert result.status is LiveOrderStatus.UNKNOWN
    assert result.filled_qty == Decimal("0.001")
    assert result.provider_fill_ids == ("fill-1",)
    assert client.entry_calls == []


@pytest.mark.asyncio
async def test_confirmed_fill_persists_provider_observed_margin_and_leverage() -> None:
    class PositionClient(FakeAsyncClient):
        async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
            return [
                {
                    "symbol": symbol,
                    "holdSide": "long",
                    "total": "0.001",
                    "openPriceAvg": "50000",
                    "markPrice": "50010",
                    "marginSize": "10",
                    "marginMode": "isolated",
                    "leverage": "20",
                }
            ]

    class Reconciliations:
        def __init__(self) -> None:
            self.observations: list[Any] = []

        def record(self, observation: Any) -> None:
            self.observations.append(observation)

    client = PositionClient()
    reconciliations = Reconciliations()
    adapter = AsyncBitgetExecution(
        client, AsyncBitgetVenue(client), reconciliation_repository=reconciliations
    )

    await adapter.submit_entry(_admitted_intent())

    assert len(reconciliations.observations) == 1
    observation = reconciliations.observations[0]
    assert observation.status == "matched"
    assert observation.planned_leverage == 20
    assert observation.planned_margin_usdt == Decimal("10")
    assert observation.observed_leverage == Decimal("20")
    assert observation.observed_margin_usdt == Decimal("10")
    assert observation.observed_margin_mode == "ISOLATED"
    assert observation.observed_quantity == Decimal("0.001")


@pytest.mark.asyncio
async def test_post_fill_leverage_mismatch_latches_future_entries_closed() -> None:
    class MismatchClient(FakeAsyncClient):
        async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
            return [
                {
                    "symbol": symbol,
                    "holdSide": "long",
                    "total": "0.001",
                    "openPriceAvg": "50000",
                    "markPrice": "50010",
                    "marginSize": "10",
                    "marginMode": "isolated",
                    "leverage": "21",
                }
            ]

    class Reconciliations:
        def __init__(self) -> None:
            self.observations: list[Any] = []

        def record(self, observation: Any) -> None:
            self.observations.append(observation)

    client = MismatchClient()
    reconciliations = Reconciliations()
    adapter = AsyncBitgetExecution(
        client, AsyncBitgetVenue(client), reconciliation_repository=reconciliations
    )

    await adapter.submit_entry(_admitted_intent())

    assert reconciliations.observations[0].status == "mismatch"
    assert adapter.degraded is True
    with pytest.raises(RuntimeError, match="degraded"):
        await adapter.submit_entry(_admitted_intent())
    assert len(client.entry_calls) == 1


@pytest.mark.asyncio
async def test_post_fill_mismatch_is_recorded_without_durable_dispatch_latch() -> None:
    class MismatchClient(FakeAsyncClient):
        async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
            return [
                {
                    "symbol": symbol,
                    "holdSide": "long",
                    "total": "0.001",
                    "openPriceAvg": "50000",
                    "markPrice": "50010",
                    "marginSize": "10",
                    "marginMode": "isolated",
                    "leverage": "21",
                }
            ]

    class Reconciliations:
        def __init__(self) -> None:
            self.observations: list[Any] = []

        def record(self, observation: Any) -> None:
            self.observations.append(observation)

    client = MismatchClient()
    reconciliations = Reconciliations()
    adapter = AsyncBitgetExecution(
        client,
        AsyncBitgetVenue(client),
        reconciliation_repository=reconciliations,
    )

    await adapter.submit_entry(_admitted_intent())

    assert reconciliations.observations[0].status == "mismatch"


class _NativeUnsupportedClient(FakeAsyncClient):
    """Fake whose native TP/SL placement fails with Bitget 43011."""

    def __init__(self) -> None:
        super().__init__()
        self.fallback_registrations: list[dict[str, Any]] = []

    async def place_position_tpsl(self, **kwargs: str) -> list[dict[str, str]]:
        raise BitgetApiError("code 43011: symbol does not support stop loss")


def _filled_entry_intent() -> LiveIntentRecord:
    intent = _admitted_intent()
    return LiveIntentRecord(
        exchange=intent.exchange,
        client_oid=intent.client_oid,
        symbol=intent.symbol,
        side=intent.side,
        requested_qty=intent.requested_qty,
        planned_leverage=intent.planned_leverage,
        planned_margin_usdt=intent.planned_margin_usdt,
        planned_notional_usdt=intent.planned_notional_usdt,
        margin_mode=intent.margin_mode,
        balance_snapshot_id=intent.balance_snapshot_id,
        margin_reservation_id=intent.margin_reservation_id,
        filled_qty=Decimal("0.001"),
        avg_price=Decimal("50000"),
    )


def _protection_plan() -> ProtectionPlan:
    return ProtectionPlan(
        exchange=Exchange.BITGET,
        symbol="BTCUSDT",
        direction=Direction.LONG,
        stop_loss=Decimal("48000"),
        take_profits=(Decimal("54000"),),
        quantity=Decimal("0.001"),
    )


class _RecordingStore:
    def __init__(self) -> None:
        self.claimed: list[LiveIntentRecord] = []

    def claim(self, intent: LiveIntentRecord) -> bool:
        self.claimed.append(intent)
        return True


@pytest.mark.asyncio
async def test_native_unsupported_fallback_is_refused_when_monitor_cannot_mutate() -> None:
    client = _NativeUnsupportedClient()
    adapter = AsyncBitgetExecution(
        client,
        AsyncBitgetVenue(client),
        fallback_protection_enabled=False,
    )

    result = await adapter.protect_filled_position(
        _filled_entry_intent(), _protection_plan(), _RecordingStore()
    )

    assert result.state is ProtectionState.DEGRADED
    assert result.reason == "fallback-monitor-mutations-disabled"
    assert adapter.degraded is True


@pytest.mark.asyncio
async def test_native_unsupported_fallback_registers_when_monitor_can_mutate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fatty_trader.execution.bitget_fallback_protection as fallback_protection

    registrations: list[dict[str, Any]] = []
    monkeypatch.setattr(
        fallback_protection,
        "register_fallback",
        lambda **kwargs: registrations.append(kwargs),
    )
    client = _NativeUnsupportedClient()
    adapter = AsyncBitgetExecution(
        client,
        AsyncBitgetVenue(client),
        fallback_protection_enabled=True,
    )

    result = await adapter.protect_filled_position(
        _filled_entry_intent(), _protection_plan(), _RecordingStore()
    )

    assert result.state is ProtectionState.DEGRADED
    assert result.reason == "fallback-registration-failed"
    assert adapter.degraded is True
    assert registrations == []
