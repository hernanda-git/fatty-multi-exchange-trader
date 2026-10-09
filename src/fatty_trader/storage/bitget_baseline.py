"""GET-only evidence and exact-record retirement, never historical close proof.

Run only with all account mutation consumers stopped. Database locks prevent local
ledger races; no database lock can prevent a different process trading externally.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import uuid4

PRODUCT_TYPES = ("USDT-FUTURES", "USDC-FUTURES", "COIN-FUTURES")
PLAN_TYPES = ("normal_plan", "profit_loss", "track_plan")
MAX_EVIDENCE_AGE_MS = 30_000
MAX_CLOCK_SKEW_MS = 1_000


class BaselineRefused(ValueError):
    """Evidence does not authorize retiring historical admission commitments."""


class BaselineGetClient(Protocol):
    @property
    def environment(self) -> str: ...

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


def _positive_identity(value: Any) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise BaselineRefused("account identity is missing or malformed")
    if int(value) <= 0:
        raise BaselineRefused("account identity is invalid")
    return value


def _zero_quantity(row: Any) -> None:
    if not isinstance(row, dict) or not isinstance(row.get("symbol"), str) or not row["symbol"]:
        raise BaselineRefused("position evidence is malformed")
    raw = row.get("total")
    if isinstance(raw, bool) or not isinstance(raw, (str, int, Decimal)):
        raise BaselineRefused("position quantity is unknown")
    try:
        quantity = Decimal(str(raw))
    except InvalidOperation as exc:
        raise BaselineRefused("position quantity is unknown") from exc
    if not quantity.is_finite() or quantity != 0:
        raise BaselineRefused("position exposure is positive or unknown")


async def collect_flat_evidence(
    client: BaselineGetClient, expected_account_id: str, environment: str
) -> dict[str, Any]:
    """Strict authenticated inventory for every Classic futures product/family.

    Empty inventory is an explicit empty list or the provider-observed paired
    null list/null terminal cursor. Missing keys and all other shapes are unknown.
    Raw evidence is retained: the receipt never rewrites null into a fabricated
    array. Every family/product is queried again under the database lock.
    """
    expected_account_id = _positive_identity(expected_account_id)
    if environment not in {"LIVE", "DEMO"} or client.environment != environment:
        raise BaselineRefused("provider environment mismatch")
    started_ms = int(time.time() * 1000)
    clock = await client._get("/api/v2/public/time")
    raw_time = clock.get("serverTime") if isinstance(clock, dict) else None
    if not isinstance(raw_time, (str, int)) or isinstance(raw_time, bool):
        raise BaselineRefused("server clock evidence is unknown")
    try:
        server_ms = int(raw_time)
    except ValueError as exc:
        raise BaselineRefused("server clock evidence is unknown") from exc
    received_ms = int(time.time() * 1000)
    if not started_ms - MAX_CLOCK_SKEW_MS <= server_ms <= received_ms + MAX_CLOCK_SKEW_MS:
        raise BaselineRefused("server clock skew exceeds baseline limit")
    if received_ms - started_ms > MAX_CLOCK_SKEW_MS:
        raise BaselineRefused("server clock round trip is too slow")
    identity = await client._get("/api/v2/spot/account/info")
    account_id = _positive_identity(identity.get("userId") if isinstance(identity, dict) else None)
    if account_id != expected_account_id:
        raise BaselineRefused("authenticated account identity mismatch")
    inventory: dict[str, Any] = {}
    for product_type in PRODUCT_TYPES:
        params = {"productType": product_type}
        positions = await client._get("/api/v2/mix/position/all-position", params)
        if not isinstance(positions, list):
            raise BaselineRefused("positions inventory is unknown")
        for row in positions:
            _zero_quantity(row)
        pages: dict[str, Any] = {}
        for family in ("ordinary", *PLAN_TYPES):
            page_params = {**params, "limit": "100"}
            endpoint = "/api/v2/mix/order/orders-pending"
            if family != "ordinary":
                endpoint = "/api/v2/mix/order/orders-plan-pending"
                page_params["planType"] = family
            page = await client._get(endpoint, page_params)
            if not isinstance(page, dict) or "entrustedList" not in page or "endId" not in page:
                raise BaselineRefused("pending inventory is unknown or malformed")
            rows = page["entrustedList"]
            paired_null = rows is None and page["endId"] is None
            if not isinstance(rows, list) and not paired_null:
                raise BaselineRefused("pending inventory is unknown or malformed")
            if rows:
                raise BaselineRefused("pending provider order exists")
            # A cursor on an empty page signals incomplete/inconsistent pagination.
            if "endId" not in page or page["endId"] not in ("", None):
                raise BaselineRefused("pending pagination is incomplete")
            pages[family] = page
        inventory[product_type] = {"positions": positions, "pending": pages}
    finished_ms = int(time.time() * 1000)
    if finished_ms - started_ms > MAX_EVIDENCE_AGE_MS:
        raise BaselineRefused("provider evidence expired during inventory")
    return {
        "account_id": account_id,
        "environment": environment,
        "started_ms": started_ms,
        "finished_ms": finished_ms,
        "server_ms": server_ms,
        "inventory": inventory,
    }


def _fresh(evidence: dict[str, Any]) -> None:
    now_ms = int(time.time() * 1000)
    if not 0 <= now_ms - evidence["started_ms"] <= MAX_EVIDENCE_AGE_MS:
        raise BaselineRefused("provider evidence is stale")


class PostgresBitgetBaseline:
    """One transaction: lock, exact snapshot, fresh provider recheck, audited release."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    async def run(
        self,
        client: BaselineGetClient,
        *,
        expected_account_id: str,
        environment: str,
        historical_before: datetime,
        approval_reference: str,
        apply: bool = False,
        expected_digest: str | None = None,
    ) -> dict[str, Any]:
        if historical_before.tzinfo is None or historical_before >= datetime.now(UTC):
            raise BaselineRefused("historical cutoff must be timezone-aware and in the past")
        approval_reference = approval_reference.strip()
        if not approval_reference:
            raise BaselineRefused("approval reference is required")
        if apply and (expected_digest is None or len(expected_digest) != 64):
            raise BaselineRefused("apply requires the reviewed dry-run digest")
        evidence = await collect_flat_evidence(client, expected_account_id, environment)
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute(
                "LOCK TABLE live_order_intents, bitget_margin_reservations, dispatches, "
                "positions, orders, bitget_audited_baselines, bitget_baseline_records "
                "IN SHARE ROW EXCLUSIVE MODE"
            )
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM bitget_audited_baselines "
                "WHERE environment<>%s OR account_id<>%s)",
                (environment, expected_account_id),
            )
            if cursor.fetchone()[0]:
                raise BaselineRefused("database baseline account/environment binding differs")
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM positions "
                "WHERE exchange='bitget' AND closed_at IS NULL), "
                "EXISTS(SELECT 1 FROM dispatches WHERE exchange='bitget' "
                "AND claimed_by IS NOT NULL AND lease_until>clock_timestamp()), "
                "EXISTS(SELECT 1 FROM live_order_intents WHERE exchange='bitget' AND role<>'ENTRY' "
                "AND state NOT IN ('filled','cancelled','rejected','reconciled'))"
            )
            if any(cursor.fetchone()):
                raise BaselineRefused("active local position, worker lease, or non-entry intent")
            records = self._candidates(cursor, historical_before, environment)
            digest = hashlib.sha256(
                json.dumps(
                    {
                        "records": records,
                        "account_id": expected_account_id,
                        "environment": environment,
                        "historical_before": historical_before.isoformat(),
                        "approval_reference": approval_reference,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            counts = {
                kind: sum(record["kind"] == kind for record in records)
                for kind in ("intent", "dispatch", "reservation")
            }
            result: dict[str, Any] = {
                "applied": False,
                "approval_reference": approval_reference,
                "candidate_digest": digest,
                "counts": counts,
            }
            if not apply:
                _fresh(evidence)
                connection.rollback()
                return result
            if digest != expected_digest:
                raise BaselineRefused("database changed since reviewed dry-run")
            if not records:
                raise BaselineRefused("no unresolved historical records to baseline")
            # Repeat all GET evidence AFTER acquiring database write locks.
            evidence = await collect_flat_evidence(client, expected_account_id, environment)
            _fresh(evidence)
            baseline_id = uuid4()
            cursor.execute(
                "INSERT INTO bitget_audited_baselines "
                "(id,exchange,environment,account_id,approval_reference,historical_before,"
                "candidate_digest,provider_evidence,uncertainty_policy) "
                "VALUES (%s,'bitget',%s,%s,%s,%s,%s,%s::jsonb,"
                "'unresolved-history-not-verified-closure')",
                (
                    baseline_id,
                    environment,
                    expected_account_id,
                    approval_reference,
                    historical_before,
                    digest,
                    json.dumps(evidence),
                ),
            )
            for record in records:
                cursor.execute(
                    "INSERT INTO bitget_baseline_records "
                    "(baseline_id,record_kind,record_id,prior_snapshot) "
                    "VALUES (%s,%s,%s,%s::jsonb)",
                    (baseline_id, record["kind"], record["id"], record["snapshot"]),
                )
                if record["kind"] == "reservation":
                    cursor.execute(
                        "UPDATE bitget_margin_reservations SET state='released', "
                        "resolved_at=clock_timestamp(), resolution_reason=%s "
                        "WHERE id=%s AND state IN ('reserved','unknown','consumed')",
                        (f"audited-baseline:{baseline_id}:unresolved-history", record["id"]),
                    )
            _fresh(evidence)
            connection.commit()
            result.update(applied=True, baseline_id=str(baseline_id))
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _candidates(cursor: Any, cutoff: datetime, environment: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        cursor.execute(
            "SELECT i.id::text,to_jsonb(i)::text,i.created_at FROM live_order_intents i "
            "WHERE i.exchange='bitget' AND i.role='ENTRY' "
            "AND (i.filled_qty>0 OR i.state NOT IN ('rejected','cancelled','reconciled')) "
            "AND NOT EXISTS(SELECT 1 FROM bitget_baseline_records b "
            "WHERE b.record_kind='intent' AND b.record_id=i.id) ORDER BY i.id"
        )
        for record_id, snapshot, created_at in cursor.fetchall():
            if created_at >= cutoff:
                raise BaselineRefused(
                    "new unresolved entry lies outside approved historical cutoff"
                )
            records.append({"kind": "intent", "id": record_id, "snapshot": snapshot})
        # Keep PostgreSQL's JSON text verbatim: Python float decoding would round
        # high-precision NUMERIC amounts in the immutable financial snapshot.
        intent_oids = [json.loads(r["snapshot"])["client_order_id"] for r in records]
        cursor.execute(
            "SELECT m.id::text,to_jsonb(m)::text,m.created_at FROM bitget_margin_reservations m "
            "WHERE m.exchange='bitget' "
            "AND (m.state IN ('reserved','unknown','consumed') OR m.client_order_id=ANY(%s)) "
            "AND NOT EXISTS(SELECT 1 FROM bitget_baseline_records b "
            "WHERE b.record_kind='reservation' AND b.record_id=m.id) ORDER BY m.id",
            (intent_oids,),
        )
        for record_id, snapshot, created_at in cursor.fetchall():
            if created_at >= cutoff or json.loads(snapshot)["environment"] not in (
                None,
                environment,
            ):
                raise BaselineRefused("reservation outside historical/environment approval")
            records.append({"kind": "reservation", "id": record_id, "snapshot": snapshot})
        dispatch_ids = [
            json.loads(r["snapshot"])["dispatch_id"] for r in records if r["kind"] == "reservation"
        ]
        cursor.execute(
            "SELECT d.id::text,to_jsonb(d)::text,d.created_at FROM dispatches d "
            "WHERE d.exchange='bitget' AND "
            "(d.id=ANY(%s::uuid[]) OR d.state='UNPROTECTED' "
            "OR d.terminal_reason='missing-protection-escalated' "
            "OR d.terminal_reason LIKE 'recovery-missing-protection:%%' "
            "OR d.terminal_reason LIKE 'recovery-filled-protection-unverified%%') "
            "AND NOT EXISTS(SELECT 1 FROM bitget_baseline_records b "
            "WHERE b.record_kind='dispatch' AND b.record_id=d.id) ORDER BY d.id",
            (dispatch_ids,),
        )
        for record_id, snapshot, created_at in cursor.fetchall():
            if created_at >= cutoff or record_id not in dispatch_ids:
                raise BaselineRefused(
                    "unresolved dispatch has no approved historical entry binding"
                )
            records.append({"kind": "dispatch", "id": record_id, "snapshot": snapshot})
        return records
