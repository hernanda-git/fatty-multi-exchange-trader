from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import UUID

import pytest

from fatty_trader.analyzer.trade_management import ManagementAction
from fatty_trader.execution.source_management import (
    InMemorySourceManagementStore,
    SourceManagementExecutor,
    SourceManagementUpdate,
)


def test_tp1_update_is_claimed_once_closes_half_then_moves_sl_after_readback() -> None:
    from fatty_trader.execution.source_management import (
        InMemorySourceManagementStore,
        SourceManagementExecutor,
        SourceManagementUpdate,
    )

    class Gateway:
        def __init__(self) -> None:
            self.positions = [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "size": Decimal("10"),
                    "entry": Decimal("60000"),
                }
            ]
            self.calls: list[tuple[str, object]] = []

        def cancel_pending_entries(self, symbol: str, management_id: UUID) -> bool:
            self.calls.append(("cancel_pending_entries", (symbol, management_id)))
            return True

        def get_positions(self, symbol: str) -> list[dict[str, object]]:
            self.calls.append(("get_positions", symbol))
            return [dict(position) for position in self.positions if position["symbol"] == symbol]

        def close_reduce_only(
            self, *, symbol: str, side: str, quantity: Decimal, client_oid: str
        ) -> None:
            self.calls.append(("close_reduce_only", (symbol, side, quantity, client_oid)))
            self.positions[0]["size"] = Decimal("5")

        def replace_stop_loss(
            self, *, symbol: str, side: str, quantity: Decimal, stop_loss: Decimal, client_oid: str
        ) -> None:
            self.calls.append(
                ("replace_stop_loss", (symbol, side, quantity, stop_loss, client_oid))
            )

    update = SourceManagementUpdate(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        source_message_id=UUID("00000000-0000-0000-0000-000000000002"),
        revision="a" * 64,
        symbol="BTCUSDT",
        action=ManagementAction.TP1_BOOKED,
    )
    store = InMemorySourceManagementStore([update])
    gateway = Gateway()

    assert (
        SourceManagementExecutor(store, gateway, mutations_enabled=True).run_once("worker-a")
        == "reconciled"
    )
    assert gateway.calls == [
        ("cancel_pending_entries", ("BTCUSDT", update.id)),
        ("get_positions", "BTCUSDT"),
        (
            "close_reduce_only",
            (
                "BTCUSDT",
                "SELL",
                Decimal("5"),
                "source-management-00000000000000000000000000000001-close",
            ),
        ),
        ("get_positions", "BTCUSDT"),
        (
            "replace_stop_loss",
            (
                "BTCUSDT",
                "LONG",
                Decimal("5"),
                Decimal("60000"),
                "source-management-00000000000000000000000000000001-sl",
            ),
        ),
    ]
    assert store.get(update.id).state == "reconciled"


def test_ambiguous_open_position_fails_closed_after_entry_cancellation() -> None:
    from fatty_trader.execution.source_management import (
        InMemorySourceManagementStore,
        SourceManagementExecutor,
        SourceManagementUpdate,
    )

    class Gateway:
        def __init__(self, positions: list[dict[str, object]]) -> None:
            self.positions = positions
            self.posts = 0

        def cancel_pending_entries(self, symbol: str, management_id: UUID) -> bool:
            return True

        def get_positions(self, symbol: str) -> list[dict[str, object]]:
            return self.positions

        def close_reduce_only(self, **_: object) -> None:
            self.posts += 1

        def replace_stop_loss(self, **_: object) -> None:
            self.posts += 1

    for positions in ([{"symbol": "BTCUSDT"}, {"symbol": "BTCUSDT"}],):
        update = SourceManagementUpdate.new("a" * 64, "BTCUSDT", ManagementAction.CLOSE)
        store = InMemorySourceManagementStore([update])
        gateway = Gateway(positions)
        assert (
            SourceManagementExecutor(store, gateway, mutations_enabled=True).run_once("worker-a")
            == "failed"
        )
        assert gateway.posts == 0
        assert store.get(update.id).state == "failed"


