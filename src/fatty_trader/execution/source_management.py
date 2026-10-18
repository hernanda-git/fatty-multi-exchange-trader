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
    attempts: int = 0

    @classmethod
    def new(cls, revision: str, symbol: str, action: ManagementAction) -> SourceManagementUpdate:
        return cls(uuid4(), uuid4(), revision, symbol, action)


class SourceManagementStore(Protocol):
    def claim(self, worker_id: str) -> SourceManagementUpdate | None: ...
    def get(self, update_id: UUID) -> SourceManagementUpdate: ...
    def update_state(self, update_id: UUID, state: str) -> None: ...
    def park(
        self, update_id: UUID, *, error: str, retry_seconds: int, max_attempts: int
    ) -> str: ...
    def persist_provider_intent(self, update_id: UUID, client_oid: str) -> bool: ...
    def has_provider_intent(self, update_id: UUID, client_oid: str) -> bool: ...
    def management_is_eligible(self, update_id: UUID) -> bool: ...
    def has_claimed_cancellation(self, update_id: UUID) -> bool: ...


class SourceManagementGateway(Protocol):
    def get_positions(self, symbol: str) -> list[dict[str, Any]]: ...
    def get_orders(self, symbol: str) -> list[dict[str, Any]]: ...
    def cancel_pending_entries(
        self, symbol: str, management_id: UUID, *, reconciliation_only: bool = False
    ) -> bool: ...
    def close_reduce_only(
        self, *, symbol: str, side: str, quantity: Decimal, client_oid: str
    ) -> None: ...
    def normalize_tp1_close_quantity(self, symbol: str, quantity: Decimal) -> Decimal: ...
    def reconcile_stop_loss(self, client_oid: str) -> None: ...
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
        self.eligible = True
        self.claimed_cancellations: set[UUID] = set()

    def claim(self, worker_id: str) -> SourceManagementUpdate | None:
        if not worker_id:
            raise ValueError("worker_id is required")
        for update in self._updates.values():
            if update.state in {"queued", "claimed", "reconciliation-pending"}:
                claimed = replace(update, state="claimed")
                self._updates[update.id] = claimed
                return claimed
        return None

    def get(self, update_id: UUID) -> SourceManagementUpdate:
        return self._updates[update_id]

    def update_state(self, update_id: UUID, state: str) -> None:
        update = self._updates.pop(update_id)
        self._updates[update_id] = replace(update, state=state)

    def park(self, update_id: UUID, *, error: str, retry_seconds: int, max_attempts: int) -> str:
        del error, retry_seconds
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        update = self._updates[update_id]
        attempts = update.attempts + 1
        state = "failed" if attempts >= max_attempts else "reconciliation-pending"
        self._updates[update_id] = replace(update, state=state, attempts=attempts)
        return state

    def persist_provider_intent(self, update_id: UUID, client_oid: str) -> bool:
        key = (update_id, client_oid)
        if key in self._provider_intents:
            return False
        self._provider_intents.add(key)
        return True

    def has_provider_intent(self, update_id: UUID, client_oid: str) -> bool:
        return (update_id, client_oid) in self._provider_intents

    def management_is_eligible(self, update_id: UUID) -> bool:
        return self.eligible

    def has_claimed_cancellation(self, update_id: UUID) -> bool:
        return update_id in self.claimed_cancellations


