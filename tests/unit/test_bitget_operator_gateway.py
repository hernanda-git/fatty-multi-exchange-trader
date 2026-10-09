from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from fatty_trader.exchanges.bitget.client import BitgetUnknownResultError
from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveIntentRecord
from fatty_trader.operator.bitget_gateway import BitgetOperatorGateway


class FakeBitgetClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.position_rows: list[dict[str, Any]] = [
            {
                "symbol": "BTCUSDT",
                "holdSide": "long",
                "total": "0.01",
                "openPriceAvg": "60000",
                "marginMode": "isolated",
                "apiSecret": "must-not-leak",
            }
        ]
        self.close_result: dict[str, Any] | Exception = {"orderId": "close-1"}

    async def get_ticker(self, symbol: str) -> dict[str, Any]:
        self.calls.append(("get_ticker", symbol))
        return {"symbol": symbol, "lastPr": "60001", "ACCESS-SIGN": "must-not-leak"}

    async def get_account(self) -> dict[str, Any]:
        self.calls.append(("get_account", None))
        return {"available": "99.5", "equity": "100", "apiKey": "must-not-leak"}

    async def get_all_positions(self) -> list[dict[str, Any]]:
        self.calls.append(("get_all_positions", None))
        return self.position_rows

    async def get_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        self.calls.append(("get_pending_orders", symbol))
        return [
            {
                "symbol": "BTCUSDT",
                "orderId": "order-1",
                "side": "buy",
                "price": "50000",
                "size": "0.01",
                "apiSecret": "must-not-leak",
            }
        ]

    async def place_market_close(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("place_market_close", kwargs))
        if isinstance(self.close_result, Exception):
            raise self.close_result
        self.position_rows = []
        return self.close_result

    async def place_position_tpsl(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("place_position_tpsl", kwargs))
        if kwargs.get("stop_loss") is not None:
            self.position_rows[0]["stopLossTriggerPrice"] = kwargs["stop_loss"]
        if kwargs.get("take_profit") is not None:
            self.position_rows[0]["stopSurplusTriggerPrice"] = kwargs["take_profit"]
        return [{"orderId": "plan-1"}]

    async def cancel_all_orders(self, symbol: str | None = None) -> dict[str, Any]:
        self.calls.append(("cancel_all_orders", symbol))
        return {"successList": [{"orderId": "order-1"}]}

    async def cancel_order(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("cancel_order", kwargs))
        return {"orderId": kwargs.get("order_id", "order-1")}

    async def aclose(self) -> None:
        self.calls.append(("aclose", None))


def make_gateway() -> tuple[BitgetOperatorGateway, FakeBitgetClient, InMemoryLiveIntentStore]:
    client = FakeBitgetClient()
    store = InMemoryLiveIntentStore()
    gateway = BitgetOperatorGateway(client, store, client_oid_factory=lambda: "operator-close-1")
    return gateway, client, store


def test_read_only_methods_return_sanitized_provider_dtos() -> None:
    gateway, client, _ = make_gateway()

    assert gateway.get_price("BTCUSDT") == Decimal("60001")
    assert gateway.get_balance() == Decimal("99.5")
    assert gateway.get_positions() == [
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "size": Decimal("0.01"),
            "entry": Decimal("60000"),
            "stop_loss": None,
            "take_profit": None,
            "mark": None,
            "unrealized_pl": None,
            "leverage": None,
            "margin_mode": "isolated",
            "liquidation_price": None,
            "stop_loss_id": None,
            "take_profit_id": None,
        }
    ]
    assert gateway.get_orders() == [
        {
            "symbol": "BTCUSDT",
            "order_id": "order-1",
            "side": "BUY",
            "price": Decimal("50000"),
            "size": Decimal("0.01"),
        }
    ]
    assert all("must-not-leak" not in str(value) for _, value in client.calls)


def test_gateway_reuses_one_event_loop_and_closes_client_on_that_loop() -> None:
    gateway, client, _ = make_gateway()

    gateway.get_price("BTCUSDT")
    first_loop = gateway._loop
    gateway.get_balance()
    gateway.close()

    assert first_loop is not None
    assert gateway._loop is first_loop
    assert ("aclose", None) in client.calls


