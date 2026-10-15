"""Scoped console trading over the canonical Bitget admission and intent ledger.

Construction and preview have no provider credentials or mutation authority. Only
worker execution constructs the signed provider graph; recovery never repeats POST.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from fatty_trader.exchanges.bitget.live import LiveIntentRecord
from fatty_trader.execution.bitget_dispatch_repository import BitgetDispatch

_DECIMAL = {"type": "string", "pattern": r"^[0-9]+(?:\.[0-9]+)?$"}
_SYMBOL = {"type": "string", "pattern": r"^[A-Z0-9]{2,20}$"}
_OID = {"type": "string", "minLength": 1, "maxLength": 128}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


ACTION_SCHEMAS = {
    "trade.open": _schema(
        {
            "symbol": _SYMBOL,
            "direction": {"enum": ["LONG", "SHORT"]},
            "order_type": {"enum": ["MARKET", "LIMIT"]},
            "entry_price": _DECIMAL,
            "stop_loss": _DECIMAL,
            "take_profit": _DECIMAL,
            "margin": _DECIMAL,
            "leverage": {"type": "integer", "minimum": 5, "maximum": 20},
        },
        ["symbol", "direction", "order_type", "stop_loss", "take_profit"],
    ),
    "entry.cancel_owned": _schema({"symbol": _SYMBOL}, ["symbol"]),
    "order.cancel_exact": _schema({"client_oid": _OID}, ["client_oid"]),
    "position.close": _schema({"client_oid": _OID}, ["client_oid"]),
    "position.close_partial": _schema(
        {"client_oid": _OID, "quantity": _DECIMAL}, ["client_oid", "quantity"]
    ),
    "protection.set_sl": _schema({"client_oid": _OID, "price": _DECIMAL}, ["client_oid", "price"]),
    "protection.set_tp": _schema({"client_oid": _OID, "price": _DECIMAL}, ["client_oid", "price"]),
    "protection.sl_to_entry": _schema({"client_oid": _OID}, ["client_oid"]),
    "protection.repair_native": _schema({"client_oid": _OID}, ["client_oid"]),
    "management.apply": _schema(
        {
            "symbol": _SYMBOL,
            "action": {"enum": ["CLOSE", "TP1_BOOKED", "TP_BOOKED", "SL_TO_ENTRY"]},
        },
        ["symbol", "action"],
    ),
    "protection.register_fallback": _schema(
        {"client_oid": _OID, "epoch": {"type": "string", "pattern": "^[1-9][0-9]*$"}},
        ["client_oid", "epoch"],
    ),
    "protection.disable_fallback": _schema(
        {"client_oid": _OID, "epoch": {"type": "string", "pattern": "^[1-9][0-9]*$"}},
        ["client_oid", "epoch"],
    ),
    "protection.update_fallback": _schema(
        {
            "client_oid": _OID,
            "epoch": {"type": "string", "pattern": "^[1-9][0-9]*$"},
            "stop_loss": _DECIMAL,
            "take_profit": _DECIMAL,
        },
        ["client_oid", "epoch", "stop_loss", "take_profit"],
    ),
    "intent.reconcile": _schema({"client_oid": _OID}, ["client_oid"]),
    "protection.observe": _schema({"client_oid": _OID}, ["client_oid"]),
    "management.reconcile": _schema({"job_id": _OID}, ["job_id"]),
}
TRADING_ACTIONS = frozenset(ACTION_SCHEMAS)


def _decimal(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError("amount must be finite and positive")
    return result


def _version(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


class TradingHandlers:
    def __init__(
        self, environ: Mapping[str, str], store: Any, *, runtime_factory: Any = None
    ) -> None:
        self.environ = dict(environ)
        self.store = store
        self.runtime_factory = runtime_factory
        self._runtime: Any = None
        self._intents: Any = None
        self._active_job: dict[str, Any] | None = None

    def _scope(self, scope: dict[str, Any]) -> str:
        account = self.environ.get(
            "CONSOLE_ACCOUNT_ID", self.environ.get("BITGET_ACCOUNT_ID", "configured-bitget")
        )
        if scope != {"account_id": account, "venue": "bitget", "environment": "LIVE"}:
            raise ValueError("trade scope does not match configured LIVE account")
        return f"bitget:{account}:LIVE"

    def _effective(self, scope: dict[str, Any]) -> dict[str, str]:
        if self.environ.get("BITGET_API_KEY"):
            return self.environ
        observation = self.store.latest_snapshot("effective_config", scope)
        if not observation or observation.get("error"):
            return {}
        timestamp = observation["observed_at"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if timestamp.tzinfo is None or not timedelta(seconds=-1) <= datetime.now(
            UTC
        ) - timestamp <= timedelta(seconds=30):
            return {}
        return {
            key: str(value)
            for key, value in observation["data"].get("values", {}).items()
            if value is not None
        }

    def _gates(
        self, scope: dict[str, Any], *, entry: bool = False, fallback: bool = False
    ) -> list[dict[str, Any]]:
        key = self._scope(scope)
        config = self._effective(scope)
        gates = []
        for name, expected in (
            ("TRADER_MODE", "LIVE"),
            ("BITGET_MODE", "LIVE"),
            ("BITGET_EXECUTION_ENABLED", "1"),
            ("BITGET_OPERATOR_MUTATIONS_ENABLED", "1"),
        ):
            gates.append({"name": name, "allowed": config.get(name) == expected})
        if fallback:
            gates.append(
                {
                    "name": "BITGET_FALLBACK_MUTATIONS_ENABLED",
                    "allowed": config.get("BITGET_FALLBACK_MUTATIONS_ENABLED") == "1",
                }
            )
            for latch in ("global", "bitget"):
                active = self.store.one(
                    "SELECT active FROM venue_kill_switches WHERE scope=%s", (latch,)
                )
                gates.append({"name": latch, "allowed": not active or not active["active"]})
        if entry:
            pause = self.store.one(
                "SELECT active FROM console_admission_pauses WHERE scope=%s", (key,)
            )
            gates.append({"name": "console-admission", "allowed": not pause or not pause["active"]})
            for name in ("global", "bitget", "bitget-protection-stream"):
                row = self.store.one(
                    "SELECT active FROM venue_kill_switches WHERE scope=%s", (name,)
                )
                gates.append({"name": name, "allowed": not row or not row["active"]})
            gates.append(
                {
                    "name": "approval-reference",
                    "allowed": bool(config.get("BITGET_APPROVAL_REFERENCE", "").strip()),
                }
            )
            gates.append(
                {
                    "name": "concurrency-cap",
                    "allowed": int(config.get("BITGET_CANARY_MAX_ORDERS", "0")) > 0,
                }
            )
        return [{**gate, "verdict": "pass" if gate["allowed"] else "fail"} for gate in gates]

    def capability(self, action: str, scope: dict[str, Any]) -> dict[str, Any]:
        if action in {"intent.reconcile", "management.reconcile", "protection.observe"}:
            self._scope(scope)
            return {
                "gates": [{"name": "GET-only", "verdict": "pass", "allowed": True}],
                "reasons": [],
            }
        gates = self._gates(
            scope, entry=action == "trade.open", fallback=action.endswith("_fallback")
        )
        return {
            "gates": gates,
            "reasons": [
                {"code": g["name"], "message": g["name"]} for g in gates if not g["allowed"]
            ],
        }

    def current_resource_version(
        self,
        action: str,
        arguments: dict[str, Any],
        target_refs: list[dict[str, Any]],
        scope: dict[str, Any],
    ) -> str:
        self._scope(scope)
        evidence: Any
        if action == "trade.open":
            symbol = arguments["symbol"]
            evidence = []
            for kind in (
                "positions",
                "contracts",
                "clock",
                f"ticker:{symbol}",
                f"position_lever:{symbol}",
                f"account_admission:{symbol}",
                "account_identity",
            ):
                snapshot = self.store.latest_snapshot(kind, scope)
                data = snapshot.get("data") if snapshot else None
                evidence.append(
                    {key: value for key, value in (data or {}).items() if key != "observed_at"}
                )
        elif action == "management.reconcile":
            evidence = self.store.one(
                "SELECT id,arguments,state,result FROM console_jobs WHERE id=%s",
                (arguments["job_id"],),
            )
        elif action in {"entry.cancel_owned", "management.apply"}:
            evidence = self._pending_owned_entries(
                arguments["symbol"], scope, include_terminal=True
            )
        elif action in {"order.cancel_exact", "intent.reconcile"}:
            evidence = self.store.one(
                "SELECT * FROM live_order_intents WHERE client_order_id=%s",
                (arguments["client_oid"],),
            )
        else:
            evidence = self._owned(arguments["client_oid"], scope)
        return _version(evidence)

    def _owned(self, client_oid: str, scope: dict[str, Any]) -> Any:
        self._scope(scope)
        row = self.store.one(
            """SELECT i.*,r.environment,r.state AS reservation_state
            FROM live_order_intents i JOIN bitget_margin_reservations r
            ON r.id=i.margin_reservation_id AND r.client_order_id=i.client_order_id
            JOIN dispatches d ON d.id=r.dispatch_id
            WHERE i.exchange='bitget' AND i.role='ENTRY' AND i.client_order_id=%s
              AND i.entry_leg IS DISTINCT FROM 'limit' AND r.environment=%s
              AND NOT EXISTS(SELECT 1 FROM bitget_baseline_records b
                WHERE (b.record_kind='intent' AND b.record_id=i.id)
                   OR (b.record_kind='reservation' AND b.record_id=r.id)
                   OR (b.record_kind='dispatch' AND b.record_id=d.id))""",
            (client_oid, scope["environment"]),
        )
        if not row:
            raise ValueError("target has no exact non-baseline ENTRY ownership")
        return row

    def _owned_limit(self, client_oid: str, scope: dict[str, Any]) -> Any:
        self._scope(scope)
        row = self.store.one(
            """SELECT i.* FROM live_order_intents i
            JOIN bitget_margin_reservations r ON r.id=i.margin_reservation_id
            JOIN dispatches d ON d.id=i.dispatch_id AND d.id=r.dispatch_id
            WHERE i.client_order_id=%s AND i.exchange='bitget' AND i.role='ENTRY'
              AND i.order_type='limit' AND r.environment=%s
              AND NOT EXISTS(SELECT 1 FROM bitget_baseline_records b
                WHERE (b.record_kind='intent' AND b.record_id=i.id)
                   OR (b.record_kind='reservation' AND b.record_id=r.id)
                   OR (b.record_kind='dispatch' AND b.record_id=d.id))""",
            (client_oid, scope["environment"]),
        )
        if row is None:
            raise ValueError("exact cancellation requires non-baseline owned LIMIT ENTRY")
        return row

    def _pending_owned_entries(
        self, symbol: str, scope: dict[str, Any], *, include_terminal: bool = False
    ) -> Any:
        self._scope(scope)
        return self.store.query(
            """SELECT i.client_order_id,i.state,i.provider_terminal,i.filled_qty,
            i.cancel_requested_by,i.cancel_post_claimed FROM live_order_intents i
            JOIN bitget_margin_reservations r ON r.id=i.margin_reservation_id
            JOIN dispatches d ON d.id=i.dispatch_id AND d.id=r.dispatch_id
            WHERE i.exchange='bitget' AND i.role='ENTRY' AND i.symbol=%s
              AND i.order_type='limit' AND r.environment=%s
              AND (%s OR NOT i.provider_terminal)
              AND NOT EXISTS(SELECT 1 FROM bitget_baseline_records b
                  WHERE (b.record_kind='intent' AND b.record_id=i.id)
                     OR (b.record_kind='reservation' AND b.record_id=r.id)
                     OR (b.record_kind='dispatch' AND b.record_id=d.id))
            ORDER BY i.client_order_id""",
            (symbol, scope["environment"], include_terminal),
        )

    async def preview(
        self,
        action: str,
        arguments: dict[str, Any],
        target_refs: list[dict[str, Any]],
        scope: dict[str, Any],
    ) -> dict[str, Any]:
        from jsonschema import validate

        validate(arguments, ACTION_SCHEMAS[action])
        args = dict(arguments)
        gates = self._gates(
            scope, entry=action == "trade.open", fallback=action.endswith("_fallback")
        )
        effects: list[dict[str, Any]] = []
        warnings: list[str] = []
        evidence: Any = {}
        if action in {"intent.reconcile", "protection.observe", "management.reconcile"}:
            return {
                "effects": [{"kind": action, "provider_methods": ["GET"]}],
                "gates": [{"name": "GET-only", "allowed": True, "verdict": "pass"}],
                "warnings": [],
                "normalized_arguments": args,
                "resource_version": self.current_resource_version(action, args, target_refs, scope),
            }
        if action == "trade.open":
            if args["order_type"] == "LIMIT" and "entry_price" not in args:
                raise ValueError("LIMIT requires an explicit entry_price")
            for field in ("stop_loss", "take_profit", "entry_price", "margin"):
                if field in args:
                    args[field] = str(_decimal(args[field]))
            try:
                venue = _SnapshotVenue(self.store, scope)
                price = _decimal(
                    args.get("entry_price")
                    or venue.client.snapshot("ticker", args["symbol"])["lastPr"]
                )
                dispatch = self._dispatch(UUID(int=0), args, price)
                from fatty_trader.service import _bitget_dispatch_preflight

                evidence = await _bitget_dispatch_preflight(
                    venue, self._effective(scope), preview_only=True
                )(dispatch)
                args["entry_price"] = str(price)
                gates.append({"name": "canonical-risk", "allowed": True})
            except (ValueError, KeyError, TypeError) as exc:
                gates.append({"name": "canonical-risk", "allowed": False, "reason": str(exc)})
            effects.append(
                {
                    "kind": "entry",
                    "symbol": args["symbol"],
                    "order_type": args["order_type"],
                    "protection": "native full-position SL and first-target TP",
                    **evidence,
                }
            )
        elif action in {"entry.cancel_owned", "management.apply"}:
            evidence = (
                []
                if action == "management.apply" and args["action"] == "SL_TO_ENTRY"
                else self._pending_owned_entries(args["symbol"], scope)
            )
            effects.append({"kind": "exact-owned-entry-cancellation", "entries": evidence})
            if action == "management.apply":
                ordering = (
                    "fresh position; owned SL to entry; strict readback"
                    if args["action"] == "SL_TO_ENTRY"
                    else (
                        "cancel entries; fresh position; reduce-only close if authorized; "
                        "readback; TP1 SL to entry"
                    )
                )
                effects.append({"kind": args["action"], "ordering": ordering})
        else:
            if action == "order.cancel_exact":
                evidence = self._owned_limit(args["client_oid"], scope)
                if not evidence:
                    raise ValueError(
                        "exact cancellation requires an owned LIMIT ENTRY; "
                        "no plan/manual cancellation"
                    )
            else:
                evidence = self._owned(args["client_oid"], scope)
            effects.append(
                {
                    "kind": action,
                    "client_oid": args["client_oid"],
                    "quantity": args.get("quantity"),
                    "price": args.get("price"),
                    "reduce_only": action.startswith("position."),
                }
            )
            if action != "order.cancel_exact":
                try:
                    details = self._preview_owned_effect(action, args, evidence, scope)
                    effects.append(details)
                    gates.append({"name": "exact-provider-ownership", "allowed": True})
                except (ValueError, KeyError, TypeError) as exc:
                    gates.append(
                        {"name": "exact-provider-ownership", "allowed": False, "reason": str(exc)}
                    )
        gates = [{**gate, "verdict": "pass" if gate["allowed"] else "fail"} for gate in gates]
        return {
            "effects": effects,
            "gates": gates,
            "warnings": warnings,
            "normalized_arguments": args,
            "resource_version": self.current_resource_version(action, args, target_refs, scope),
        }

    def _preview_owned_effect(
        self, action: str, args: dict[str, Any], root: dict[str, Any], scope: dict[str, Any]
    ) -> dict[str, Any]:
        from psycopg.rows import tuple_row

        from fatty_trader.exchanges.bitget.metadata import find_contract, metadata_from_contract
        from fatty_trader.risk.sizing import round_price_to_tick
        from fatty_trader.storage.live_intents import PostgresLiveIntentStore

        client = _SnapshotClient(self.store, scope)
        rows = client.snapshot("positions")
        positions = [
            p for p in rows if p["symbol"] == root["symbol"] and _decimal_or_zero(p["total"]) > 0
        ]
        if len(positions) != 1:
            raise ValueError("one fresh provider position is required")
        row = positions[0]
        with self.store.connection() as connection:
            remaining = PostgresLiveIntentStore(
                self.store._connection_factory, console_enabled=True
            ).remaining_owned_quantity(
                args["client_oid"], cursor=connection.cursor(row_factory=tuple_row)
            )
        quantity = _decimal(row["total"])
        if quantity != remaining or row["holdSide"] != (
            "long" if root["side"] == "BUY" else "short"
        ):
            raise ValueError("provider residual differs from exact owned residual")
        metadata = metadata_from_contract(
            find_contract(client.snapshot("contracts"), root["symbol"])
        )
        requested = _decimal(args["quantity"]) if action == "position.close_partial" else quantity
        if (
            requested > quantity
            or requested % metadata.size_step
            or requested < metadata.min_order_qty
        ):
            raise ValueError("selected quantity violates residual or venue lot constraints")
        stop = _decimal(root.get("active_stop_loss_price") or root["planned_stop_loss"])
        targets = root["planned_take_profits"]
        if isinstance(targets, str):
            targets = json.loads(targets)
        target = _decimal(root.get("active_take_profit_price") or targets[0])
        if action in {"protection.set_sl", "protection.sl_to_entry"}:
            stop = round_price_to_tick(
                _decimal(row["openPriceAvg"] if action.endswith("sl_to_entry") else args["price"]),
                metadata.price_tick,
            )
        elif action == "protection.set_tp":
            target = round_price_to_tick(_decimal(args["price"]), metadata.price_tick)
        if action.endswith("_fallback") and str(row.get("cTime")) != args["epoch"]:
            raise ValueError("fallback epoch differs from provider position")
        return {
            "kind": action,
            "client_oid": args["client_oid"],
            "symbol": root["symbol"],
            "quantity": str(requested),
            "remaining_after": str(quantity - requested)
            if action.startswith("position.")
            else str(quantity),
            "reduce_only": action.startswith("position."),
            "stop_loss": str(stop),
            "take_profit": str(target),
            "retained_sl_id": row.get("stopLossId"),
            "retained_tp_id": row.get("takeProfitId"),
            "provider_epoch": row.get("cTime"),
        }

    @staticmethod
    def _dispatch(identifier: UUID, args: dict[str, Any], price: Decimal) -> BitgetDispatch:
        return BitgetDispatch(
            identifier,
            "QUEUED",
            f"console-{identifier}",
            1,
            args["symbol"],
            args["direction"],
            price,
            _decimal(args["stop_loss"]),
            (_decimal(args["take_profit"]),),
            order_type=args["order_type"],
            manual_margin=_decimal(args["margin"]) if args.get("margin") is not None else None,
            manual_leverage=args.get("leverage"),
        )

    def _entry_claim_authority(self, cursor: Any) -> None:
        if self._active_job is None:
            raise ValueError("entry requires active durable job")
        from psycopg.rows import dict_row

        cursor.row_factory = dict_row
        self.store.effect_authority(cursor, self._active_job)
        if any(not gate["allowed"] for gate in self._gates(self._active_job["scope"], entry=True)):
            raise ValueError("final console entry mutation gate is closed")
        from psycopg.rows import tuple_row

        cursor.row_factory = tuple_row

    def _cancel_claim_authority(self, cursor: Any) -> None:
        if self._active_job is None:
            raise ValueError("cancellation requires active durable job")
        from psycopg.rows import dict_row, tuple_row

        cursor.row_factory = dict_row
        self.store.effect_authority(cursor, self._active_job)
        if any(not gate["allowed"] for gate in self._gates(self._active_job["scope"])):
            raise ValueError("final console cancellation gate is closed")
        cursor.row_factory = tuple_row

    def _claim_intent(
        self, intent: LiveIntentRecord, *, close_remaining: Decimal | None = None
    ) -> bool:
        if self._active_job is None:
            raise ValueError("mutation requires active durable job")
        from fatty_trader.storage.live_intents import PostgresLiveIntentStore

        with self.store.connection() as connection:
            cursor = self.store.cursor(connection)
            self.store.effect_authority(cursor, self._active_job)
            if any(not gate["allowed"] for gate in self._gates(self._active_job["scope"])):
                raise ValueError("final console mutation gate is closed")
            claimed = PostgresLiveIntentStore._insert(cursor, intent)
            if (
                claimed
                and close_remaining is not None
                and not self._intents.verified_close_lifecycle.bind_requested_close(
                    self._graph().client,
                    intent.client_oid,
                    expected_remaining=close_remaining,
                    connection=connection,
                )
            ):
                raise ValueError("exact pre-POST close binding could not be proved")
            return claimed

    def _claim_owned_cancel_post(self, client_oid: str) -> bool:
        if self._active_job is None:
            raise ValueError("cancellation requires active durable job")
        with self.store.connection() as connection:
            cursor = self.store.cursor(connection)
            self.store.effect_authority(cursor, self._active_job)
            if any(not gate["allowed"] for gate in self._gates(self._active_job["scope"])):
                raise ValueError("final console cancellation gate is closed")
            cursor.execute(
                """UPDATE live_order_intents i SET cancel_post_claimed=TRUE,
                updated_at=clock_timestamp() FROM bitget_margin_reservations r,dispatches d
                WHERE i.exchange='bitget' AND i.client_order_id=%s
                AND i.role='ENTRY' AND i.order_type='limit' AND i.cancel_requested_by IS NOT NULL
                AND r.id=i.margin_reservation_id AND d.id=i.dispatch_id AND d.id=r.dispatch_id
                AND r.environment=%s AND NOT i.cancel_post_claimed AND NOT i.provider_terminal
                AND NOT EXISTS(SELECT 1 FROM bitget_baseline_records b
                    WHERE (b.record_kind='intent' AND b.record_id=i.id)
                       OR (b.record_kind='reservation' AND b.record_id=r.id)
                       OR (b.record_kind='dispatch' AND b.record_id=d.id))
                RETURNING i.client_order_id""",
                (client_oid, self._active_job["scope"]["environment"]),
            )
            return cursor.fetchone() is not None

    def _graph(self) -> Any:
        if self._runtime is None:
            import psycopg

            from fatty_trader.service import build_bitget_execution_runtime
            from fatty_trader.storage.live_intents import PostgresLiveIntentStore

            self._intents = PostgresLiveIntentStore(psycopg.connect, console_enabled=True)
            self._runtime = (
                self.runtime_factory(self.environ)
                if self.runtime_factory
                else build_bitget_execution_runtime(
                    self.environ, intent_store_factory=lambda: self._intents
                )
            )
            if self._runtime is None:
                raise ValueError("canonical execution graph is gated")
        return self._runtime

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        return await self._run(job, reconcile=False)

    async def reconcile(self, job: dict[str, Any]) -> dict[str, Any]:
        return await self._run(job, reconcile=True)

    async def _run(self, job: dict[str, Any], *, reconcile: bool) -> dict[str, Any]:
        action, args, scope = job["action"], job["arguments"], job["scope"]
        self._active_job = job
        read_only = action in {"intent.reconcile", "management.reconcile", "protection.observe"}
        if (
            not reconcile
            and not read_only
            and any(
                not gate["allowed"]
                for gate in self._gates(
                    scope, entry=action == "trade.open", fallback=action.endswith("_fallback")
                )
            )
        ):
            return {"state": "failed", "result": {"code": "mutation_gated"}}
        runtime = self._graph()
        identity = await runtime.client._get("/api/v2/spot/account/info")
        if not isinstance(identity, dict) or str(identity.get("userId")) != scope["account_id"]:
            raise ValueError("authenticated provider account differs from selected scope")
        self._intents.entry_claim_authority = self._entry_claim_authority
        self._intents.cancel_claim_authority = self._cancel_claim_authority
        runtime.execution.console_cancel_claim = self._claim_owned_cancel_post
        if action == "management.reconcile":
            original = self.store.one(
                "SELECT * FROM console_jobs WHERE id=%s AND action='management.apply'",
                (args["job_id"],),
            )
            if not original or original["scope"] != scope:
                raise ValueError("management reconciliation target does not match scope")
            oid = f"console-{UUID(str(original['id'])).hex}"
            result = await self._management(original, oid, True)
            return {
                "state": "succeeded" if result.get("confirmed") else "reconciliation_pending",
                "result": result,
            }
        if action == "intent.reconcile":
            intent = self._intents.get(args["client_oid"])
            if not intent:
                raise ValueError("existing intent is required for GET reconciliation")
            if intent.role == "ENTRY":
                self._owned(intent.client_oid.removesuffix("-limit"), scope)
                await runtime.execution.recover_entry_lifecycles()
                return {
                    "state": "succeeded"
                    if runtime.execution.recovery_ready
                    else "reconciliation_pending",
                    "result": {"client_oid": intent.client_oid, "GET_only": True},
                }
            result = (
                await self._close_readback(intent.client_oid)
                if intent.role in {"CLOSE", "EMERGENCY_CLOSE"}
                else await self._protection_readback(intent.client_oid)
            )
            return {
                "state": "succeeded" if result["confirmed"] else "reconciliation_pending",
                "result": result,
            }
        if action == "protection.observe":
            root = self._intents.get(args["client_oid"])
            position = await self._position(root, scope)
            result = await self._observe_protection(root, position)
            return {
                "state": "succeeded" if result["confirmed"] else "reconciliation_pending",
                "result": result,
            }
        oid = f"console-{UUID(str(job['id'])).hex}"
        try:
            if action == "trade.open":
                result = await self._open(job, reconcile)
            elif action in {"entry.cancel_owned", "order.cancel_exact"}:
                result = await self._cancel(args, UUID(str(job["id"])), reconcile)
            elif action == "management.apply":
                result = await self._management(job, oid, reconcile)
            elif action.startswith("position."):
                root = self._intents.get(args["client_oid"])
                if reconcile:
                    result = await self._close_readback(oid)
                else:
                    cancelled = await self._cancel(
                        {"symbol": root.symbol}, UUID(str(job["id"])), False
                    )
                    if not cancelled["confirmed"]:
                        return {"state": "reconciliation_pending", "result": cancelled}
                    position = await self._position(root, scope)
                    quantity = (
                        _decimal(args["quantity"])
                        if action.endswith("partial")
                        else position["quantity"]
                    )
                    result = await self._close(root, position, quantity, oid)
            elif action.endswith("_fallback"):
                result = await self._fallback(job, reconcile)
            else:
                root = self._intents.get(args["client_oid"])
                if reconcile or self._intents.get(oid) is not None:
                    result = await self._protection_readback(oid)
                else:
                    position = await self._position(root, scope)
                    price = (
                        position["entry"]
                        if action == "protection.sl_to_entry"
                        else args.get("price")
                    )
                    result = await self._protect(root, position, action, price, oid)
            return {
                "state": "succeeded" if result.get("confirmed") else "reconciliation_pending",
                "result": result,
            }
        except Exception as exc:
            # After the worker effect claim, exceptions are ambiguous, not permission to retry.
            return {"state": "reconciliation_pending", "result": {"code": type(exc).__name__}}

    async def _open(self, job: dict[str, Any], reconcile: bool) -> dict[str, Any]:
        import psycopg

        from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
        from fatty_trader.execution.bitget_dispatch_repository import (
            PostgresBitgetDispatchRepository,
        )
        from fatty_trader.execution.bitget_dispatcher import BitgetDispatcher, DispatchGate
        from fatty_trader.service import _validate_bitget_cutover, build_bitget_protection_admission

        runtime = self._graph()
        args = job["arguments"]
        identifier = UUID(str(job["id"]))
        dispatch = self._dispatch(identifier, args, _decimal(args["entry_price"]))
        entry_oid = BitgetDispatchExecution.client_oid(dispatch)
        if reconcile or self._intents.get(entry_oid) is not None:
            await runtime.execution.recover_entry_lifecycles()
            intent = self._intents.get(entry_oid)
            return {
                "client_oid": entry_oid,
                "confirmed": bool(
                    intent
                    and intent.state in {"filled", "accepted", "partial", "cancelled", "reconciled"}
                    and runtime.execution.recovery_ready
                ),
                "intent_state": intent.state if intent else None,
            }
        _validate_bitget_cutover(self.environ)
        with self.store.connection() as connection, connection.cursor() as cur:
            cur.execute(
                """INSERT INTO console_manual_entries
                (id,job_id,account_id,environment,symbol,direction,entry_price,stop_loss,
                 take_profits,order_type,expires_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,
                       clock_timestamp()+interval '60 seconds')
                ON CONFLICT(id) DO NOTHING""",
                (
                    identifier,
                    job["id"],
                    job["scope"]["account_id"],
                    "LIVE",
                    dispatch.pair_token,
                    dispatch.direction,
                    dispatch.entry_price,
                    dispatch.stop_loss,
                    json.dumps([str(p) for p in dispatch.take_profits]),
                    dispatch.order_type,
                ),
            )
            cur.execute(
                """INSERT INTO dispatches
                (id,source_type,source_id,revision,exchange,state,claimed_by,lease_until,attempts)
                VALUES(%s,'console_manual',%s,%s,'bitget','QUEUED',%s,
                clock_timestamp()+interval '300 seconds',1) ON CONFLICT(id) DO NOTHING""",
                (identifier, identifier, job["preview_digest"], dispatch.claimed_by),
            )
        repo = PostgresBitgetDispatchRepository(
            psycopg.connect, console_enabled=True, console_scope=self._scope(job["scope"])
        )

        class SelectedRepository:
            def claim(self, worker_id: str, lease_seconds: int) -> BitgetDispatch:
                return dispatch

            def __getattr__(self, name: str) -> Any:
                return getattr(repo, name)

        await runtime.execution.recover_entry_lifecycles()
        dispatcher = BitgetDispatcher(
            SelectedRepository(),
            gate=DispatchGate(
                execution_enabled=True,
                canary_max_orders=int(self.environ["BITGET_CANARY_MAX_ORDERS"]),
                canary_symbol=self.environ.get("BITGET_CANARY_SYMBOL") or None,
            ),
            execution=runtime.execution,
            preflight=runtime.preflight,
            kill_switch=runtime.execution._kill_switch,
            protection_admission=build_bitget_protection_admission(self.environ),
        )
        outcome = await dispatcher.run_once(dispatch.claimed_by, 300)
        intent = self._intents.get(entry_oid)
        protected = True
        if intent is not None and intent.filled_qty > 0:
            proof = await runtime.execution._read_recovery_protection(intent, dispatch)
            from fatty_trader.execution.protection import ProtectionState

            protected = proof.state == ProtectionState.VENUE_PROTECTED
            rows = await runtime.client.get_single_position(intent.symbol)
            if isinstance(rows, list):
                matches = [
                    p
                    for p in rows
                    if p.get("holdSide") == ("long" if intent.side == "BUY" else "short")
                    and _decimal_or_zero(p.get("total")) == intent.filled_qty
                ]
                if len(matches) == 1 and str(matches[0].get("cTime", "")).isdigit():
                    self.store.execute(
                        """INSERT INTO console_position_epochs
                        (root_client_order_id,account_id,environment,
                         provider_position_epoch,observed_at)
                        VALUES(%s,%s,%s,%s,clock_timestamp()) ON CONFLICT DO NOTHING""",
                        (
                            intent.client_oid,
                            job["scope"]["account_id"],
                            "LIVE",
                            str(matches[0]["cTime"]),
                        ),
                    )
        return {
            "client_oid": entry_oid,
            "outcome": outcome,
            "confirmed": bool(
                intent
                and protected
                and outcome in {"filled", "acknowledged", "partial", "reconciled"}
                and intent.state not in {"unknown", "rejected"}
            ),
        }

    async def _cancel(
        self, args: dict[str, Any], management_id: UUID, reconcile: bool
    ) -> dict[str, Any]:
        runtime = self._graph()
        if "symbol" in args:
            confirmed = await runtime.execution.cancel_pending_entries(
                args["symbol"], management_id, reconciliation_only=reconcile
            )
        else:
            if self._active_job is None:
                raise ValueError("cancel requires durable job ownership")
            self._owned_limit(args["client_oid"], self._active_job["scope"])
            intent = self._intents.get(args["client_oid"])
            if (
                not intent
                or intent.role != "ENTRY"
                or intent.order_type != "limit"
                or not intent.dispatch_id
            ):
                raise ValueError("cancellation target is not an exact owned LIMIT ENTRY")
            if not reconcile:
                self._intents.claim_cancel(intent.client_oid, management_id)
            current = self._intents.get(intent.client_oid)
            result = (
                await runtime.execution._execution.reconcile_intent(current)
                if reconcile
                else await runtime.execution._execution.cancel_pending_entry(
                    current, lambda: runtime.execution._claim_cancel_post(current.client_oid)
                )
            )
            runtime.execution._persist_readback(current, result)
            confirmed = result.provider_terminal
        return {"confirmed": confirmed, "kind": "owned-entry-cancellation"}

    async def _position(self, root: Any, scope: dict[str, Any]) -> dict[str, Any]:
        if root is None:
            raise ValueError("owned entry is absent")
        self._owned(root.client_oid, scope)
        rows = await self._graph().client.get_all_positions()
        if not isinstance(rows, list):
            raise ValueError("position read is malformed")
        matches = [
            p
            for p in rows
            if p.get("symbol") == root.symbol and _decimal_or_zero(p.get("total")) > 0
        ]
        if len(matches) != 1:
            raise ValueError("position does not resolve to one exact owned side")
        row = matches[0]
        quantity = _decimal(row["total"])
        if row.get("holdSide") != (
            "long" if root.side == "BUY" else "short"
        ) or quantity != self._intents.remaining_owned_quantity(root.client_oid):
            raise ValueError("provider quantity does not match exact owned residual")
        if row.get("marginMode") != "isolated" or row.get("posMode") != "one_way_mode":
            raise ValueError("position mode is not canonical isolated one-way")
        from fatty_trader.console.reads import native_position_owner

        root_record = dict(self._owned(root.client_oid, scope))
        root_record["remaining_owned_quantity"] = quantity
        plans = await self._graph().client.get_pending_plan_orders(root.symbol)
        native = native_position_owner(row, plans, [root_record], scope["environment"])
        fallback = self.store.one(
            """SELECT id FROM fallback_protection WHERE position_key=%s
            AND provider_position_epoch=%s AND environment=%s AND state IN ('active','closing')""",
            (root.client_oid, str(row.get("cTime")), scope["environment"]),
        )
        epoch = self.store.one(
            """SELECT provider_position_epoch FROM console_position_epochs
            WHERE root_client_order_id=%s AND account_id=%s AND environment=%s""",
            (root.client_oid, scope["account_id"], scope["environment"]),
        )
        if (
            native is None
            and fallback is None
            and (epoch is None or epoch["provider_position_epoch"] != str(row.get("cTime")))
        ):
            raise ValueError(
                "position epoch is not proved by exact native plans or immutable owned epoch"
            )
        if native is not None or fallback is not None:
            self.store.execute(
                """INSERT INTO console_position_epochs
                (root_client_order_id,account_id,environment,provider_position_epoch,observed_at)
                VALUES(%s,%s,%s,%s,clock_timestamp()) ON CONFLICT DO NOTHING""",
                (root.client_oid, scope["account_id"], scope["environment"], str(row["cTime"])),
            )
        return {"quantity": quantity, "entry": _decimal(row["openPriceAvg"]), "row": row}

    async def _close(
        self, root: Any, position: dict[str, Any], quantity: Decimal, oid: str
    ) -> dict[str, Any]:
        from fatty_trader.exchanges.bitget.metadata import find_contract, metadata_from_contract

        client = self._graph().client
        meta = metadata_from_contract(find_contract(await client.get_contracts(), root.symbol))
        if (
            quantity > position["quantity"]
            or quantity % meta.size_step
            or quantity < meta.min_order_qty
            or (meta.max_market_order_qty and quantity > meta.max_market_order_qty)
        ):
            raise ValueError("reduce-only quantity violates selected residual or venue lots")
        if self._intents.get(oid) is not None:
            return await self._close_readback(oid)
        intent = LiveIntentRecord(
            "bitget",
            oid,
            root.symbol,
            "SELL" if root.side == "BUY" else "BUY",
            "CLOSE",
            "requested",
            quantity,
        )
        if not self._claim_intent(intent, close_remaining=position["quantity"]):
            return await self._close_readback(oid)
        ack = await client.place_market_close(
            symbol=root.symbol, side=intent.side, quantity=str(quantity), client_oid=oid
        )
        intent.provider_order_id = (
            str(ack["orderId"]) if isinstance(ack, dict) and ack.get("orderId") else None
        )
        intent.state = "submitted" if intent.provider_order_id else "unknown"
        self._intents.update(intent)
        return await self._close_readback(oid)

    async def _close_readback(self, oid: str) -> dict[str, Any]:
        runtime = self._graph()
        intent = self._intents.get(oid)
        if not intent:
            return {"confirmed": False, "client_oid": oid}
        result = await runtime.execution._execution.reconcile_intent(intent)
        runtime.execution._persist_readback(intent, result)
        await self._intents.verified_close_lifecycle.reconcile(runtime.client)
        # Real authenticated close fills prove execution, not flat positions or derived fills.
        real = result.provider_fills
        confirmed = bool(
            real
            and result.filled_qty == intent.requested_qty
            and all(
                str(f.get("tradeSide", "")).lower() == "close"
                and str(f.get("fillId", f.get("tradeId", "")))
                and not str(f.get("fillId", f.get("tradeId", ""))).startswith("status-derived:")
                for f in real
            )
        )
        binding = self.store.one(
            """SELECT r.client_order_id,r.state AS reservation_state
            FROM bitget_verified_close_bindings b
            JOIN bitget_margin_reservations r ON r.id=b.reservation_id
            WHERE b.close_client_order_id=%s""",
            (oid,),
        )
        if confirmed and binding:
            remaining = self._intents.remaining_owned_quantity(binding["client_order_id"])
            positions = await runtime.client.get_all_positions()
            quantities = [
                _decimal_or_zero(p["total"]) for p in positions if p["symbol"] == intent.symbol
            ]
            confirmed = sum(quantities, Decimal(0)) == remaining
            if confirmed and remaining == 0:
                confirmed = binding["reservation_state"] == "released"
        else:
            confirmed = False
        return {"confirmed": confirmed, "client_oid": oid, "quantity": str(result.filled_qty)}

    async def _protect(
        self, root: Any, position: dict[str, Any], action: str, price: Any, oid: str
    ) -> dict[str, Any]:
        from fatty_trader.exchanges.bitget.metadata import find_contract, metadata_from_contract
        from fatty_trader.risk.sizing import round_price_to_tick

        client = self._graph().client
        meta = metadata_from_contract(find_contract(await client.get_contracts(), root.symbol))
        stop = root.active_stop_loss_price or root.planned_stop_loss
        target = root.active_take_profit_price or root.planned_take_profits[0]
        if action in {"protection.set_sl", "protection.sl_to_entry"}:
            stop = round_price_to_tick(_decimal(price), meta.price_tick)
        elif action == "protection.set_tp":
            target = round_price_to_tick(_decimal(price), meta.price_tick)
        ticker = await client.get_ticker(root.symbol)
        mark = _decimal(ticker["lastPr"])
        sign = Decimal(1) if root.side == "BUY" else Decimal(-1)
        if (mark - stop) * sign < meta.price_tick or (target - mark) * sign < meta.price_tick:
            raise ValueError("native protection must clear the current mark by one tick")
        row = position["row"]
        sl_id, tp_id = row.get("stopLossId"), row.get("takeProfitId")
        plans = await client.get_pending_plan_orders(root.symbol)
        if not isinstance(plans, list):
            raise ValueError("native plans unavailable")
        from fatty_trader.exchanges.bitget.reconciliation_live import (
            _plan_client_oid,
            _plan_hold_side,
            _plan_order_id,
            _plan_type,
        )

        identities: dict[str, str | None] = {}
        for supplied, kind, leg, suffix in (
            (sl_id, "pos_loss", "stopLoss", "-sl"),
            (tp_id, "pos_profit", "stopSurplus", "-tp"),
        ):
            matching = [
                p
                for p in plans
                if str(p.get("symbol", "")).upper() == root.symbol
                and _plan_type(p) == kind
                and _plan_client_oid(p, leg) == root.client_oid + suffix
                and _plan_hold_side(p) == ("buy" if root.side == "BUY" else "sell")
            ]
            if len(matching) > 1 or (
                supplied and (len(matching) != 1 or _plan_order_id(matching[0]) != str(supplied))
            ):
                raise ValueError("native opposite plan has no exact root ownership")
            identities[kind] = _plan_order_id(matching[0]) if matching else None
        sl_id, tp_id = identities["pos_loss"], identities["pos_profit"]
        repair = action == "protection.repair_native"
        if repair and sl_id and tp_id:
            # Reconciliation repair does not replace healthy retained plans.
            intent = LiveIntentRecord(
                "bitget",
                oid,
                root.symbol,
                "SELL" if root.side == "BUY" else "BUY",
                "SL",
                "requested",
                position["quantity"],
                provider_order_id=str(sl_id),
                planned_stop_loss=stop,
                planned_take_profits=(target,),
                stop_mutation_root_oid=root.client_oid,
                stop_mutation_take_profit_id=str(tp_id),
            )
            self._claim_intent(intent)
            return await self._protection_readback(oid)
        if not repair and (not sl_id or not tp_id):
            raise ValueError("selected modification requires both exact native plans; use repair")
        intent = LiveIntentRecord(
            "bitget",
            oid,
            root.symbol,
            "SELL" if root.side == "BUY" else "BUY",
            "TP" if action == "protection.set_tp" else "SL",
            "requested",
            position["quantity"],
            provider_order_id=str(tp_id if action == "protection.set_tp" else sl_id)
            if (tp_id if action == "protection.set_tp" else sl_id)
            else None,
            planned_stop_loss=stop,
            planned_take_profits=(target,),
            stop_mutation_root_oid=root.client_oid,
            stop_mutation_take_profit_id=str(sl_id if action == "protection.set_tp" else tp_id)
            if (sl_id if action == "protection.set_tp" else tp_id)
            else None,
        )
        if not self._claim_intent(intent):
            return await self._protection_readback(oid)
        if repair:
            await client.place_position_tpsl(
                symbol=root.symbol,
                hold_side=row["holdSide"],
                quantity=str(position["quantity"]),
                stop_loss=str(stop) if not sl_id else None,
                take_profit=str(target) if not tp_id else None,
                stop_loss_client_oid=root.client_oid + "-sl" if not sl_id else None,
                take_profit_client_oid=root.client_oid + "-tp" if not tp_id else None,
            )
        else:
            await client.modify_position_stop_loss(
                symbol=root.symbol,
                order_id=str(tp_id if action == "protection.set_tp" else sl_id),
                client_oid=root.client_oid + ("-tp" if action == "protection.set_tp" else "-sl"),
                trigger_price=str(target if action == "protection.set_tp" else stop),
            )
        intent.state = "acknowledged"
        self._intents.update(intent)
        return await self._protection_readback(oid)

    async def _protection_readback(self, oid: str) -> dict[str, Any]:
        from fatty_trader.exchanges.bitget.reconciliation_live import (
            NativeProtectionExpectation,
            confirm_native_protection,
        )
        from fatty_trader.execution.protection import ProtectionState

        intent = self._intents.get(oid)
        if not intent:
            return {"confirmed": False, "client_oid": oid}
        root_oid = intent.stop_mutation_root_oid or intent.protection_root_oid
        root = self._intents.get(root_oid) if root_oid else None
        if root is None or self._active_job is None:
            raise ValueError("protection readback requires exact owned entry root")
        self._owned(root.client_oid, self._active_job["scope"])
        client = self._graph().client
        expectation = NativeProtectionExpectation(
            symbol=intent.symbol,
            hold_side="long" if root.side == "BUY" else "short",
            quantity=intent.requested_qty,
            stop_loss=intent.planned_stop_loss,
            take_profit=intent.planned_take_profits[0],
            stop_loss_client_oid=root.client_oid + "-sl",
            take_profit_client_oid=root.client_oid + "-tp",
            stop_loss_provider_order_id=(intent.provider_order_id if intent.role == "SL" else None)
            if intent.protection_root_oid
            else (
                intent.stop_mutation_take_profit_id
                if intent.role == "TP"
                else intent.provider_order_id
            ),
            take_profit_provider_order_id=(
                intent.provider_order_id if intent.role == "TP" else None
            )
            if intent.protection_root_oid
            else (
                intent.provider_order_id
                if intent.role == "TP"
                else intent.stop_mutation_take_profit_id
            ),
        )
        report = await confirm_native_protection(
            lambda: client.get_single_position(intent.symbol),
            lambda: client.get_pending_plan_orders(intent.symbol),
            expectation=expectation,
        )
        confirmed = report.state == ProtectionState.VENUE_PROTECTED
        if confirmed:
            intent.state = "reconciled"
            self._intents.update(intent)
            self.store.execute(
                """UPDATE live_order_intents SET active_stop_loss_price=%s,
                active_take_profit_price=%s WHERE exchange='bitget' AND role='ENTRY'
                AND margin_reservation_id=%s""",
                (
                    intent.planned_stop_loss,
                    intent.planned_take_profits[0],
                    root.margin_reservation_id,
                ),
            )
        return {"confirmed": confirmed, "client_oid": oid, "protection": str(report.state)}

    async def _management(self, job: dict[str, Any], oid: str, reconcile: bool) -> dict[str, Any]:
        args, runtime = job["arguments"], self._graph()
        action = args["action"]
        if action in {"CLOSE", "TP1_BOOKED", "TP_BOOKED"}:
            cancellation = await self._cancel(
                {"symbol": args["symbol"]}, UUID(str(job["id"])), reconcile
            )
            if not cancellation["confirmed"]:
                return cancellation
        positions = await runtime.client.get_all_positions()
        if not isinstance(positions, list) or not all(isinstance(p, dict) for p in positions):
            raise ValueError("fresh management position read is unavailable")
        flat = not any(
            p.get("symbol") == args["symbol"] and _decimal_or_zero(p["total"]) > 0
            for p in positions
        )
        if flat and action in {"CLOSE", "TP1_BOOKED", "TP_BOOKED"}:
            if self._intents.get(oid + "-close"):
                return await self._close_readback(oid + "-close")
            return {"confirmed": True, "outcome": "cancelled-flat"}
        owners = self.store.query(
            """SELECT i.client_order_id FROM live_order_intents i
            JOIN bitget_margin_reservations r ON r.id=i.margin_reservation_id
            AND r.client_order_id=i.client_order_id
            JOIN dispatches d ON d.id=i.dispatch_id AND d.id=r.dispatch_id
            WHERE i.exchange='bitget' AND i.symbol=%s AND i.role='ENTRY'
            AND i.entry_leg IS DISTINCT FROM 'limit'
            AND r.environment=%s AND r.state='consumed'
            AND NOT EXISTS(SELECT 1 FROM bitget_baseline_records b
                WHERE (b.record_kind='intent' AND b.record_id=i.id)
                   OR (b.record_kind='reservation' AND b.record_id=r.id)
                   OR (b.record_kind='dispatch' AND b.record_id=d.id))""",
            (args["symbol"], job["scope"]["environment"]),
        )
        if len(owners) != 1:
            return {"confirmed": False, "outcome": "ownership-unproved"}
        root = self._intents.get(owners[0]["client_order_id"])
        self._owned(root.client_oid, job["scope"])
        if action == "TP_BOOKED":
            return {"confirmed": True, "outcome": "entries-cancelled"}
        existing_close = self._intents.get(oid + "-close")
        if existing_close:
            close = await self._close_readback(oid + "-close")
            if not close["confirmed"] or action == "CLOSE":
                return close
        if action == "CLOSE":
            if reconcile:
                return {"confirmed": False}
            position = await self._position(root, job["scope"])
            return await self._close(root, position, position["quantity"], oid + "-close")
        if action == "TP1_BOOKED" and not existing_close:
            if reconcile:
                return {"confirmed": False}
            from fatty_trader.exchanges.bitget.metadata import find_contract, metadata_from_contract
            from fatty_trader.risk.sizing import round_qty_to_step

            position = await self._position(root, job["scope"])
            meta = metadata_from_contract(
                find_contract(await runtime.client.get_contracts(), root.symbol)
            )
            half = round_qty_to_step(position["quantity"] / 2, meta.size_step)
            if half <= 0 or position["quantity"] - half < meta.size_step:
                raise ValueError("TP1 cannot retain a lot-safe half residual")
            closed = await self._close(root, position, half, oid + "-close")
            if not closed["confirmed"]:
                return closed
        if self._intents.get(oid + "-sl") or reconcile:
            return await self._protection_readback(oid + "-sl")
        position = await self._position(root, job["scope"])
        return await self._protect(
            root, position, "protection.sl_to_entry", position["entry"], oid + "-sl"
        )

    async def _observe_protection(self, root: Any, position: dict[str, Any]) -> dict[str, Any]:
        from fatty_trader.exchanges.bitget.reconciliation_live import (
            NativeProtectionExpectation,
            confirm_native_protection,
        )
        from fatty_trader.execution.protection import ProtectionState

        client = self._graph().client
        expectation = NativeProtectionExpectation(
            symbol=root.symbol,
            hold_side=position["row"]["holdSide"],
            quantity=position["quantity"],
            stop_loss=root.active_stop_loss_price or root.planned_stop_loss,
            take_profit=root.active_take_profit_price or root.planned_take_profits[0],
            stop_loss_client_oid=root.client_oid + "-sl",
            take_profit_client_oid=root.client_oid + "-tp",
            stop_loss_provider_order_id=position["row"].get("stopLossId"),
            take_profit_provider_order_id=position["row"].get("takeProfitId"),
        )
        proof = await confirm_native_protection(
            lambda: client.get_single_position(root.symbol),
            lambda: client.get_pending_plan_orders(root.symbol),
            expectation=expectation,
        )
        return {
            "confirmed": proof.state == ProtectionState.VENUE_PROTECTED,
            "GET_only": True,
            "protection": str(proof.state),
        }

    async def _fallback(self, job: dict[str, Any], reconcile: bool) -> dict[str, Any]:
        from fatty_trader.execution.bitget_fallback_protection import register_fallback

        root = self._intents.get(job["arguments"]["client_oid"])
        position = await self._position(root, job["scope"])
        epoch = job["arguments"]["epoch"]
        if str(position["row"].get("cTime")) != epoch:
            raise ValueError("fallback provider epoch changed")
        key = root.client_oid
        if job["action"] == "protection.register_fallback" and not reconcile:
            with self.store.connection() as connection:
                self.store.effect_authority(self.store.cursor(connection), job)
                register_fallback(
                    "bitget",
                    root.symbol,
                    "LONG" if root.side == "BUY" else "SHORT",
                    position["entry"],
                    root.active_stop_loss_price or root.planned_stop_loss,
                    [root.active_take_profit_price or root.planned_take_profits[0]],
                    position["quantity"],
                    position_key=key,
                    provider_position_epoch=epoch,
                    environment="LIVE",
                    connection=connection,
                )
        elif job["action"] == "protection.disable_fallback" and not reconcile:
            # Never remove the only protection: strict native readback must be healthy first.
            from fatty_trader.exchanges.bitget.reconciliation_live import (
                NativeProtectionExpectation,
                confirm_native_protection,
            )
            from fatty_trader.execution.protection import ProtectionState

            client = self._graph().client
            expectation = NativeProtectionExpectation(
                symbol=root.symbol,
                hold_side=position["row"]["holdSide"],
                quantity=position["quantity"],
                stop_loss=root.active_stop_loss_price or root.planned_stop_loss,
                take_profit=root.active_take_profit_price or root.planned_take_profits[0],
                stop_loss_client_oid=root.client_oid + "-sl",
                take_profit_client_oid=root.client_oid + "-tp",
            )
            proof = await confirm_native_protection(
                lambda: client.get_single_position(root.symbol),
                lambda: client.get_pending_plan_orders(root.symbol),
                expectation=expectation,
            )
            if proof.state != ProtectionState.VENUE_PROTECTED:
                raise ValueError("fallback disable requires proved native protection")
            with self.store.connection() as connection:
                cursor = self.store.cursor(connection)
                self.store.effect_authority(cursor, job)
                cursor.execute(
                    """UPDATE fallback_protection
                    SET state='cancelled',close_reason='console-native-confirmed',
                    updated_at=clock_timestamp()
                    WHERE position_key=%s AND provider_position_epoch=%s
                    AND environment='LIVE' AND state='active'""",
                    (key, epoch),
                )
        elif job["action"] == "protection.update_fallback" and not reconcile:
            from fatty_trader.exchanges.bitget.metadata import find_contract, metadata_from_contract
            from fatty_trader.risk.sizing import round_price_to_tick

            client = self._graph().client
            meta = metadata_from_contract(find_contract(await client.get_contracts(), root.symbol))
            stop = round_price_to_tick(_decimal(job["arguments"]["stop_loss"]), meta.price_tick)
            target = round_price_to_tick(_decimal(job["arguments"]["take_profit"]), meta.price_tick)
            mark = _decimal((await client.get_ticker(root.symbol))["lastPr"])
            sign = Decimal(1) if root.side == "BUY" else Decimal(-1)
            if (mark - stop) * sign < meta.price_tick or (target - mark) * sign < meta.price_tick:
                raise ValueError("fallback levels must clear current mark by one tick")
            with self.store.connection() as connection:
                cursor = self.store.cursor(connection)
                self.store.effect_authority(cursor, job)
                cursor.execute(
                    """UPDATE fallback_protection SET stop_loss=%s,take_profits=%s::jsonb,
                    updated_at=clock_timestamp()
                    WHERE position_key=%s AND provider_position_epoch=%s
                    AND environment='LIVE' AND state='active'""",
                    (stop, json.dumps([str(target)]), key, epoch),
                )
        row = self.store.one(
            """SELECT state,stop_loss,take_profits FROM fallback_protection WHERE position_key=%s
            AND provider_position_epoch=%s AND environment='LIVE'""",
            (key, epoch),
        )
        confirmed = bool(
            row
            and row["state"]
            == ("cancelled" if job["action"] == "protection.disable_fallback" else "active")
        )
        if confirmed and job["action"] == "protection.update_fallback":
            confirmed = _decimal(row["stop_loss"]) == _decimal(job["arguments"]["stop_loss"])
            targets = row["take_profits"]
            if isinstance(targets, str):
                targets = json.loads(targets)
            confirmed = (
                confirmed
                and len(targets) == 1
                and _decimal(targets[0]) == _decimal(job["arguments"]["take_profit"])
            )
        return {"confirmed": confirmed, "epoch": epoch}


def _decimal_or_zero(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("provider quantity is invalid")
    return result


class _SnapshotClient:
    def __init__(self, store: Any, scope: dict[str, Any]) -> None:
        self.store, self.scope = store, scope
        self.times: list[datetime] = []
        identity = self.snapshot("account_identity")
        if identity["provider_account_id"] != scope["account_id"]:
            raise ValueError("provider account observation does not match selected scope")

    def snapshot(self, kind: str, symbol: str | None = None) -> Any:
        observation = self.store.latest_snapshot(f"{kind}:{symbol}" if symbol else kind, self.scope)
        if not observation or observation.get("error"):
            raise ValueError(f"fresh provider {kind} observation unavailable")
        timestamp = observation["observed_at"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if timestamp.tzinfo is None or not timedelta(seconds=-1) <= datetime.now(
            UTC
        ) - timestamp <= timedelta(seconds=5):
            raise ValueError(f"provider {kind} observation stale")
        self.times.append(timestamp)
        data = observation["data"]
        if not data or not data.get("complete"):
            raise ValueError(f"provider {kind} observation is incomplete")
        return data.get("items", data.get("values"))

    async def get_account(self, symbol: str) -> Any:
        return self.snapshot("account_admission", symbol)

    async def get_single_position(self, symbol: str) -> list[dict[str, Any]]:
        return [p for p in self.snapshot("positions") if p["symbol"] == symbol]

    async def get_all_positions(self) -> Any:
        return self.snapshot("positions")

    async def get_contracts(self) -> Any:
        return self.snapshot("contracts")

    async def get_position_lever(self, symbol: str) -> Any:
        return self.snapshot("position_lever", symbol)

    async def get_ticker(self, symbol: str) -> Any:
        return self.snapshot("ticker", symbol)

    async def get_clock_skew_ms(self) -> int:
        return int(self.snapshot("clock")["local_minus_server_ms"])


class _SnapshotVenue:
    def __init__(self, store: Any, scope: dict[str, Any]) -> None:
        from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetClient, AsyncBitgetVenue

        self.client = _SnapshotClient(store, scope)
        self.venue = AsyncBitgetVenue(cast(AsyncBitgetClient, self.client))

    async def preflight(self, symbol: str, *, read_only: bool = True) -> Any:
        snapshot = await self.venue.preflight(symbol, read_only=True)
        return replace(
            snapshot, account=replace(snapshot.account, observed_at=min(self.client.times))
        )

    async def active_position_snapshot(self) -> Any:
        snapshot = await self.venue.active_position_snapshot()
        return replace(snapshot, observed_at=min(self.client.times))