def test_restart_with_existing_close_intent_never_reposts_provider_close() -> None:
    from fatty_trader.execution.source_management import (
        InMemorySourceManagementStore,
        SourceManagementExecutor,
        SourceManagementUpdate,
    )

    class Gateway:
        def __init__(self) -> None:
            self.posts = 0

        def cancel_pending_entries(self, symbol: str, management_id: UUID) -> bool:
            return True

        def get_positions(self, symbol: str) -> list[dict[str, object]]:
            return [
                {"symbol": symbol, "side": "LONG", "size": Decimal("10"), "entry": Decimal("60000")}
            ]

        def close_reduce_only(self, **_: object) -> None:
            self.posts += 1

        def replace_stop_loss(self, **_: object) -> None:
            self.posts += 1

    update = SourceManagementUpdate.new("a" * 64, "BTCUSDT", ManagementAction.TP1_BOOKED)
    store = InMemorySourceManagementStore([replace(update, state="claimed")])
    store.persist_provider_intent(update.id, "source-management-" + update.id.hex + "-close")
    gateway = Gateway()

    assert (
        SourceManagementExecutor(store, gateway, mutations_enabled=True).run_once("worker-b")
        == "reconciliation-pending"
    )
    assert gateway.posts == 0
    assert store.get(update.id).state == "reconciliation-pending"


def test_source_management_gate_blocks_live_position_mutations_by_default() -> None:
    from fatty_trader.execution.source_management import (
        InMemorySourceManagementStore,
        SourceManagementExecutor,
        SourceManagementUpdate,
    )

    class Gateway:
        def __init__(self) -> None:
            self.posts = 0

        def get_positions(self, symbol: str) -> list[dict[str, object]]:
            return [
                {"symbol": symbol, "side": "LONG", "size": Decimal("1"), "entry": Decimal("100")}
            ]

        def close_reduce_only(self, **_: object) -> None:
            self.posts += 1

        def replace_stop_loss(self, **_: object) -> None:
            self.posts += 1

    update = SourceManagementUpdate.new("a" * 64, "BTCUSDT", ManagementAction.CLOSE)
    store = InMemorySourceManagementStore([update])
    gateway = Gateway()

    assert SourceManagementExecutor(store, gateway).run_once("worker") == "mutations-disabled"
    assert gateway.posts == 0
    assert store.get(update.id).state == "failed"


def test_analyzer_persists_management_update_idempotently_before_marking_analyzed() -> None:
    from fatty_trader.analyzer.postgres_worker import _MANAGEMENT_INSERT

    assert "INSERT INTO source_management_updates" in _MANAGEMENT_INSERT
    assert (
        "ON CONFLICT (source_message_id, revision, symbol, action) DO NOTHING" in _MANAGEMENT_INSERT
    )


class RemainderGateway:
    """Source-executor fake; provider cancellation fencing is tested by the adapter."""

    def __init__(self) -> None:
        self.positions: list[dict[str, object]] = []
        self.calls: list[tuple[str, object]] = []
        self.cancel_result = True
        self.racing_fill = Decimal("0")
        self.remaining_entry = True

    def cancel_pending_entries(self, symbol: str, management_id: UUID) -> bool:
        self.calls.append(("cancel_pending_entries", (symbol, management_id)))
        if not self.cancel_result:
            return False
        self.remaining_entry = False
        if self.racing_fill:
            self.positions = [
                {
                    "symbol": symbol,
                    "side": "LONG",
                    "size": self.racing_fill,
                    "entry": Decimal("100"),
                }
            ]
            self.racing_fill = Decimal("0")
        return True

    def get_positions(self, symbol: str) -> list[dict[str, object]]:
        self.calls.append(("get_positions", symbol))
        return [dict(position) for position in self.positions]

    def close_reduce_only(
        self, *, symbol: str, side: str, quantity: Decimal, client_oid: str
    ) -> None:
        self.calls.append(("close_reduce_only", quantity))
        size = self.positions[0]["size"]
        assert isinstance(size, Decimal)
        if size == quantity:
            self.positions = []
        else:
            self.positions[0]["size"] = size - quantity

    def replace_stop_loss(self, **kwargs: object) -> None:
        self.calls.append(("replace_stop_loss", kwargs))