def test_cancel_symbol_uses_provider_cancel_all_for_exact_symbol() -> None:
    gateway, client, _ = make_gateway()

    result = gateway.cancel_order("BTCUSDT")

    assert result == {"cancelled": "BTCUSDT", "count": 1}
    assert ("cancel_all_orders", "BTCUSDT") in client.calls


def test_cancel_order_id_resolves_provider_symbol_before_cancellation() -> None:
    gateway, client, _ = make_gateway()

    result = gateway.cancel_order("order_id=order-1")

    assert result == {"cancelled": "order-1"}
    assert ("cancel_order", {"symbol": "BTCUSDT", "order_id": "order-1"}) in client.calls


def test_close_position_persists_reduce_only_close_intent_and_reads_back_flat() -> None:
    gateway, client, store = make_gateway()

    result = gateway.close_position("BTCUSDT")

    assert result == {"closed": "BTCUSDT", "state": "closed"}
    intent = store.get("operator-close-1")
    assert intent is not None
    assert intent.role == "CLOSE"
    assert intent.side == "SELL"
    assert intent.requested_qty == Decimal("0.01")
    assert intent.state == "reconciled"
    close = next(value for name, value in client.calls if name == "place_market_close")
    assert isinstance(close, dict)
    assert close["symbol"] == "BTCUSDT"
    assert close["side"] == "SELL"
    assert close["quantity"] == "0.01"
    assert close["client_oid"] == "operator-close-1"


def test_unknown_close_result_is_reconciliation_pending_not_success() -> None:
    gateway, client, store = make_gateway()
    client.close_result = BitgetUnknownResultError("unknown")

    result = gateway.close_position("BTCUSDT")

    assert result == {"closed": "BTCUSDT", "state": "reconciliation-pending"}
    intent = store.get("operator-close-1")
    assert intent is not None
    assert intent.state == "unknown"


def test_set_stop_loss_submits_native_protection_and_reads_back() -> None:
    gateway, client, _ = make_gateway()

    result = gateway.set_position_protection(
        "BTCUSDT", stop_loss=Decimal("59900"), take_profit=None
    )

    assert result == {"symbol": "BTCUSDT", "state": "reconciled"}
    request = next(value for name, value in client.calls if name == "place_position_tpsl")
    assert request == {
        "symbol": "BTCUSDT",
        "hold_side": "long",
        "quantity": "0.01",
        "stop_loss": "59900",
        "take_profit": None,
    }
    assert gateway.get_positions()[0]["stop_loss"] == Decimal("59900")


def test_close_without_matching_open_position_makes_no_close_request() -> None:
    gateway, client, _ = make_gateway()
    client.position_rows = []

    result = gateway.close_position("BTCUSDT")

    assert result == {"closed": "BTCUSDT", "state": "not-open"}
    assert not any(name == "place_market_close" for name, _ in client.calls)


def test_gateway_rejects_calls_from_an_active_async_loop() -> None:
    gateway, _, _ = make_gateway()

    async def invoke() -> None:
        with pytest.raises(RuntimeError, match="synchronous operator gateway"):
            gateway.get_balance()

    asyncio.run(invoke())


@pytest.mark.parametrize("terminal", [True, False, {"success": True}, None])
def test_source_cancel_delegates_exact_owned_lifecycle_not_broad_order_cancel(terminal) -> None:
    class EntryExecution:
        def __init__(self) -> None:
            self.calls: list[tuple[str, UUID]] = []

        async def cancel_pending_entries(self, symbol: str, management_id: UUID) -> object:
            self.calls.append((symbol, management_id))
            return terminal

    client = FakeBitgetClient()
    client.position_rows[0]["stopLossTriggerPrice"] = "59000"
    client.position_rows[0]["stopSurplusTriggerPrice"] = "61000"
    execution = EntryExecution()
    gateway = BitgetOperatorGateway(client, InMemoryLiveIntentStore(), entry_execution=execution)
    management_id = uuid4()

    assert gateway.cancel_pending_entries("btcusdt", management_id) is (terminal is True)
    assert execution.calls == [("BTCUSDT", management_id)]
    assert client.calls == []
    assert client.position_rows[0]["stopLossTriggerPrice"] == "59000"
    assert client.position_rows[0]["stopSurplusTriggerPrice"] == "61000"
    gateway.close()


