"""Additive operational epoch receipts. No close ownership is asserted here."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4


@dataclass(frozen=True, repr=False)
class BaselineContext:
    account_identity: str
    api_key_sha256: str

    def __post_init__(self) -> None:
        if not self.account_identity.strip() or not re.fullmatch(
            r"[0-9a-f]{64}", self.api_key_sha256
        ):
            raise ValueError("invalid-authenticated-account-context")


@dataclass(frozen=True, repr=False)
class FlatAccountProof:
    context: BaselineContext
    environment: str
    authenticated: bool
    observed_at: datetime
    positions: int
    ordinary_orders: int
    conditional_orders: int
    conditional_inventory_complete: bool
    clock_safe: bool


@dataclass(frozen=True, repr=False)
class MonitorReadinessProof:
    context: BaselineContext
    process_generation: str
    observed_at: datetime
    private_pong_at: datetime
    clean_cycle_at: datetime
    login_authenticated: bool
    private_subscriptions_ready: bool
    connected: bool


@dataclass(frozen=True)
class IncidentLatch:
    scope: str
    reason: str
    latched_at: datetime | None
    updated_at: datetime


def _fresh(observed: datetime, now: datetime) -> bool:
    return (
        isinstance(observed, datetime)
        and observed.tzinfo is not None
        and observed.utcoffset() is not None
        and -timedelta(seconds=1) <= now - observed <= timedelta(seconds=30)
    )


def set_baseline_context(cursor: Any, context: BaselineContext | None) -> None:
    cursor.execute(
        "SELECT set_config('fatty.baseline_uid',%s,true), set_config('fatty.baseline_key',%s,true)",
        (context.account_identity if context else "", context.api_key_sha256 if context else ""),
    )


def _queued_work(cursor: Any) -> list[tuple[str, UUID, str]]:
    cursor.execute(
        "SELECT d.id,to_jsonb(d)::text, EXISTS(SELECT 1 FROM bitget_margin_reservations m "
        "WHERE m.dispatch_id=d.id) FROM dispatches d "
        "WHERE d.exchange='bitget' AND d.state='QUEUED' ORDER BY d.id FOR UPDATE OF d"
    )
    queued = []
    for row_id, snapshot_json, reservation in cursor.fetchall():
        snapshot = json.loads(snapshot_json, parse_float=Decimal)
        if (
            snapshot["attempts"] != 0
            or snapshot["claimed_by"] is not None
            or snapshot["lease_until"] is not None
            or reservation
        ):
            raise ValueError("ambiguous-prebaseline-entry-queue")
        queued.append(("dispatch", row_id, snapshot_json))
    cursor.execute(
        "SELECT u.id,to_jsonb(u)::text, EXISTS(SELECT 1 FROM "
        "source_management_provider_intents p WHERE p.management_update_id=u.id) "
        "FROM source_management_updates u WHERE u.state IN ('queued','claimed') "
        "ORDER BY u.id FOR UPDATE OF u"
    )
    for row_id, snapshot, marker in cursor.fetchall():
        # A durable pre-POST marker cannot be declared unsent merely because its
        # provider acknowledgement/durable close intent never survived.
        if marker:
            raise ValueError("ambiguous-prebaseline-management-queue")
        queued.append(("management", row_id, snapshot))
    return queued


class PostgresOperationalBaselineRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def activate(
        self,
        *,
        baseline_id: UUID,
        context: BaselineContext,
        collect_proof: Callable[[], FlatAccountProof],
        monitor_ready: Callable[[Any, BaselineContext], MonitorReadinessProof],
        expected_incidents: tuple[IncidentLatch, ...],
        tests_reference: str,
        review_reference: str,
    ) -> UUID:
        if (
            not tests_reference.strip()
            or not review_reference.strip()
            or len(expected_incidents) != 2
            or {i.scope for i in expected_incidents} != {"bitget", "bitget-protection-stream"}
            or any(
                i.reason
                != {
                    "bitget": "clock-skew-exceeded",
                    "bitget-protection-stream": "socket-not-connected",
                }[i.scope]
                for i in expected_incidents
            )
        ):
            raise ValueError("unapproved-baseline-activation")
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('bitget'))")
            # Fence insertions and state changes too, not merely the captured rows.
            cursor.execute(
                "LOCK TABLE live_order_intents, bitget_margin_reservations, "
                "dispatches, venue_kill_switches, source_management_updates, "
                "source_management_provider_intents, notifications_outbox, dispatch_transitions, "
                "bitget_operational_baseline_activations, "
                "bitget_operational_baseline_queue_retirements, "
                "bitget_operational_baseline_incident_releases IN SHARE ROW EXCLUSIVE MODE"
            )
            cursor.execute(
                "SELECT scope,active,reason,latched_at,updated_at "
                "FROM venue_kill_switches WHERE scope IN "
                "('global','bitget','bitget-protection-stream') ORDER BY scope FOR UPDATE"
            )
            switches = {r[0]: r for r in cursor.fetchall()}
            if "global" not in switches or switches["global"][1] is not False:
                raise ValueError("global-kill-not-explicitly-inactive")
            for incident in expected_incidents:
                if switches.get(incident.scope) != (
                    incident.scope,
                    True,
                    incident.reason,
                    incident.latched_at,
                    incident.updated_at,
                ):
                    raise ValueError("incident-latch-changed")
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM live_order_intents "
                "WHERE exchange='bitget' AND state NOT IN "
                "('filled','rejected','cancelled','reconciled'))"
            )
            if cursor.fetchone()[0]:
                raise ValueError("pending-or-unknown-intents")
            cursor.execute(
                "SELECT account_identity,api_key_sha256,captured_rows FROM "
                "bitget_operational_baselines WHERE id=%s FOR UPDATE",
                (baseline_id,),
            )
            receipt = cursor.fetchone()
            if receipt is None or receipt[:2] != (context.account_identity, context.api_key_sha256):
                raise ValueError("baseline-account-context-mismatch")
            # Compare JSONB in PostgreSQL: decoding NUMERIC through binary floats
            # would erase financial precision and can miss a changed fingerprint.
            for kind, table in (
                ("intent", "live_order_intents"),
                ("reservation", "bitget_margin_reservations"),
                ("dispatch", "dispatches"),
            ):
                cursor.execute(
                    f"SELECT EXISTS(SELECT 1 FROM bitget_operational_baseline_rows r "
                    f"LEFT JOIN {table} t ON t.id=r.row_id WHERE r.baseline_id=%s "
                    "AND r.kind=%s AND ((to_jsonb(t) - 'updated_at') "
                    "IS DISTINCT FROM r.snapshot))",
                    (baseline_id, kind),
                )
                if cursor.fetchone()[0]:
                    raise ValueError("baseline-captured-rows-changed")
            queued = _queued_work(cursor)
            proof = collect_proof()
            readiness = monitor_ready(cursor, context)
            cursor.execute("SELECT clock_timestamp()")
            now = cursor.fetchone()[0]
            if (
                not isinstance(proof, FlatAccountProof)
                or proof.context != context
                or proof.environment != "LIVE"
                or proof.authenticated is not True
                or proof.conditional_inventory_complete is not True
                or proof.clock_safe is not True
                or any(
                    type(n) is not int or n != 0
                    for n in (proof.positions, proof.ordinary_orders, proof.conditional_orders)
                )
                or not _fresh(proof.observed_at, now)
            ):
                raise ValueError("invalid-or-stale-provider-proof")
            if (
                not isinstance(readiness, MonitorReadinessProof)
                or readiness.context != context
                or not readiness.process_generation.strip()
                or readiness.login_authenticated is not True
                or readiness.private_subscriptions_ready is not True
                or readiness.connected is not True
                or not all(
                    _fresh(t, now)
                    for t in (
                        readiness.observed_at,
                        readiness.private_pong_at,
                        readiness.clean_cycle_at,
                    )
                )
            ):
                raise ValueError("invalid-or-stale-monitor-readiness")
            epoch = uuid4()
            cursor.execute(
                "INSERT INTO bitget_operational_baseline_activations "
                "(baseline_id,epoch_id,activated_at,source_cutoff,proof) "
                "VALUES (%s,%s,%s,%s,%s::jsonb)",
                (
                    baseline_id,
                    epoch,
                    now,
                    now,
                    json.dumps(
                        {
                            "provider": asdict(proof),
                            "monitor": asdict(readiness),
                            "tests_reference": tests_reference,
                            "review_reference": review_reference,
                        },
                        default=str,
                    ),
                ),
            )
            for kind, row_id, snapshot in queued:
                cursor.execute(
                    "INSERT INTO bitget_operational_baseline_queue_retirements "
                    "(baseline_id,kind,row_id,prior_row,retired_at) "
                    "VALUES (%s,%s,%s,%s::jsonb,%s)",
                    (baseline_id, kind, row_id, snapshot, now),
                )
                if kind == "dispatch":
                    cursor.execute(
                        "UPDATE dispatches SET state='EXPIRED', "
                        "terminal_reason='operational-baseline-cutoff',updated_at=%s "
                        "WHERE id=%s",
                        (now, row_id),
                    )
                    cursor.execute(
                        "INSERT INTO dispatch_transitions "
                        "(id,dispatch_id,from_state,to_state,reason) "
                        "VALUES (%s,%s,'QUEUED','EXPIRED','operational-baseline-cutoff')",
                        (uuid4(), row_id),
                    )
                    cursor.execute(
                        "INSERT INTO notifications_outbox (id,dedup_key,payload) "
                        "VALUES (%s,%s,%s::jsonb) ON CONFLICT (dedup_key) DO NOTHING",
                        (
                            uuid4(),
                            f"dispatch-transition:{row_id}:QUEUED:EXPIRED:"
                            "operational-baseline-cutoff",
                            json.dumps(
                                {
                                    "kind": "execution-event",
                                    "dispatch_id": str(row_id),
                                    "from_state": "QUEUED",
                                    "to_state": "EXPIRED",
                                    "reason": "operational-baseline-cutoff",
                                }
                            ),
                        ),
                    )
                else:
                    cursor.execute(
                        "UPDATE source_management_updates SET state='failed', "
                        "updated_at=%s WHERE id=%s",
                        (now, row_id),
                    )
            for incident in expected_incidents:
                cursor.execute(
                    "INSERT INTO bitget_operational_baseline_incident_releases "
                    "(baseline_id,scope,prior_latch,released_at) VALUES (%s,%s,%s::jsonb,%s)",
                    (baseline_id, incident.scope, json.dumps(asdict(incident), default=str), now),
                )
                cursor.execute(
                    "UPDATE venue_kill_switches SET active=false, "
                    "reason=%s,updated_at=%s WHERE scope=%s",
                    (f"operational-baseline:{epoch}", now, incident.scope),
                )
            # Audit inserts and triggers may still take time under the fences.
            # Roll back the entire activation/release if either proof aged meanwhile.
            cursor.execute("SELECT clock_timestamp()")
            final_now = cursor.fetchone()[0]
            if not _fresh(proof.observed_at, final_now):
                raise ValueError("invalid-or-stale-provider-proof")
            if not all(
                _fresh(t, final_now)
                for t in (
                    readiness.observed_at,
                    readiness.private_pong_at,
                    readiness.clean_cycle_at,
                )
            ):
                raise ValueError("invalid-or-stale-monitor-readiness")
            connection.commit()
            return epoch
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def prepare(
        self,
        *,
        context: BaselineContext,
        approval_reference: str,
        entry_ids: tuple[UUID, ...],
        reservation_ids: tuple[UUID, ...],
    ) -> UUID:
        if (
            not isinstance(context, BaselineContext)
            or not approval_reference.strip()
            or len(set(entry_ids)) != 17
            or len(entry_ids) != 17
            or len(set(reservation_ids)) != 4
            or len(reservation_ids) != 4
        ):
            raise ValueError("exact-owner-approved-allowlist-required")
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('bitget'))")
            captured: list[str] = []
            cursor.execute(
                "SELECT id,(to_jsonb(i) - 'updated_at')::text FROM live_order_intents i "
                "WHERE id=ANY(%s) ORDER BY id FOR UPDATE",
                (list(entry_ids),),
            )
            intents_json = cursor.fetchall()
            intents = [(r[0], json.loads(r[1], parse_float=Decimal)) for r in intents_json]
            if len(intents) != 17 or any(
                r[1]["exchange"] != "bitget"
                or r[1]["role"] != "ENTRY"
                or r[1]["state"] != "filled"
                or r[1]["filled_qty"] <= 0
                for r in intents
            ):
                raise ValueError("allowlist-entry-not-filled")
            cursor.execute(
                "SELECT id,(to_jsonb(m) - 'updated_at')::text FROM bitget_margin_reservations m "
                "WHERE id=ANY(%s) ORDER BY id FOR UPDATE",
                (list(reservation_ids),),
            )
            reservations_json = cursor.fetchall()
            reservations = [
                (r[0], json.loads(r[1], parse_float=Decimal)) for r in reservations_json
            ]
            oids = {r[1]["client_order_id"] for r in intents}
            if len(reservations) != 4 or any(
                r[1]["exchange"] != "bitget"
                or r[1]["state"] != "consumed"
                or r[1]["environment"] is not None
                or r[1]["client_order_id"] not in oids
                for r in reservations
            ):
                raise ValueError("allowlist-reservation-not-legacy-consumed")
            cursor.execute(
                "SELECT id,(to_jsonb(d) - 'updated_at')::text FROM dispatches d "
                "WHERE id=ANY(%s) ORDER BY id FOR UPDATE",
                ([UUID(r[1]["dispatch_id"]) for r in reservations],),
            )
            dispatches = cursor.fetchall()
            if len(dispatches) != 4:
                raise ValueError("allowlist-dispatch-not-exact")
            for kind, rows in (
                ("intent", intents_json),
                ("reservation", reservations_json),
                ("dispatch", dispatches),
            ):
                captured.extend(
                    '{"kind":'
                    + json.dumps(kind)
                    + ',"id":'
                    + json.dumps(str(r[0]))
                    + ',"snapshot":'
                    + r[1]
                    + "}"
                    for r in rows
                )
            cursor.execute(
                "INSERT INTO venue_kill_switches(scope,active,reason) "
                "VALUES ('global',false,'operational-baseline-initialized') "
                "ON CONFLICT (scope) DO NOTHING RETURNING scope"
            )
            created_global = cursor.fetchone() is not None
            baseline_id = uuid4()
            cursor.execute(
                "INSERT INTO bitget_operational_baselines "
                "(id,account_identity,api_key_sha256,approval_reference,"
                "captured_rows,created_global_inactive) "
                "VALUES (%s,%s,%s,%s,%s::jsonb,%s)",
                (
                    baseline_id,
                    context.account_identity,
                    context.api_key_sha256,
                    approval_reference,
                    "[" + ",".join(captured) + "]",
                    created_global,
                ),
            )
            connection.commit()
            return baseline_id
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
