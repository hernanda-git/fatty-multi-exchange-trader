from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import UUID

from fatty_trader.analyzer.trade_management import ManagementAction


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

    assert SourceManagementExecutor(store, gateway).run_once("worker-a") == "reconciled"
    assert gateway.calls == [
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


def test_no_or_ambiguous_open_position_fails_closed_without_provider_post() -> None:
    from fatty_trader.execution.source_management import (
        InMemorySourceManagementStore,
        SourceManagementExecutor,
        SourceManagementUpdate,
    )

    class Gateway:
        def __init__(self, positions: list[dict[str, object]]) -> None:
            self.positions = positions
            self.posts = 0

        def get_positions(self, symbol: str) -> list[dict[str, object]]:
            return self.positions

        def close_reduce_only(self, **_: object) -> None:
            self.posts += 1

        def replace_stop_loss(self, **_: object) -> None:
            self.posts += 1

    for positions in ([], [{"symbol": "BTCUSDT"}, {"symbol": "BTCUSDT"}]):
        update = SourceManagementUpdate.new("a" * 64, "BTCUSDT", ManagementAction.CLOSE)
        store = InMemorySourceManagementStore([update])
        gateway = Gateway(positions)
        assert SourceManagementExecutor(store, gateway).run_once("worker-a") == "failed"
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

    assert SourceManagementExecutor(store, gateway).run_once("worker-b") == "reconciliation-pending"
    assert gateway.posts == 0
    assert store.get(update.id).state == "reconciliation-pending"


def test_analyzer_persists_management_update_idempotently_before_marking_analyzed() -> None:
    from fatty_trader.analyzer.postgres_worker import _MANAGEMENT_INSERT

    assert "INSERT INTO source_management_updates" in _MANAGEMENT_INSERT
    assert (
        "ON CONFLICT (source_message_id, revision, symbol, action) DO NOTHING" in _MANAGEMENT_INSERT
    )