class SourceManagementExecutor:
    """Executes exactly one claimed update; provider POSTs are intent-first and never retried."""

    def __init__(
        self,
        store: SourceManagementStore,
        gateway: SourceManagementGateway,
        *,
        mutations_enabled: bool = False,
        max_attempts: int = 5,
        retry_seconds: int = 60,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._mutations_enabled = mutations_enabled
        self._max_attempts = max(1, max_attempts)
        self._retry_seconds = max(0, retry_seconds)

    def run_once(self, worker_id: str) -> str:
        update = self._store.claim(worker_id)
        if update is None:
            return "idle"
        try:
            if not self._mutations_enabled:
                self._store.update_state(update.id, "failed")
                return "mutations-disabled"
            sl_oid = f"source-management-{update.id.hex}-sl"
            if update.action in {
                ManagementAction.TP1_BOOKED,
                ManagementAction.SL_TO_ENTRY,
            } and self._store.has_provider_intent(update.id, sl_oid):
                # This is GET-only even when the position is now flat. Flat
                # cannot prove which protection plan a previous POST changed.
                self._gateway.reconcile_stop_loss(sl_oid)
                self._store.update_state(update.id, "reconciled")
                return "reconciled"
            if not self._store.management_is_eligible(update.id):
                close_oid = f"source-management-{update.id.hex}-close"
                if self._store.has_provider_intent(update.id, close_oid):
                    positions = self._gateway.get_positions(update.symbol)
                    if self._retire_unreconcilable_close_claim(update, positions):
                        return "reconciled"
                    raise SourceManagementReconciliationPending(
                        "stale close claim still requires authenticated execution reconciliation"
                    )
                if self._store.has_claimed_cancellation(
                    update.id
                ) and not self._gateway.cancel_pending_entries(
                    update.symbol, update.id, reconciliation_only=True
                ):
                    raise SourceManagementReconciliationPending(
                        "stale cancellation claim still requires terminal readback"
                    )
                raise ValueError("source management revision is no longer eligible")
            cancels_entries = update.action in {
                ManagementAction.CLOSE,
                ManagementAction.TP1_BOOKED,
                ManagementAction.TP_BOOKED,
            }
            if cancels_entries and not self._gateway.cancel_pending_entries(
                update.symbol, update.id
            ):
                # The entry lifecycle persists each exact owned cancellation claim
                # before POST. Repeated/ambiguous claims only perform readback.
                raise SourceManagementReconciliationPending(
                    "owned pending entries are not proved terminal"
                )
            # Cancellation can race a fill: never size management from an earlier
            # position snapshot, even when the account was flat before cancellation.
            positions = self._gateway.get_positions(update.symbol)
            if not positions:
                if cancels_entries:
                    close_oid = f"source-management-{update.id.hex}-close"
                    if self._store.has_provider_intent(update.id, close_oid):
                        if self._retire_unreconcilable_close_claim(update, positions):
                            return "reconciled"
                        raise SourceManagementReconciliationPending(
                            "existing close intent requires execution reconciliation"
                        )
                    self._store.update_state(update.id, "cancelled-flat")
                    return "cancelled-flat"
                self._store.update_state(update.id, "failed")
                return "failed"
            if update.action is ManagementAction.TP_BOOKED:
                # A generic/later booking stops re-entry; it is not authority to
                # invent an additional position allocation or claim a TP fill.
                self._store.update_state(update.id, "entries-cancelled")
                return "entries-cancelled"
            position = self._one_position(update.symbol, positions)
            if update.action is ManagementAction.CLOSE:
                self._require_eligible(update)
                close_oid = f"source-management-{update.id.hex}-close"
                if not self._store.persist_provider_intent(update.id, close_oid):
                    self._store.update_state(update.id, "reconciliation-pending")
                    return "reconciliation-pending"
                self._close(update, position, position["size"], close_oid)
                self._require_flat(update.symbol)
            elif update.action is ManagementAction.TP1_BOOKED:
                close_oid = f"source-management-{update.id.hex}-close"
                if self._store.has_provider_intent(update.id, close_oid):
                    raise SourceManagementReconciliationPending(
                        "existing TP1 close intent requires execution reconciliation"
                    )
                normalizer = getattr(self._gateway, "normalize_tp1_close_quantity", None)
                quantity = (
                    normalizer(update.symbol, position["size"])
                    if callable(normalizer)
                    else position["size"] / Decimal("2")
                )
                self._require_eligible(update)
                if not self._store.persist_provider_intent(update.id, close_oid):
                    raise SourceManagementReconciliationPending("TP1 close claim was lost")
                self._close(update, position, quantity, close_oid)
                remaining = self._one_position(update.symbol)
                if remaining["size"] != position["size"] - quantity:
                    raise SourceManagementReconciliationPending(
                        "TP1 reduction differs from the claimed half close"
                    )
                self._require_eligible(update)
                if not self._store.persist_provider_intent(update.id, sl_oid):
                    self._gateway.reconcile_stop_loss(sl_oid)
                    self._store.update_state(update.id, "reconciled")
                    return "reconciled"
                self._gateway.replace_stop_loss(
                    symbol=update.symbol,
                    side=remaining["side"],
                    quantity=remaining["size"],
                    stop_loss=remaining["entry"],
                    client_oid=sl_oid,
                )
            elif update.action is ManagementAction.SL_TO_ENTRY:
                sl_oid = f"source-management-{update.id.hex}-sl"
                self._require_eligible(update)
                if not self._store.persist_provider_intent(update.id, sl_oid):
                    self._gateway.reconcile_stop_loss(sl_oid)
                    self._store.update_state(update.id, "reconciled")
                    return "reconciled"
                self._gateway.replace_stop_loss(
                    symbol=update.symbol,
                    side=position["side"],
                    quantity=position["size"],
                    stop_loss=position["entry"],
                    client_oid=sl_oid,
                )
        except SourceManagementReconciliationPending as exc:
            # Count the attempt: the row keeps a deadline while the budget lasts and
            # dead-letters with one alert once it is spent, instead of retrying forever.
            return self._store.park(
                update.id,
                error=str(exc) or type(exc).__name__,
                retry_seconds=self._retry_seconds,
                max_attempts=self._max_attempts,
            )
        except (ValueError, KeyError, TypeError):
            self._store.update_state(update.id, "failed")
            return "failed"
        self._store.update_state(update.id, "reconciled")
        return "reconciled"

    def _retire_unreconcilable_close_claim(
        self, update: SourceManagementUpdate, positions: list[dict[str, Any]]
    ) -> bool:
        """Retire a close claim only on GET-only proof the venue holds no work.

        A claim that can never be posted again (closed source window, or a flat
        position) must not pin the queue forever: when the symbol shows no
        position, no pending provider order and no non-terminal owned entry
        claim, nothing is left to reconcile and the update is retired as
        reconciled. Any unproven or still-live readback keeps it pending.
        """
        if positions:
            return False
        try:
            orders = self._gateway.get_orders(update.symbol)
            owned_entries_terminal = self._gateway.cancel_pending_entries(
                update.symbol, update.id, reconciliation_only=True
            )
        except Exception:  # noqa: BLE001 - an unproven readback never retires a claim
            return False
        if orders or not owned_entries_terminal:
            return False
        self._store.update_state(update.id, "reconciled")
        return True

    def _require_eligible(self, update: SourceManagementUpdate) -> None:
        if not self._store.management_is_eligible(update.id):
            raise ValueError("source management revision is no longer eligible")

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