def test_source_cancel_missing_adapter_is_pending_not_unowned_order_cancellation() -> None:
    from fatty_trader.execution.source_management import SourceManagementReconciliationPending

    gateway, client, _ = make_gateway()
    with pytest.raises(SourceManagementReconciliationPending, match="not wired"):
        gateway.cancel_pending_entries("BTCUSDT", uuid4())
    assert client.calls == []


@pytest.mark.parametrize("error", [BitgetUnknownResultError("unknown"), TimeoutError()])
def test_source_cancel_ambiguous_result_is_pending_and_not_retried(error) -> None:
    from fatty_trader.execution.source_management import SourceManagementReconciliationPending

    class EntryExecution:
        def __init__(self) -> None:
            self.calls = 0

        async def cancel_pending_entries(self, symbol: str, management_id: UUID) -> bool:
            self.calls += 1
            raise error

    client = FakeBitgetClient()
    execution = EntryExecution()
    gateway = BitgetOperatorGateway(client, InMemoryLiveIntentStore(), entry_execution=execution)

    with pytest.raises(SourceManagementReconciliationPending, match="unknown"):
        gateway.cancel_pending_entries("BTCUSDT", uuid4())
    assert execution.calls == 1
    assert client.calls == []
    gateway.close()


class StopModifyClient(FakeBitgetClient):
    def __init__(self, store: InMemoryLiveIntentStore) -> None:
        super().__init__()
        self.store = store
        self.position_rows[0].update(
            posMode="one_way_mode", stopLossId="sl-plan", takeProfitId="tp-plan"
        )
        self.plans = [
            {
                "symbol": "BTCUSDT",
                "holdSide": "long",
                "planStatus": "live",
                "posMode": "one_way_mode",
                "marginMode": "isolated",
                "planType": kind,
                "orderId": order_id,
                "clientOid": "root-entry",
                "triggerPrice": trigger,
                "triggerType": "mark_price",
                "executePrice": "0",
                "size": "",
            }
            for kind, order_id, trigger in (
                ("pos_loss", "sl-plan", "59000"),
                ("pos_profit", "tp-plan", "62000"),
            )
        ]
        self.modify_error: Exception | None = None
        self.apply_modify = True
        self.change_tp = False

    async def get_contracts(self) -> list[dict[str, str]]:
        return [
            {
                "symbol": "BTCUSDT",
                "pricePlace": "1",
                "priceEndStep": "5",
                "sizeMultiplier": "0.001",
                "minTradeNum": "0.001",
                "maxLever": "125",
            }
        ]

    async def get_pending_plan_orders(self, symbol: str) -> list[dict[str, Any]]:
        self.calls.append(("get_pending_plan_orders", symbol))
        return self.plans

    async def modify_position_stop_loss(self, **kwargs: Any) -> dict[str, str]:
        intent = self.store.get("management-sl")
        assert intent is not None and intent.state == "requested"
        assert intent.planned_stop_loss == Decimal(kwargs["trigger_price"])
        assert intent.provider_order_id == "sl-plan"
        self.calls.append(("modify_position_stop_loss", kwargs))
        if self.apply_modify:
            self.plans[0]["triggerPrice"] = kwargs["trigger_price"]
        if self.change_tp:
            self.position_rows[0]["takeProfitId"] = "different-tp"
        if self.modify_error is not None:
            raise self.modify_error
        return {"orderId": "sl-plan", "clientOid": "root-entry"}


def stop_gateway() -> tuple[BitgetOperatorGateway, StopModifyClient, InMemoryLiveIntentStore]:
    store = InMemoryLiveIntentStore()
    store.save(
        LiveIntentRecord(
            "bitget",
            "root-entry",
            "BTCUSDT",
            "BUY",
            filled_qty=Decimal("0.01"),
            planned_stop_loss=Decimal("59000"),
            planned_take_profits=(Decimal("62000"),),
        )
    )
    client = StopModifyClient(store)
    return BitgetOperatorGateway(client, store), client, store


def move_stop(gateway: BitgetOperatorGateway) -> None:
    gateway.replace_stop_loss(
        symbol="BTCUSDT",
        side="LONG",
        quantity=Decimal("0.01"),
        stop_loss=Decimal("60000.24"),
        client_oid="management-sl",
    )


