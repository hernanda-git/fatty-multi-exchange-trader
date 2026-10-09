"""Durable dispatcher adapter for the async Bitget entry/protection workflow."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from decimal import Decimal
from typing import Any, Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from fatty_trader.domain.enums import Direction, Exchange
from fatty_trader.exchanges.bitget.async_execution import (
    AsyncExecutionResult,
    AsyncProtectionResult,
    BitgetEntryVeto,
)
from fatty_trader.exchanges.bitget.live import (
    LiveIntentRecord,
    LiveIntentStoreProtocol,
    LiveOrderStatus,
)
from fatty_trader.execution.bitget_admission import BitgetEntrySubmission
from fatty_trader.execution.bitget_dispatch_repository import BitgetDispatch
from fatty_trader.execution.protection import ProtectionPlan, ProtectionState


class AsyncDispatchExecution(Protocol):
    """The narrow async execution seam used by the durable dispatcher."""

    async def submit_entry(self, intent: LiveIntentRecord) -> AsyncExecutionResult: ...

    async def reconcile_intent(self, intent: LiveIntentRecord) -> AsyncExecutionResult: ...

    async def protect_filled_position(
        self,
        intent: LiveIntentRecord,
        plan: ProtectionPlan,
        store: LiveIntentStoreProtocol,
    ) -> AsyncProtectionResult: ...


class BitgetDispatchExecution:
    """Persist an intent, submit exactly once, read it back, then prove protection.

    A durable intent keyed by the dispatch UUID makes a restart GET-only.  The
    underlying execution client never retries an ambiguous POST. A filled or
    partial entry retains its provider fill truth independently of protection.
    Missing protection/failed containment returns an ``*_UNPROTECTED`` outcome;
    the dispatcher persists a valid fill state with a durable escalation reason.
    ``UNKNOWN`` is reserved for ambiguous entry evidence, never protection alone.
    """

    def __init__(
        self,
        execution: AsyncDispatchExecution,
        store: LiveIntentStoreProtocol,
        *,
        reservation_repository: object | None = None,
        dispatch_repository: Any = None,
        kill_switch: Any = None,
        recovery_protection: Callable[
            [LiveIntentRecord, ProtectionPlan], Awaitable[AsyncProtectionResult]
        ]
        | None = None,
    ) -> None:
        self._execution = execution
        self._store = store
        self._reservations = reservation_repository
        self._dispatch_repository = dispatch_repository
        self._kill_switch = kill_switch
        self._recovery_protection = recovery_protection
        self.recovery_ready = False
        self.recovery_issues: tuple[str, ...] = ()

    async def recover_entry_lifecycles(self) -> int:
        """GET-only recovery, including consumed reservations and terminal fills.

        Readiness means each candidate has an audited provider/protection verdict,
        NOT permission to admit entries. Missing protection is explicitly escalated
        in dispatch terminal_reason and the transactional outbox. The injected
        reader must verify ownership and exact quantity; it must never POST.
        Without a reader we escalate, never infer protection or repeat placement.
        Persistence/reconciliation failures propagate and leave readiness false.
        """
        self.recovery_ready = False
        repository = self._dispatch_repository
        if repository is None:
            raise RuntimeError("entry lifecycle recovery requires dispatch repository")
        count = 0
        issues: list[str] = []
        reader = self._recovery_protection
        binding = getattr(reader, "verify_account_binding", None)
        if callable(binding):
            await binding()
        begin = getattr(reader, "begin", None)
        if callable(begin):
            begin()
        for dispatch, client_oid in repository.recovery_candidates():
            from dataclasses import replace

            from fatty_trader.execution.bitget_dispatcher import _bitget_symbol

            dispatch = replace(dispatch, pair_token=_bitget_symbol(dispatch.pair_token))
            intent = self._store.get(client_oid)
            if intent is None:
                raise RuntimeError("recovery candidate lacks durable entry intent")
            if (
                intent.exchange != "bitget"
                or intent.role != "ENTRY"
                or intent.client_oid != self.client_oid(dispatch)
                or intent.symbol != dispatch.pair_token
                or intent.side != ("BUY" if dispatch.direction == "LONG" else "SELL")
            ):
                raise ValueError("recovery ownership mismatch")
            limit = self._store.get(intent.client_oid + "-limit")
            if intent.entry_leg == "market":
                if limit is None:
                    raise ValueError("split root lacks durable limit child")
                await self._reconcile_waiting_leg(intent, limit)
            result = await self._execution.reconcile_intent(intent)
            self._persist_readback(intent, result)
            await self._reconcile_post_fill(intent, result)
            if limit is not None:
                intent = self._aggregate_split(intent, limit)
                result = self._aggregate_result(intent)
                await self._reconcile_post_fill(intent, result)
            status = _dispatcher_status(result.status)
            reason = "recovery-entry-" + status.lower()
            if result.filled_qty > 0:
                protection = await self._read_recovery_protection(intent, dispatch)
                reason = (
                    "recovery-protection-verified"
                    if protection.state is ProtectionState.VENUE_PROTECTED
                    and protection.observed_quantity == result.filled_qty
                    else "recovery-missing-protection:" + (protection.reason or "unconfirmed")
                )
            if (
                reason != "recovery-protection-verified"
                and status not in {"REJECTED"}
                and not (limit is not None and result.filled_qty == 0 and status == "ACKNOWLEDGED")
            ):
                issues.append(reason)
            target = {
                "FILLED": "FILLED",
                "PARTIAL": "PARTIALLY_FILLED",
                "ACKNOWLEDGED": "ACKNOWLEDGED",
                "REJECTED": "REJECTED",
            }.get(status, "UNKNOWN")
            repository.transition(
                dispatch.id, expected_state=dispatch.state, target_state=target, reason=reason
            )
            self._resolve_reservation(intent, status)
            count += 1
        inventory = getattr(reader, "inventory", None)
        if callable(inventory):
            issues.extend(await inventory())
        else:
            durable_inventory = getattr(repository, "inventory_issues", None)
            if callable(durable_inventory):
                # Without a reader no venue/environment can be safely adopted.
                issues.extend(durable_inventory(None))
        unresolved = getattr(repository, "unresolved_protection_issues", None)
        if callable(unresolved):
            issues.extend(unresolved())
        self.recovery_issues = tuple(sorted(set(issues)))
        self.recovery_ready = not self.recovery_issues
        return count

    async def _read_recovery_protection(
        self, intent: LiveIntentRecord, dispatch: BitgetDispatch
    ) -> AsyncProtectionResult:
        if intent.planned_stop_loss is None or intent.planned_take_profits is None:
            return AsyncProtectionResult(
                ProtectionState.DEGRADED, intent.filled_qty, "recovery-validated-plan-missing"
            )
        plan = self._protection_plan(dispatch, intent, intent.filled_qty)
        if self._recovery_protection is None:
            return AsyncProtectionResult(
                ProtectionState.DEGRADED, intent.filled_qty, "recovery-reader-unwired"
            )
        try:
            return await self._recovery_protection(intent, plan)
        except Exception as exc:
            return AsyncProtectionResult(
                ProtectionState.DEGRADED,
                intent.filled_qty,
                f"recovery-read-error:{type(exc).__name__}",
            )

    async def submit_entry(
        self, dispatch: BitgetDispatch, submission: BitgetEntrySubmission
    ) -> str:
        existing = self._store.get(self.client_oid(dispatch))
        if existing is not None and existing.entry_leg == "market":
            from fatty_trader.execution.entry_routing import EntryMode, EntryRoute

            child = self._store.get(existing.client_oid + "-limit")
            if child is None:
                raise ValueError("split replay lacks durable limit child")
            submission = replace(
                submission,
                quantity=existing.requested_qty + child.requested_qty,
                entry_route=EntryRoute(
                    EntryMode.SPLIT_MARKET_LIMIT,
                    existing.requested_qty,
                    child.requested_qty,
                    child.limit_price,
                    "durable-split-replay",
                ),
            )
        if submission.entry_route is not None and submission.entry_route.limit_quantity > 0:
            return await self._submit_split(dispatch, submission)
        intent = self._intent(dispatch, submission)
        if self._store.get(intent.client_oid) is None:
            unresolved = getattr(self._dispatch_repository, "unresolved_protection_issues", None)
            if callable(unresolved) and unresolved():
                return "REJECTED"
            if (
                callable(getattr(self._recovery_protection, "inventory", None))
                and not self.recovery_ready
            ):
                return "REJECTED"
        if (
            self._store.get(intent.client_oid) is None
            and self._dispatch_repository is not None
            and not self._dispatch_repository.entry_source_eligible(dispatch.id)
        ):
            return "EXPIRED"
        claim = getattr(self._store, "claim", None)
        try:
            if callable(claim):
                claimed = bool(claim(intent))
                if claimed:
                    veto = self._final_entry_veto(dispatch, intent)
                    if veto is not None:
                        return veto
                    result = await self._submit_guarded(dispatch, intent)
                else:
                    existing = self._store.get(intent.client_oid)
                    if existing is None:
                        raise RuntimeError("entry intent claim lost without a durable record")
                    intent = existing
                    result = await self._execution.reconcile_intent(intent)
            else:
                # Compatibility for legacy stores; production stores implement atomic claim.
                existing = self._store.get(intent.client_oid)
                claimed = existing is None
                if existing is None:
                    self._store.save(intent)
                    veto = self._final_entry_veto(dispatch, intent)
                    if veto is not None:
                        return veto
                    result = await self._submit_guarded(dispatch, intent)
                else:
                    intent = existing
                    result = await self._execution.reconcile_intent(intent)
        except BitgetEntryVeto as exc:
            return exc.outcome
        except Exception:
            # A POST/read-back failure is ambiguous: retain margin as unknown, never release it.
            try:
                intent.state = "unknown"
                self._store.update(intent)
                self._resolve_reservation(intent, "UNKNOWN")
            except Exception:
                pass
            raise
        self._persist_readback(intent, result)
        await self._reconcile_post_fill(intent, result)
        self._resolve_reservation(intent, _dispatcher_status(result.status))
        if result.status not in {LiveOrderStatus.FILLED, LiveOrderStatus.PARTIAL}:
            return _dispatcher_status(result.status)
        # Losing the entry claim means any previous protection POST is ambiguous.
        # Replays are read-only even when no placement acknowledgement survived.
        try:
            protection = (
                await self._execution.protect_filled_position(
                    intent, self._protection_plan(dispatch, intent, result.filled_qty), self._store
                )
                if claimed
                else await self._read_recovery_protection(intent, dispatch)
            )
        except Exception:
            # Entry evidence was committed above. A protection/containment error
            # cannot turn a proven fill into an ambiguous entry or permit replay.
            return (
                "PARTIAL_UNPROTECTED"
                if result.status is LiveOrderStatus.PARTIAL
                else "FILLED_UNPROTECTED"
            )
        if (
            protection.state is not ProtectionState.VENUE_PROTECTED
            or protection.observed_quantity != result.filled_qty
        ):
            return (
                "PARTIAL_UNPROTECTED"
                if result.status is LiveOrderStatus.PARTIAL
                else "FILLED_UNPROTECTED"
            )
        return _dispatcher_status(result.status)

    @staticmethod
    def _aggregate_split(root: LiveIntentRecord, limit: LiveIntentRecord) -> LiveIntentRecord:
        if (
            root.entry_leg != "market"
            or limit.entry_leg != "limit"
            or root.dispatch_id is None
            or root.dispatch_id != limit.dispatch_id
            or limit.client_oid != root.client_oid + "-limit"
            or limit.symbol != root.symbol
            or limit.side != root.side
            or limit.margin_reservation_id != root.margin_reservation_id
            or limit.planned_stop_loss != root.planned_stop_loss
            or limit.planned_take_profits != root.planned_take_profits
            or root.order_type != "market"
            or limit.order_type != "limit"
            or root.requested_qty * 3 != limit.requested_qty
            or root.filled_qty < 0
            or root.filled_qty > root.requested_qty
            or limit.filled_qty < 0
            or limit.filled_qty > limit.requested_qty
        ):
            raise ValueError("split durable ownership mismatch")
        quantity = root.filled_qty + limit.filled_qty
        requested = root.requested_qty + limit.requested_qty
        priced = [(leg.filled_qty, leg.avg_price) for leg in (root, limit) if leg.filled_qty > 0]
        average = (
            sum((qty * price for qty, price in priced if price is not None), Decimal("0"))
            / quantity
            if quantity > 0 and all(price is not None for _, price in priced)
            else None
        )
        return replace(
            root,
            requested_qty=requested,
            filled_qty=quantity,
            avg_price=average,
            fee=root.fee + limit.fee,
            entry_leg=None,
            state="filled"
            if quantity == requested
            else "partially_filled"
            if quantity > 0
            else "unknown"
            if "unknown" in {root.state, limit.state}
            else "acknowledged",
            provider_fill_ids=tuple(
                dict.fromkeys((*root.provider_fill_ids, *limit.provider_fill_ids))
            ),
        )

    @staticmethod
    def _aggregate_result(intent: LiveIntentRecord) -> AsyncExecutionResult:
        status = (
            LiveOrderStatus.FILLED
            if intent.filled_qty == intent.requested_qty
            else LiveOrderStatus.PARTIAL
            if intent.filled_qty > 0
            else LiveOrderStatus.UNKNOWN
            if intent.state == "unknown"
            else LiveOrderStatus.ACCEPTED
        )
        return AsyncExecutionResult(
            intent.client_oid,
            status,
            intent.filled_qty,
            intent.avg_price,
            intent.fee,
            intent.provider_order_id,
            (),
        )

    async def _reconcile_waiting_leg(self, root: LiveIntentRecord, limit: LiveIntentRecord) -> None:
        self._aggregate_split(root, limit)
        if limit.state == "staged":
            # Startup/replay cannot infer whether the root protection placement
            # completed. This definitely-unsent child is retired, never exposed.
            limit.state = "cancelled"
            limit.provider_terminal = True
            self._store.update(limit)
        elif not limit.provider_terminal:
            result = await self._execution.reconcile_intent(limit)
            self._persist_readback(limit, result)

    async def _submit_split(
        self, dispatch: BitgetDispatch, submission: BitgetEntrySubmission
    ) -> str:
        route = submission.entry_route
        assert route is not None
        if route.market_quantity <= 0 or route.limit_quantity != route.market_quantity * 3:
            raise ValueError("split requires exact 25/75 quantities")
        if route.market_quantity + route.limit_quantity != submission.quantity:
            raise ValueError("split exceeds admitted quantity")
        root = replace(
            self._intent(dispatch, submission),
            requested_qty=route.market_quantity,
            dispatch_id=dispatch.id,
            entry_leg="market",
        )
        limit = replace(
            root,
            client_oid=root.client_oid + "-limit",
            requested_qty=route.limit_quantity,
            entry_leg="limit",
            order_type="limit",
            limit_price=route.limit_price,
            state="staged",
        )
        if self._store.get(root.client_oid) is None:
            veto = self._final_entry_veto_before_claim(dispatch)
            if veto is not None:
                self.release_reservation(submission)
                return veto
        claimed = self._store.claim_split(root, limit)
        if not claimed:
            root_existing = self._store.get(root.client_oid)
            limit_existing = self._store.get(limit.client_oid)
            if root_existing is None or limit_existing is None:
                raise ValueError("split replay lacks durable legs")
            root = root_existing
            limit = limit_existing
            if root is None or limit is None:
                raise ValueError("split replay lacks durable legs")
            result = await self._execution.reconcile_intent(root)
            self._persist_readback(root, result)
            await self._reconcile_waiting_leg(root, limit)
            aggregate = self._aggregate_split(root, limit)
            if aggregate.filled_qty > 0:
                protection = await self._read_recovery_protection(aggregate, dispatch)
                if protection.state is not ProtectionState.VENUE_PROTECTED:
                    return "PARTIAL_UNPROTECTED"
            return _dispatcher_status(self._aggregate_result(aggregate).status)
        try:
            result = await self._submit_guarded(dispatch, root)
            self._persist_readback(root, result)
            if result.filled_qty <= 0:
                limit.state = "cancelled"
                limit.provider_terminal = True
                self._store.update(limit)
                self._resolve_reservation(root, _dispatcher_status(result.status))
                return _dispatcher_status(result.status)
            protection = await self._execution.protect_filled_position(
                root, self._protection_plan(dispatch, root, root.filled_qty), self._store
            )
            if (
                protection.state is not ProtectionState.VENUE_PROTECTED
                or protection.observed_quantity != root.filled_qty
                or result.status is not LiveOrderStatus.FILLED
            ):
                limit.state = "cancelled"
                limit.provider_terminal = True
                self._store.update(limit)
                self._resolve_reservation(root, "FILLED")
                return (
                    "PARTIAL_UNPROTECTED"
                    if protection.state is not ProtectionState.VENUE_PROTECTED
                    else "PARTIAL"
                )
            if self._store.claim_entry_post(limit.client_oid):
                limit_fetched = self._store.get(limit.client_oid)
                assert limit_fetched is not None
                limit = limit_fetched
                result = await self._submit_guarded(dispatch, limit)
                self._persist_readback(limit, result)
            else:
                limit_fetched = self._store.get(limit.client_oid)
                assert limit_fetched is not None
                limit = limit_fetched
            aggregate = self._aggregate_split(root, limit)
            self._resolve_reservation(root, "FILLED")
            await self._reconcile_post_fill(aggregate, self._aggregate_result(aggregate))
            if limit.filled_qty > 0:
                proof = await self._read_recovery_protection(aggregate, dispatch)
                if (
                    proof.state is not ProtectionState.VENUE_PROTECTED
                    or proof.observed_quantity != aggregate.filled_qty
                ):
                    return "PARTIAL_UNPROTECTED"
            return _dispatcher_status(self._aggregate_result(aggregate).status)
        except BitgetEntryVeto as exc:
            current = self._store.get(limit.client_oid)
            if current is not None and current.state == "staged":
                current.state = "cancelled"
                current.provider_terminal = True
                self._store.update(current)
            return "PARTIAL" if root.filled_qty > 0 else exc.outcome
        except Exception:
            self._resolve_reservation(root, "UNKNOWN")
            raise

    def _final_entry_veto_before_claim(self, dispatch: BitgetDispatch) -> str | None:
        unresolved = getattr(self._dispatch_repository, "unresolved_protection_issues", None)
        if (callable(unresolved) and unresolved()) or (
            callable(getattr(self._recovery_protection, "inventory", None))
            and not self.recovery_ready
        ):
            return "REJECTED"
        if (
            self._dispatch_repository is not None
            and not self._dispatch_repository.entry_source_eligible(dispatch.id)
        ):
            return "EXPIRED"
        return None

    async def cancel_pending_entries(self, symbol: str, management_id: UUID) -> bool:
        """Fence ONLY owned LIMIT ENTRY children, then prove terminal provider truth."""
        try:
            binding = getattr(self._recovery_protection, "verify_account_binding", None)
            if callable(binding):
                await binding()
            for limit in self._store.pending_entries(symbol, management_id):
                root = self._store.get(limit.client_oid.removesuffix("-limit"))
                if root is None:
                    return False
                self._aggregate_split(root, limit)
                self._store.claim_cancel(limit.client_oid, management_id)
                limit_updated = self._store.get(limit.client_oid)
                if limit_updated is None:
                    return False
                limit = limit_updated
                if not limit.provider_terminal:
                    cancel = getattr(self._execution, "cancel_pending_entry", None)
                    result = (
                        await cancel(
                            limit, (lambda oid=limit.client_oid: self._store.claim_cancel_post(oid))
                        )
                        if callable(cancel)
                        else await self._execution.reconcile_intent(limit)
                    )
                    self._persist_readback(limit, result)
                if not limit.provider_terminal:
                    return False
                root_result = await self._execution.reconcile_intent(root)
                self._persist_readback(root, root_result)
                aggregate = self._aggregate_split(root, limit)
                if aggregate.filled_qty > 0:
                    stop_loss_val = aggregate.active_stop_loss_price or aggregate.planned_stop_loss
                    assert stop_loss_val is not None
                    assert aggregate.planned_take_profits is not None
                    plan = ProtectionPlan(
                        exchange=Exchange.BITGET,
                        symbol=symbol,
                        direction=Direction.LONG if root.side == "BUY" else Direction.SHORT,
                        quantity=aggregate.filled_qty,
                        stop_loss=stop_loss_val,
                        take_profits=aggregate.planned_take_profits,
                    )
                    if self._recovery_protection is not None:
                        # Cancellation readiness is terminal ENTRY proof, not an
                        # admission verdict. A flat source-close retry must remain
                        # able to reconcile its real close fills.
                        await self._recovery_protection(aggregate, plan)
            return not self._store.pending_entries(symbol)
        except Exception:
            return False

    async def _submit_guarded(
        self, dispatch: BitgetDispatch, intent: LiveIntentRecord
    ) -> AsyncExecutionResult:
        def final_check() -> None:
            veto = self._final_entry_veto(dispatch, intent)
            if veto is not None:
                raise BitgetEntryVeto(veto)

        guarded = getattr(self._execution, "submit_entry_guarded", None)
        if callable(guarded):
            submit = cast(
                Callable[[LiveIntentRecord, Callable[[], None]], Awaitable[AsyncExecutionResult]],
                guarded,
            )
            return await submit(intent, final_check)
        # Synchronous test/legacy boundaries still receive a last claim check.
        final_check()
        return await self._execution.submit_entry(intent)

    def _final_entry_veto(self, dispatch: BitgetDispatch, intent: LiveIntentRecord) -> str | None:
        """Only the winning, known-unsent claim may retire its own intent.

        Claim persistence can block. Re-read database SOURCE time after it returns,
        with no intervening await before the ENTRY submission.
        """
        repository = self._dispatch_repository
        if intent.entry_leg == "limit":
            current = self._store.get(intent.client_oid)
            if current is None:
                return "REJECTED"
            if current.cancel_requested_by is not None or current.provider_terminal:
                intent.state = "cancelled"
                intent.provider_terminal = True
                self._store.update(intent)
                return "REJECTED"
        unresolved = getattr(repository, "unresolved_protection_issues", None)
        if (callable(unresolved) and unresolved()) or (
            callable(getattr(self._recovery_protection, "inventory", None))
            and not self.recovery_ready
        ):
            intent.state = "rejected"
            intent.provider_terminal = True
            self._store.update(intent)
            self._resolve_reservation(intent, "REJECTED")
            return "REJECTED"
        if repository is not None and not repository.entry_source_eligible(dispatch.id):
            intent.state = "rejected"
            intent.provider_terminal = True
            self._store.update(intent)
            # The first read must retain any durable ambiguous intent. This second
            # read can retire our now-proven unsent rejected claim, never a replay.
            repository.entry_source_eligible(dispatch.id)
            return "EXPIRED"
        # Eligibility may wait on FOR UPDATE. Read BOTH kill scopes only after
        # all eligibility/protection DB operations, immediately before permission.
        # Never gate reconciliation or closes with this ENTRY-only boundary.
        killed = self._kill_switch is not None and any(
            (self._kill_switch.is_active("global"), self._kill_switch.is_active("bitget"))
        )
        if killed:
            intent.state = "rejected"
            intent.provider_terminal = True
            self._store.update(intent)
            self._resolve_reservation(intent, "REJECTED")
            return "REJECTED"
        return None

    async def reconcile_active_reservations(self) -> int:
        """GET-reconcile active commitments before a restarted worker admits entries."""
        if self._dispatch_repository is None:
            # Compatibility sweep is not evidence of complete lifecycle recovery.
            self.recovery_ready = False
            return await self._reconcile_reservations_legacy()
        return await self.recover_entry_lifecycles()

    async def _reconcile_reservations_legacy(self) -> int:
        """Legacy reservation-only helper; not a startup readiness boundary."""
        if self._reservations is None:
            return 0
        active = getattr(self._reservations, "active_client_order_ids", None)
        escalate = getattr(self._reservations, "escalate_expired", None)
        if not callable(active):
            raise TypeError("margin reservation repository lacks active reservation query")
        reconciled = 0
        for reservation_id, client_oid, expired in cast(Any, active)():
            intent = self._store.get(client_oid)
            if intent is None:
                if expired and callable(escalate):
                    escalate(reservation_id)
                continue
            try:
                result = await self._execution.reconcile_intent(intent)
                self._persist_readback(intent, result)
                await self._reconcile_post_fill(intent, result)
                status = _dispatcher_status(result.status)
                if status in {"FILLED", "PARTIAL", "REJECTED"}:
                    self._resolve_reservation(intent, status)
                    reconciled += 1
                elif expired and callable(escalate):
                    escalate(reservation_id)
            except Exception:
                self._resolve_reservation(intent, "UNKNOWN")
                if expired and callable(escalate):
                    escalate(reservation_id)
        return reconciled

    @staticmethod
    def client_oid(dispatch: BitgetDispatch) -> str:
        side = "BUY" if dispatch.direction == Direction.LONG.value else "SELL"
        token = dispatch.id.hex[:16]
        if dispatch.source_channel_id is not None and dispatch.source_message_id is not None:
            token = uuid5(
                NAMESPACE_URL,
                f"fatty-bitget-entry:{dispatch.source_channel_id}:{dispatch.source_message_id}:"
                f"{dispatch.pair_token}:{side}",
            ).hex[:16]
        return f"live-bitget-{dispatch.pair_token}-{token}"

    @staticmethod
    def _intent(dispatch: BitgetDispatch, submission: BitgetEntrySubmission) -> LiveIntentRecord:
        if submission.quantity <= 0:
            raise ValueError("dispatch quantity must be positive")
        side = "BUY" if dispatch.direction == Direction.LONG.value else "SELL"
        return LiveIntentRecord(
            exchange=Exchange.BITGET.value,
            client_oid=BitgetDispatchExecution.client_oid(dispatch),
            symbol=dispatch.pair_token,
            side=side,
            requested_qty=submission.quantity,
            planned_leverage=submission.effective_leverage,
            planned_margin_usdt=submission.planned_margin_usdt,
            planned_notional_usdt=submission.planned_notional_usdt,
            margin_mode=submission.margin_mode,
            balance_snapshot_id=submission.balance_snapshot_id,
            margin_reservation_id=submission.margin_reservation_id,
            planned_stop_loss=submission.planned_stop_loss,
            planned_take_profits=submission.planned_take_profits,
        )

    @staticmethod
    def _protection_plan(
        dispatch: BitgetDispatch, intent: LiveIntentRecord, filled_qty: Decimal
    ) -> ProtectionPlan:
        if intent.planned_stop_loss is None or intent.planned_take_profits is None:
            raise ValueError("durable validated protection plan is missing")
        return ProtectionPlan(
            exchange=Exchange.BITGET,
            symbol=dispatch.pair_token,
            direction=Direction(dispatch.direction),
            quantity=filled_qty,
            stop_loss=intent.planned_stop_loss,
            take_profits=intent.planned_take_profits,
        )

    def release_reservation(self, submission: BitgetEntrySubmission) -> None:
        """Release admission only when dispatcher rejects before any entry POST."""
        if self._reservations is None:
            return
        resolve = getattr(self._reservations, "resolve", None)
        if not callable(resolve):
            raise TypeError("margin reservation repository lacks resolve")
        resolve(submission.margin_reservation_id, "REJECTED")

    def _resolve_reservation(self, intent: LiveIntentRecord, outcome: str) -> None:
        if self._reservations is None or intent.margin_reservation_id is None:
            return
        resolve = getattr(self._reservations, "resolve", None)
        if not callable(resolve):
            raise TypeError("margin reservation repository lacks resolve")
        # Repository updates are idempotent; resolution is after durable intent persistence.
        resolve(intent.margin_reservation_id, outcome)

    async def _reconcile_post_fill(
        self, intent: LiveIntentRecord, result: AsyncExecutionResult
    ) -> None:
        if result.status not in {LiveOrderStatus.FILLED, LiveOrderStatus.PARTIAL}:
            return
        reconcile = getattr(self._execution, "reconcile_post_fill", None)
        if callable(reconcile):
            await cast(Any, reconcile)(intent, result)

    def _persist_readback(self, intent: LiveIntentRecord, result: AsyncExecutionResult) -> None:
        if result.client_oid != intent.client_oid:
            raise ValueError("Bitget readback client OID does not match durable intent")
        if not result.filled_qty.is_finite() or result.filled_qty < intent.filled_qty:
            # A GET gap/partial response cannot erase previously committed fills.
            # Fail recovery closed and retain the durable provider evidence.
            raise ValueError("Bitget readback quantity regression or non-finite quantity")
        intent.state = {
            LiveOrderStatus.ACCEPTED: "acknowledged",
            LiveOrderStatus.PARTIAL: "partially_filled",
            LiveOrderStatus.FILLED: "filled",
            LiveOrderStatus.REJECTED: "rejected",
            LiveOrderStatus.UNKNOWN: "unknown",
        }[result.status]
        intent.filled_qty = result.filled_qty
        intent.avg_price = result.avg_price
        intent.fee = result.fee
        intent.provider_terminal = (
            result.provider_terminal or result.status is LiveOrderStatus.FILLED
        )
        intent.provider_order_id = result.provider_order_id or intent.provider_order_id
        intent.provider_fill_ids = result.provider_fill_ids
        intent.provider_fills = result.provider_fills
        self._store.update(intent)


def _dispatcher_status(status: LiveOrderStatus) -> str:
    return {
        LiveOrderStatus.ACCEPTED: "ACKNOWLEDGED",
        LiveOrderStatus.PARTIAL: "PARTIAL",
        LiveOrderStatus.FILLED: "FILLED",
        LiveOrderStatus.REJECTED: "REJECTED",
        LiveOrderStatus.UNKNOWN: "UNKNOWN",
    }[status]
