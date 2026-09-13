"""Durable, fail-closed execution of parsed source trade-management updates."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from fatty_trader.analyzer.trade_management import ManagementAction


@dataclass(frozen=True)
class SourceManagementUpdate:
    id: UUID
    source_message_id: UUID
    revision: str
    symbol: str
    action: ManagementAction
    state: str = "queued"

    @classmethod
    def new(cls, revision: str, symbol: str, action: ManagementAction) -> SourceManagementUpdate:
        return cls(uuid4(), uuid4(), revision, symbol, action)


class SourceManagementStore(Protocol):
    def claim(self, worker_id: str) -> SourceManagementUpdate | None: ...
    def get(self, update_id: UUID) -> SourceManagementUpdate: ...
    def update_state(self, update_id: UUID, state: str) -> None: ...
    def persist_provider_intent(self, update_id: UUID, client_oid: str) -> bool: ...


class SourceManagementGateway(Protocol):
    def get_positions(self, symbol: str) -> list[dict[str, Any]]: ...
    def close_reduce_only(
        self, *, symbol: str, side: str, quantity: Decimal, client_oid: str
    ) -> None: ...
    def replace_stop_loss(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        stop_loss: Decimal,
        client_oid: str,
    ) -> None: ...


class SourceManagementReconciliationPending(RuntimeError):
    """A durable provider intent exists, so a POST retry is forbidden."""


class InMemorySourceManagementStore:
    """Deterministic test store mirroring durable claim and intent semantics."""

    def __init__(self, updates: list[SourceManagementUpdate] | None = None) -> None:
        self._updates = {update.id: update for update in updates or []}
        self._provider_intents: set[tuple[UUID, str]] = set()

    def claim(self, worker_id: str) -> SourceManagementUpdate | None:
        if not worker_id:
            raise ValueError("worker_id is required")
        for update in self._updates.values():
            if update.state in {"queued", "claimed"}:
                claimed = replace(update, state="claimed")
                self._updates[update.id] = claimed
                return claimed
        return None

    def get(self, update_id: UUID) -> SourceManagementUpdate:
        return self._updates[update_id]

    def update_state(self, update_id: UUID, state: str) -> None:
        self._updates[update_id] = replace(self._updates[update_id], state=state)

    def persist_provider_intent(self, update_id: UUID, client_oid: str) -> bool:
        key = (update_id, client_oid)
        if key in self._provider_intents:
            return False
        self._provider_intents.add(key)
        return True


class SourceManagementExecutor:
    """Executes exactly one claimed update; provider POSTs are intent-first and never retried."""

    def __init__(
        self,
        store: SourceManagementStore,
        gateway: SourceManagementGateway,
        *,
        mutations_enabled: bool = False,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._mutations_enabled = mutations_enabled

    def run_once(self, worker_id: str) -> str:
        update = self._store.claim(worker_id)
        if update is None:
            return "idle"
        try:
            positions = self._gateway.get_positions(update.symbol)
            if not positions:
                # A management instruction without a live provider position was
                # not executed. Keep it auditable as failed rather than claiming
                # reconciliation and losing the operator's intent.
                self._store.update_state(update.id, "failed")
                return "failed"
            position = self._one_position(update.symbol, positions)
            if not self._mutations_enabled:
                self._store.update_state(update.id, "failed")
                return "mutations-disabled"
            if update.action is ManagementAction.CLOSE:
                close_oid = f"source-management-{update.id.hex}-close"
                if not self._store.persist_provider_intent(update.id, close_oid):
                    self._store.update_state(update.id, "reconciliation-pending")
                    return "reconciliation-pending"
                self._close(update, position, position["size"], close_oid)
                self._require_flat(update.symbol)
            elif update.action is ManagementAction.TP1_BOOKED:
                close_oid = f"source-management-{update.id.hex}-close"
                if not self._store.persist_provider_intent(update.id, close_oid):
                    self._store.update_state(update.id, "reconciliation-pending")
                    return "reconciliation-pending"
                quantity = position["size"] / Decimal("2")
                if quantity <= 0:
                    raise ValueError("TP1 close quantity is not positive")
                self._close(update, position, quantity, close_oid)
                remaining = self._one_position(update.symbol)
                if remaining["size"] >= position["size"]:
                    raise ValueError("TP1 close was not confirmed by provider position readback")
                sl_oid = f"source-management-{update.id.hex}-sl"
                if not self._store.persist_provider_intent(update.id, sl_oid):
                    self._store.update_state(update.id, "reconciliation-pending")
                    return "reconciliation-pending"
                self._gateway.replace_stop_loss(
                    symbol=update.symbol,
                    side=remaining["side"],
                    quantity=remaining["size"],
                    stop_loss=remaining["entry"],
                    client_oid=sl_oid,
                )
            elif update.action is ManagementAction.SL_TO_ENTRY:
                sl_oid = f"source-management-{update.id.hex}-sl"
                if not self._store.persist_provider_intent(update.id, sl_oid):
                    self._store.update_state(update.id, "reconciliation-pending")
                    return "reconciliation-pending"
                self._gateway.replace_stop_loss(
                    symbol=update.symbol,
                    side=position["side"],
                    quantity=position["size"],
                    stop_loss=position["entry"],
                    client_oid=sl_oid,
                )
        except SourceManagementReconciliationPending:
            self._store.update_state(update.id, "reconciliation-pending")
            return "reconciliation-pending"
        except (ValueError, KeyError, TypeError):
            self._store.update_state(update.id, "failed")
            return "failed"
        self._store.update_state(update.id, "reconciled")
        return "reconciled"

    def _one_position(
        self, symbol: str, positions: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        positions = self._gateway.get_positions(symbol) if positions is None else positions
        if len(positions) != 1:
            raise ValueError("management target must resolve to exactly one open Bitget position")
        position = positions[0]
        if not isinstance(position.get("size"), Decimal) or position["size"] <= 0:
            raise ValueError("management position size is invalid")
        if position.get("side") not in {"LONG", "SHORT"}:
            raise ValueError("management position side is invalid")
        if not isinstance(position.get("entry"), Decimal) or position["entry"] <= 0:
            raise ValueError("management position entry is invalid")
        return position

    def _close(
        self,
        update: SourceManagementUpdate,
        position: dict[str, Any],
        quantity: Decimal,
        client_oid: str,
    ) -> None:
        self._gateway.close_reduce_only(
            symbol=update.symbol,
            side="SELL" if position["side"] == "LONG" else "BUY",
            quantity=quantity,
            client_oid=client_oid,
        )

    def _require_flat(self, symbol: str) -> None:
        if self._gateway.get_positions(symbol):
            raise ValueError("close was not confirmed by provider position readback")