def test_source_stop_modifies_exact_owned_sl_preserves_full_tp_and_rounds_tick() -> None:
    gateway, client, store = stop_gateway()
    move_stop(gateway)
    assert (
        "modify_position_stop_loss",
        {
            "symbol": "BTCUSDT",
            "order_id": "sl-plan",
            "client_oid": "root-entry",
            "trigger_price": "60000.0",
        },
    ) in client.calls
    assert store.get("management-sl").state == "reconciled"
    assert store.get("root-entry").active_stop_loss_price == Decimal("60000")
    assert client.plans[1]["orderId"] == "tp-plan"
    assert client.plans[1]["triggerPrice"] == "62000"
    assert not any(
        name.startswith("cancel") or name == "place_position_tpsl" for name, _ in client.calls
    )
    gateway.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("clientOid", "unowned"),
        ("triggerType", "fill_price"),
        ("executePrice", "1"),
        ("holdSide", "short"),
        ("symbol", "ETHUSDT"),
        ("posMode", "hedge_mode"),
        ("marginMode", "crossed"),
        ("size", "0.01"),
        ("planStatus", "cancelled"),
        ("triggerPrice", "58000"),
    ],
)
def test_source_stop_rejects_nonmatching_old_native_plan_before_mutation(field, value) -> None:
    gateway, client, store = stop_gateway()
    client.plans[0][field] = value
    with pytest.raises(ValueError):
        move_stop(gateway)
    assert store.get("management-sl") is None
    assert not any(name == "modify_position_stop_loss" for name, _ in client.calls)
    gateway.close()


@pytest.mark.parametrize("apply_modify", [True, False])
def test_ambiguous_stop_modify_is_get_reconciled_never_posted_again(apply_modify) -> None:
    from fatty_trader.execution.source_management import SourceManagementReconciliationPending

    gateway, client, store = stop_gateway()
    client.modify_error = BitgetUnknownResultError("unknown")
    client.apply_modify = apply_modify
    with pytest.raises(SourceManagementReconciliationPending, match="unknown"):
        move_stop(gateway)
    assert store.get("management-sl").state == "unknown"
    if apply_modify:
        gateway.reconcile_stop_loss("management-sl")
        assert store.get("management-sl").state == "reconciled"
    else:
        with pytest.raises(SourceManagementReconciliationPending):
            gateway.reconcile_stop_loss("management-sl")
    assert sum(name == "modify_position_stop_loss" for name, _ in client.calls) == 1
    gateway.close()


@pytest.mark.parametrize("flat", [True, False])
def test_stop_modify_neither_flat_nor_replaced_tp_proves_completion(flat) -> None:
    from fatty_trader.execution.source_management import SourceManagementReconciliationPending

    gateway, client, store = stop_gateway()
    client.change_tp = True
    with pytest.raises(SourceManagementReconciliationPending):
        move_stop(gateway)
    if flat:
        client.position_rows = []
    with pytest.raises(SourceManagementReconciliationPending):
        gateway.reconcile_stop_loss("management-sl")
    assert store.get("root-entry").active_stop_loss_price is None
    assert sum(name == "modify_position_stop_loss" for name, _ in client.calls) == 1
    gateway.close()


def test_stop_modify_rejects_unowned_residual_without_post() -> None:
    gateway, client, store = stop_gateway()
    root = store.get("root-entry")
    root.filled_qty = Decimal("0.02")
    store.update(root)
    with pytest.raises(ValueError, match="ownership"):
        move_stop(gateway)
    assert not any(name == "modify_position_stop_loss" for name, _ in client.calls)
    gateway.close()


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [
        ("0.011", "0.005"),
        ("0.002", "0.001"),
        ("0.001", None),
        ("0.0025", None),
    ],
)
def test_tp1_half_policy_is_lot_safe_and_preserves_residual(quantity, expected) -> None:
    gateway, _, _ = stop_gateway()
    if expected is None:
        with pytest.raises(ValueError):
            gateway.normalize_tp1_close_quantity("BTCUSDT", Decimal(quantity))
    else:
        assert gateway.normalize_tp1_close_quantity("BTCUSDT", Decimal(quantity)) == Decimal(
            expected
        )
    gateway.close()