@pytest.mark.parametrize(
    "action",
    [ManagementAction.CLOSE, ManagementAction.TP1_BOOKED, ManagementAction.TP_BOOKED],
)
def test_flat_source_management_cancels_waiting_entry_without_fabricating_close(
    action: ManagementAction,
) -> None:
    update = SourceManagementUpdate.new("a" * 64, "BTCUSDT", action)
    store = InMemorySourceManagementStore([update])
    gateway = RemainderGateway()
    executor = SourceManagementExecutor(store, gateway, mutations_enabled=True)

    assert executor.run_once("worker") == "cancelled-flat"
    assert store.get(update.id).state == "cancelled-flat"
    assert not gateway.remaining_entry
    assert gateway.calls == [
        ("cancel_pending_entries", ("BTCUSDT", update.id)),
        ("get_positions", "BTCUSDT"),
    ]
    assert executor.run_once("duplicate-worker") == "idle"
    assert not store.has_provider_intent(update.id, f"source-management-{update.id.hex}-close")


@pytest.mark.parametrize(
    ("action", "expected_close"),
    [(ManagementAction.CLOSE, Decimal("8")), (ManagementAction.TP1_BOOKED, Decimal("4"))],
)
def test_management_sizes_from_fill_racing_remainder_cancel(
    action: ManagementAction, expected_close: Decimal
) -> None:
    update = SourceManagementUpdate.new("a" * 64, "BTCUSDT", action)
    store = InMemorySourceManagementStore([update])
    gateway = RemainderGateway()
    gateway.racing_fill = Decimal("8")
    executor = SourceManagementExecutor(store, gateway, mutations_enabled=True)

    assert executor.run_once("worker") == "reconciled"
    assert gateway.calls[:2] == [
        ("cancel_pending_entries", ("BTCUSDT", update.id)),
        ("get_positions", "BTCUSDT"),
    ]
    assert ("close_reduce_only", expected_close) in gateway.calls
    assert executor.run_once("duplicate-worker") == "idle"


def test_unknown_cancel_blocks_management_and_restart_reconciles_before_position_read() -> None:
    update = SourceManagementUpdate.new("a" * 64, "BTCUSDT", ManagementAction.CLOSE)
    store = InMemorySourceManagementStore([update])
    gateway = RemainderGateway()
    gateway.cancel_result = False

    assert (
        SourceManagementExecutor(store, gateway, mutations_enabled=True).run_once("worker")
        == "reconciliation-pending"
    )
    assert gateway.calls == [("cancel_pending_entries", ("BTCUSDT", update.id))]
    assert store.get(update.id).state == "reconciliation-pending"
    assert gateway.remaining_entry
    gateway.cancel_result = True
    gateway.racing_fill = Decimal("8")

    assert (
        SourceManagementExecutor(store, gateway, mutations_enabled=True).run_once("restart-worker")
        == "reconciled"
    )
    assert gateway.calls[-3:] == [
        ("get_positions", "BTCUSDT"),
        ("close_reduce_only", Decimal("8")),
        ("get_positions", "BTCUSDT"),
    ]


def test_flat_restart_with_close_intent_does_not_claim_cancellation_only_outcome() -> None:
    update = SourceManagementUpdate.new("a" * 64, "BTCUSDT", ManagementAction.CLOSE)
    store = InMemorySourceManagementStore([replace(update, state="reconciliation-pending")])
    store.persist_provider_intent(update.id, f"source-management-{update.id.hex}-close")
    gateway = RemainderGateway()

    for worker in ("restart-a", "restart-b"):
        assert (
            SourceManagementExecutor(store, gateway, mutations_enabled=True).run_once(worker)
            == "reconciliation-pending"
        )
    assert not any(name == "close_reduce_only" for name, _ in gateway.calls)


def test_source_gate_prevents_cancellation_even_when_only_waiting_entry_exists() -> None:
    update = SourceManagementUpdate.new("a" * 64, "BTCUSDT", ManagementAction.CLOSE)
    store = InMemorySourceManagementStore([update])
    gateway = RemainderGateway()

    assert SourceManagementExecutor(store, gateway).run_once("worker") == "mutations-disabled"
    assert gateway.remaining_entry
    assert gateway.calls == []


def test_stop_move_does_not_cancel_waiting_entry() -> None:
    update = SourceManagementUpdate.new("a" * 64, "BTCUSDT", ManagementAction.SL_TO_ENTRY)
    store = InMemorySourceManagementStore([update])
    gateway = RemainderGateway()
    gateway.positions = [
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "size": Decimal("2"),
            "entry": Decimal("100"),
        }
    ]

    assert (
        SourceManagementExecutor(store, gateway, mutations_enabled=True).run_once("worker")
        == "reconciled"
    )
    assert gateway.remaining_entry
    assert not any(name == "cancel_pending_entries" for name, _ in gateway.calls)


def test_generic_booking_cancels_remainder_without_inventing_quantity_allocation() -> None:
    update = SourceManagementUpdate.new("a" * 64, "BTCUSDT", ManagementAction.TP_BOOKED)
    store = InMemorySourceManagementStore([update])
    gateway = RemainderGateway()
    gateway.racing_fill = Decimal("8")
    executor = SourceManagementExecutor(store, gateway, mutations_enabled=True)

    assert executor.run_once("worker") == "entries-cancelled"
    assert store.get(update.id).state == "entries-cancelled"
    assert gateway.calls == [
        ("cancel_pending_entries", ("BTCUSDT", update.id)),
        ("get_positions", "BTCUSDT"),
    ]
    assert gateway.positions[0]["size"] == Decimal("8")
    assert not gateway.remaining_entry
    assert executor.run_once("duplicate-worker") == "idle"


def test_unknown_generic_booking_cancellation_does_not_claim_terminal_outcome() -> None:
    update = SourceManagementUpdate.new("a" * 64, "BTCUSDT", ManagementAction.TP_BOOKED)
    store = InMemorySourceManagementStore([update])
    gateway = RemainderGateway()
    gateway.cancel_result = False

    assert (
        SourceManagementExecutor(store, gateway, mutations_enabled=True).run_once("worker")
        == "reconciliation-pending"
    )
    assert gateway.calls == [("cancel_pending_entries", ("BTCUSDT", update.id))]
    assert gateway.remaining_entry


@pytest.mark.asyncio
async def test_source_service_uses_runtime_owned_cancellation_before_real_gateway_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from types import SimpleNamespace

    from fatty_trader import service
    from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore

    update = SourceManagementUpdate.new("a" * 64, "BTCUSDT", ManagementAction.CLOSE)
    store = InMemorySourceManagementStore([update])
    calls: list[tuple[str, object]] = []

    class Client:
        def __init__(self) -> None:
            self.positions: list[dict[str, str]] = []

        async def get_all_positions(self) -> list[dict[str, str]]:
            calls.append(("get_all_positions", None))
            return self.positions

        async def place_market_close(self, **kwargs: object) -> dict[str, str]:
            calls.append(("place_market_close", kwargs))
            self.positions = []
            return {"orderId": "source-close-1"}

        async def aclose(self) -> None:
            calls.append(("aclose", None))

    client = Client()

    class EntryExecution:
        async def cancel_pending_entries(self, symbol: str, management_id: UUID) -> bool:
            calls.append(("cancel_pending_entries", (symbol, management_id)))
            client.positions = [
                {
                    "symbol": symbol,
                    "holdSide": "long",
                    "total": "2",
                    "openPriceAvg": "100",
                }
            ]
            return True

    runtime = SimpleNamespace(client=client, execution=EntryExecution())
    monkeypatch.setattr(service, "build_bitget_execution_runtime", lambda _env: runtime)
    monkeypatch.setattr(service, "worker_progress", lambda: None)
    monkeypatch.setattr(
        "fatty_trader.storage.source_management.PostgresSourceManagementStore", lambda _: store
    )
    monkeypatch.setattr(
        "fatty_trader.storage.live_intents.PostgresLiveIntentStore",
        lambda _: InMemoryLiveIntentStore(),
    )

    async def stop_after_cycle(_interval: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(service.asyncio, "sleep", stop_after_cycle)
    with pytest.raises(asyncio.CancelledError):
        await service.run_source_management.__wrapped__(
            {
                "BITGET_MODE": "LIVE",
                "BITGET_OPERATOR_MUTATIONS_ENABLED": "1",
            }
        )

    assert calls[0] == ("cancel_pending_entries", ("BTCUSDT", update.id))
    assert calls[1] == ("get_all_positions", None)
    assert calls[2] == (
        "place_market_close",
        {
            "symbol": "BTCUSDT",
            "side": "SELL",
            "quantity": "2",
            "client_oid": f"source-management-{update.id.hex}-close",
        },
    )
    assert calls[3:] == [("get_all_positions", None), ("aclose", None)]
    assert store.get(update.id).state == "reconciled"
