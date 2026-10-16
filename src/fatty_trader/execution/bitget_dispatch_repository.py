"""Durable PostgreSQL claim and transition boundary for Bitget dispatches."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from fatty_trader.intake.freshness import SOURCE_ELIGIBLE_SQL, expire_source_dispatch


def _retire_stale(cursor: Any) -> None:
    cursor.execute(
        """SELECT d.id FROM dispatches d
        JOIN canonical_signals s ON s.id=d.source_id
        JOIN telegram_messages tm ON tm.id=s.message_id
        WHERE d.exchange='bitget' AND d.state='QUEUED'
          AND NOT EXISTS (SELECT 1 FROM bitget_baseline_records b
              WHERE b.record_kind='dispatch' AND b.record_id=d.id)
          AND (d.claimed_by IS NULL OR d.lease_until <= clock_timestamp())
          AND NOT COALESCE(("""
        + SOURCE_ELIGIBLE_SQL.format(alias="tm")
        + """), false)
        FOR UPDATE OF d SKIP LOCKED"""
    )
    ids = []
    while (row := cursor.fetchone()) is not None:
        ids.append(row[0])
    for dispatch_id in ids:
        expire_source_dispatch(cursor, dispatch_id, "QUEUED")


class Cursor(Protocol):
    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> object: ...
    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@dataclass(frozen=True)
class BitgetDispatch:
    id: UUID
    state: str
    claimed_by: str
    attempts: int
    pair_token: str
    direction: str
    entry_price: Decimal
    stop_loss: Decimal
    take_profits: tuple[Decimal, ...]
    source_channel_id: int | None = None
    source_message_id: int | None = None
    source_received_at: datetime | None = None
    order_type: str = "MARKET"
    manual_margin: Decimal | None = None
    manual_leverage: int | None = None
    is_manual: bool = False
    source_id: UUID | None = None
    canonical_signal_id: UUID | None = None
    source_revision: str | None = None


_CLAIM_SQL = (
    """
WITH next_dispatch AS (
    SELECT d.id
    FROM dispatches d
    JOIN canonical_signals s ON s.id=d.source_id
    JOIN telegram_messages tm ON tm.id=s.message_id
    WHERE d.exchange = 'bitget'
      AND ("""
    + SOURCE_ELIGIBLE_SQL.format(alias="tm")
    + """)
      AND d.state = 'QUEUED'
      AND NOT EXISTS (SELECT 1 FROM bitget_baseline_records b
          WHERE b.record_kind='dispatch' AND b.record_id=d.id)
      AND (claimed_by IS NULL OR lease_until <= now())
    ORDER BY d.created_at, d.id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE dispatches d
SET claimed_by = %s,
    lease_until = now() + (%s * interval '1 second'),
    attempts = attempts + 1,
    updated_at = now()
FROM next_dispatch n, canonical_signals s, telegram_messages tm
WHERE d.id = n.id AND s.id = d.source_id AND tm.id = s.message_id
RETURNING d.id, d.state, d.claimed_by, d.attempts, s.pair_token, s.direction,
          s.entry_price, s.stop_loss, s.take_profits, tm.channel_id, tm.message_id,
          tm.received_at, d.source_id, s.id, s.revision;
"""
)

_RESERVE_CANARY_ENTRY_SQL = """
WITH canary_lock AS (
    SELECT pg_advisory_xact_lock(hashtext(%s))
), reservation AS (
    INSERT INTO canary_entry_reservations (dispatch_id, exchange)
    SELECT %s, %s FROM canary_lock
    WHERE (
        SELECT count(*) FROM (
            SELECT COALESCE(
                i.dispatch_id::text, m.dispatch_id::text, 'intent:' || i.client_order_id
            ) AS dispatch_key
            FROM live_order_intents i
            LEFT JOIN bitget_margin_reservations m
              ON m.exchange = i.exchange AND m.client_order_id = i.client_order_id
            WHERE i.exchange = %s AND i.role = 'ENTRY'
              AND i.state NOT IN ('filled', 'rejected', 'cancelled', 'reconciled')
              AND NOT EXISTS (SELECT 1 FROM bitget_baseline_records b
                  WHERE b.record_kind='intent' AND b.record_id=i.id)
            UNION
            SELECT r.dispatch_id::text
            FROM canary_entry_reservations r
            JOIN dispatches d ON d.id = r.dispatch_id
            WHERE r.exchange = %s
              AND d.state NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')
              AND NOT EXISTS (SELECT 1 FROM bitget_baseline_records b
                  WHERE b.record_kind='dispatch' AND b.record_id=d.id)
        ) unique_dispatches
    ) < %s
    ON CONFLICT (dispatch_id) DO NOTHING
    RETURNING dispatch_id
)
SELECT dispatch_id FROM reservation;
"""


class PostgresBitgetDispatchRepository:
    """Lease-safe dispatch repository; every mutation is transactional and auditable."""

    def __init__(
        self,
        connection_factory: Callable[[], Connection],
        *,
        console_enabled: bool = False,
        console_scope: str = "bitget:configured-bitget:LIVE",
    ) -> None:
        self._connection_factory = connection_factory
        self._console_enabled = console_enabled
        self._console_scope = console_scope

    def claim(self, worker_id: str, lease_seconds: int) -> BitgetDispatch | None:
        if not worker_id:
            raise ValueError("worker_id is required")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            _retire_stale(cursor)
            cursor.execute(_CLAIM_SQL, (worker_id, lease_seconds))
            row = cursor.fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return _dispatch_from_row(row) if row is not None else None

    def entry_source_eligible(self, dispatch_id: UUID) -> bool:
        """Recheck immediately before a new ENTRY, not read-only recovery."""
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            if self._console_enabled:
                cursor.execute(
                    "SELECT active FROM console_admission_pauses WHERE scope=%s",
                    (self._console_scope,),
                )
                pause = cursor.fetchone()
                if pause is not None and pause[0]:
                    connection.commit()
                    return False
                cursor.execute(
                    """SELECT d.state, m.expires_at > clock_timestamp(), true
                    FROM dispatches d JOIN console_manual_entries m ON m.id=d.source_id
                    JOIN console_jobs j ON j.id=m.job_id
                    WHERE d.id=%s AND d.source_type='console_manual'
                      AND j.state IN ('running','reconciliation_pending')
                    FOR UPDATE OF d""",
                    (dispatch_id,),
                )
                manual = cursor.fetchone()
                if manual is not None:
                    connection.commit()
                    return bool(manual[0] in {"QUEUED", "SUBMITTING"} and manual[1] and manual[2])
            cursor.execute(
                "SELECT d.state, COALESCE(("
                + SOURCE_ELIGIBLE_SQL.format(alias="tm")
                + "), false) FROM dispatches d JOIN canonical_signals s ON s.id=d.source_id "
                "JOIN telegram_messages tm ON tm.id=s.message_id WHERE d.id=%s "
                "AND NOT EXISTS(SELECT 1 FROM bitget_baseline_records b "
                "WHERE b.record_kind='dispatch' AND b.record_id=d.id) FOR UPDATE OF d",
                (dispatch_id,),
            )
            row = cursor.fetchone()
            eligible = row is not None and row[0] in {"QUEUED", "SUBMITTING"} and row[1]
            if not eligible and row is not None and row[0] in {"QUEUED", "SUBMITTING"}:
                expire_source_dispatch(cursor, dispatch_id, row[0])
            connection.commit()
            return bool(eligible)
        except Exception:
            connection.rollback()
            raise

    def recovery_candidates(self) -> list[tuple[BitgetDispatch, str]]:
        """Load canonical expectations for durable owned ENTRYs, not just active margin.

        Consumed reservations and FILLED dispatches remain eligible unless their
        exact records have an immutable approved historical-baseline receipt.
        A lost canonical source raises rather than silently declaring recovery ready.
        Legacy entries without reservation ownership require a separate migration/
        operator reconciliation; this API never adopts a manual provider position.
        """
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """SELECT d.id, d.state, d.claimed_by, d.attempts,
                          s.pair_token, s.direction, s.entry_price, s.stop_loss,
                          s.take_profits, tm.channel_id, tm.message_id, i.client_order_id,
                          tm.received_at, d.source_id, s.id, d.revision
                   FROM live_order_intents i
                   JOIN bitget_margin_reservations m ON m.exchange = i.exchange
                       AND m.client_order_id = i.client_order_id
                   JOIN dispatches d ON d.id = m.dispatch_id AND d.exchange = i.exchange
                   LEFT JOIN canonical_signals s ON s.id = d.source_id
                   LEFT JOIN telegram_messages tm ON tm.id = s.message_id
                   WHERE i.exchange = 'bitget' AND i.role = 'ENTRY'
                     AND NOT EXISTS (SELECT 1 FROM bitget_baseline_records b
                         WHERE (b.record_kind='intent' AND b.record_id=i.id)
                            OR (b.record_kind='reservation' AND b.record_id=m.id)
                            OR (b.record_kind='dispatch' AND b.record_id=d.id))
                     AND (i.filled_qty > 0 OR i.state NOT IN
                          ('rejected', 'cancelled', 'reconciled'))
                   ORDER BY d.created_at, d.id"""
            )
            rows = []
            while (row := cursor.fetchone()) is not None:
                if row[4] is None or row[9] is None:
                    if self._console_enabled:
                        manual_cursor = connection.cursor()
                        manual_cursor.execute(
                            """SELECT m.symbol,m.direction,m.entry_price,m.stop_loss,
                                      m.take_profits,m.order_type
                            FROM console_manual_entries m JOIN dispatches d ON d.source_id=m.id
                            WHERE d.id=%s AND d.source_type='console_manual'""",
                            (row[0],),
                        )
                        manual = manual_cursor.fetchone()
                        if manual is not None:
                            targets = manual[4]
                            if isinstance(targets, str):
                                targets = json.loads(targets)
                            rows.append(
                                (
                                    BitgetDispatch(
                                        row[0],
                                        row[1],
                                        row[2] or "",
                                        row[3],
                                        manual[0],
                                        manual[1],
                                        Decimal(str(manual[2])),
                                        Decimal(str(manual[3])),
                                        tuple(Decimal(str(p)) for p in targets),
                                        order_type=manual[5],
                                        is_manual=True,
                                        source_id=row[13],
                                        source_revision=row[15],
                                    ),
                                    str(row[11]),
                                )
                            )
                            continue
                    raise ValueError("recovery canonical source missing")
                rows.append((_dispatch_from_row(row[:11] + row[12:]), str(row[11])))
            connection.commit()
            return rows
        except Exception:
            connection.rollback()
            raise

    def unresolved_protection_issues(self) -> list[str]:
        """Durable account admission veto; independent of optional capability gate.

        FILLED is fill truth, never proof of protection. UNPROTECTED is an
        explicit dispatcher verdict. Verified recovery clears a verdict; an
        approved exact-record baseline only excludes it as unresolved history.
        """
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """SELECT EXISTS(
                SELECT 1 FROM dispatches d WHERE lower(d.exchange)='bitget'
                AND NOT EXISTS (SELECT 1 FROM bitget_baseline_records b
                    WHERE b.record_kind='dispatch' AND b.record_id=d.id)
                AND (state='UNPROTECTED' OR terminal_reason='missing-protection-escalated'
                     OR terminal_reason LIKE 'recovery-missing-protection:%%'
                     OR terminal_reason LIKE 'recovery-filled-protection-unverified%%')
            )""",
                (),
            )
            blocked = cursor.fetchone()[0]
            connection.commit()
            return ["unresolved-protection"] if blocked else []
        except Exception:
            connection.rollback()
            raise

    def inventory_issues(self, environment: str) -> list[str]:
        """Account-wide unresolved ownership is not an empty candidate set.

        Intents have no environment column: unmatched rows cannot safely be scoped
        away. Cross-environment ownership is similarly not adopted into this graph.
        """
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """SELECT EXISTS (
                    SELECT 1 FROM live_order_intents i
                    LEFT JOIN bitget_margin_reservations m ON m.exchange=i.exchange
                        AND (m.client_order_id=i.client_order_id OR m.id=i.margin_reservation_id)
                    LEFT JOIN dispatches d ON d.id=m.dispatch_id AND d.exchange=i.exchange
                    WHERE i.exchange='bitget' AND i.role='ENTRY'
                      AND NOT EXISTS (SELECT 1 FROM bitget_baseline_records b
                          WHERE b.record_kind='intent' AND b.record_id=i.id)
                      AND (i.filled_qty>0 OR i.state NOT IN ('rejected','cancelled','reconciled'))
                      AND (d.id IS NULL OR m.environment IS DISTINCT FROM %s
                           OR (i.dispatch_id IS NOT NULL AND i.dispatch_id <> d.id))
                ), EXISTS (
                    SELECT 1 FROM bitget_margin_reservations m
                    LEFT JOIN live_order_intents i ON i.exchange=m.exchange
                        AND i.client_order_id=m.client_order_id AND i.role='ENTRY'
                    WHERE m.exchange='bitget' AND m.state IN ('reserved','unknown','consumed')
                      AND NOT EXISTS (SELECT 1 FROM bitget_baseline_records b
                          WHERE b.record_kind='reservation' AND b.record_id=m.id)
                      AND (m.environment=%s OR m.environment IS NULL) AND i.client_order_id IS NULL
                )""",
                (environment, environment),
            )
            row = cursor.fetchone()
            connection.commit()
            return [
                reason
                for active, reason in zip(
                    row,
                    ("orphan-entry-intent", "reservation-without-entry-intent"),
                    strict=False,
                )
                if active
            ]
        except Exception:
            connection.rollback()
            raise

    def baseline_binding_issues(self, account_id: str | None, environment: str | None) -> list[str]:
        """Exclusions authorize only the approved authenticated account/environment."""
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM bitget_audited_baselines "
                "WHERE account_id IS DISTINCT FROM %s OR environment IS DISTINCT FROM %s)",
                (account_id, environment),
            )
            blocked = cursor.fetchone()[0]
            connection.commit()
            return ["baseline-account-environment-mismatch"] if blocked else []
        except Exception:
            connection.rollback()
            raise

    def reserve_canary_entry(self, dispatch_id: UUID, exchange: str, max_orders: int) -> bool:
        if not exchange:
            raise ValueError("exchange is required")
        if max_orders < 1:
            raise ValueError("canary cap must be positive")
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                _RESERVE_CANARY_ENTRY_SQL,
                (exchange, dispatch_id, exchange, exchange, exchange, max_orders),
            )
            reserved = cursor.fetchone() is not None
            connection.commit()
            return reserved
        except Exception:
            connection.rollback()
            raise

    def release_canary_entry(self, dispatch_id: UUID, exchange: str) -> None:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "DELETE FROM canary_entry_reservations WHERE dispatch_id = %s AND exchange = %s",
                (dispatch_id, exchange),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def transition(
        self,
        dispatch_id: UUID,
        *,
        expected_state: str,
        target_state: str,
        reason: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        reason = safe_transition_reason(reason)
        event_evidence = {
            "schema_version": 1,
            "exchange": "bitget",
            "dispatch_id": str(dispatch_id),
            "stage": expected_state.lower(),
            "outcome": target_state.lower(),
            "observed_at": datetime.now(UTC).isoformat(),
            "reason_code": reason,
            "provider_effect_scope": "entry_submission",
            "provider_effect": (
                "submission_may_have_occurred"
                if expected_state not in {"QUEUED", "PREFLIGHT", "SIZED", "VALIDATED"}
                or target_state == "SUBMITTING"
                else "not_submitted"
            ),
            **(evidence or {}),
        }
        event_evidence["dispatch_id"] = str(dispatch_id)
        event_evidence["exchange"] = "bitget"
        event_evidence["reason_code"] = reason
        for field in ("audit_evidence", "contract_catalog"):
            if field in event_evidence:
                event_evidence[field] = sanitize_audit_evidence(event_evidence[field])
        serialized_evidence = json.dumps(event_evidence, allow_nan=False)
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE dispatches
                SET state = %s, terminal_reason = %s, updated_at = now()
                WHERE id = %s AND exchange = 'bitget' AND state = %s
                RETURNING id
                """,
                (target_state, reason, dispatch_id, expected_state),
            )
            if cursor.fetchone() is None:
                raise ValueError("dispatch state changed concurrently or does not exist")
            cursor.execute(
                """
                INSERT INTO dispatch_transitions
                    (id, dispatch_id, from_state, to_state, reason, evidence)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                """,
                (uuid4(), dispatch_id, expected_state, target_state, reason, serialized_evidence),
            )
            if target_state in {"FILLED", "REJECTED", "CANCELLED", "RECONCILED"}:
                cursor.execute(
                    """
                    DELETE FROM canary_entry_reservations
                    WHERE dispatch_id = %s AND exchange = 'bitget'
                    """,
                    (dispatch_id,),
                )
            cursor.execute(
                """
                INSERT INTO notifications_outbox (id, dedup_key, payload)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (dedup_key) DO NOTHING
                """,
                (
                    uuid4(),
                    f"dispatch-transition:{dispatch_id}:{expected_state}:{target_state}:"
                    f"{reason or ''}",
                    json.dumps(
                        {
                            "kind": "execution-event",
                            "dispatch_id": str(dispatch_id),
                            "from_state": expected_state,
                            "to_state": target_state,
                            "reason": reason or "none",
                            "evidence": event_evidence,
                        }
                    ),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        print(
            json.dumps(
                {
                    "event": "bitget-dispatch-transition",
                    "from_state": expected_state,
                    "to_state": target_state,
                    "reason": reason,
                    **event_evidence,
                },
                allow_nan=False,
            ),
            flush=True,
        )

    def alert(self, dispatch_id: UUID, reason: str) -> None:
        reason = safe_transition_reason(reason) or "invalid-alert-reason"
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO notifications_outbox (id, dedup_key, payload)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (dedup_key) DO NOTHING
                """,
                (
                    uuid4(),
                    f"bitget-dispatch:{dispatch_id}:{reason}",
                    json.dumps(
                        {
                            "kind": "execution-alert",
                            "dispatch_id": str(dispatch_id),
                            "reason": reason,
                        }
                    ),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _dispatch_from_row(row: Any) -> BitgetDispatch:
    values = (
        row
        if isinstance(row, dict)
        else {
            "id": row[0],
            "state": row[1],
            "claimed_by": row[2],
            "attempts": row[3],
            "pair_token": row[4],
            "direction": row[5],
            "entry_price": row[6],
            "stop_loss": row[7],
            "take_profits": row[8],
            "source_channel_id": row[9] if len(row) > 9 else None,
            "source_message_id": row[10] if len(row) > 10 else None,
            "source_received_at": row[11] if len(row) > 11 else None,
            "source_id": row[12] if len(row) > 12 else None,
            "canonical_signal_id": row[13] if len(row) > 13 else None,
            "source_revision": row[14] if len(row) > 14 else None,
        }
    )
    raw_take_profits = values["take_profits"] or []
    if isinstance(raw_take_profits, str):
        raw_take_profits = json.loads(raw_take_profits)
    return BitgetDispatch(
        id=UUID(str(values["id"])),
        state=str(values["state"]),
        claimed_by=str(values["claimed_by"]),
        attempts=int(values["attempts"]),
        pair_token=str(values["pair_token"]),
        direction=str(values["direction"]),
        entry_price=Decimal(str(values["entry_price"])),
        stop_loss=Decimal(str(values["stop_loss"])),
        take_profits=tuple(Decimal(str(value)) for value in raw_take_profits),
        source_channel_id=(
            int(values["source_channel_id"])
            if values.get("source_channel_id") is not None
            else None
        ),
        source_message_id=(
            int(values["source_message_id"])
            if values.get("source_message_id") is not None
            else None
        ),
        source_received_at=values.get("source_received_at"),
        source_id=values.get("source_id"),
        canonical_signal_id=values.get("canonical_signal_id"),
        source_revision=values.get("source_revision"),
    )


def dispatch_evidence(dispatch: BitgetDispatch) -> dict[str, Any]:
    """Public identifiers only; never include account payloads or source text."""
    return {
        "schema_version": 1,
        "exchange": "bitget",
        "dispatch_id": str(dispatch.id),
        "source_id": str(dispatch.source_id) if dispatch.source_id else None,
        "canonical_signal_id": (
            str(dispatch.canonical_signal_id) if dispatch.canonical_signal_id else None
        ),
        "source_channel_id": dispatch.source_channel_id,
        "source_message_id": dispatch.source_message_id,
        "source_revision": dispatch.source_revision,
        "source_received_at": (
            dispatch.source_received_at.astimezone(UTC).isoformat()
            if dispatch.source_received_at
            else None
        ),
        "worker_id": dispatch.claimed_by,
        "attempt": dispatch.attempts,
        "symbol": dispatch.pair_token,
        "direction": dispatch.direction,
    }


_PUBLIC_AUDIT_KEYS = frozenset(
    {
        "stage",
        "code",
        "requested_symbol",
        "row_count",
        "invalid_rows",
        "invalid_row_indexes",
        "index",
        "row_index",
        "field",
        "field_types",
        "type",
        "row_type",
        "payload_type",
        "payload_sha256",
        "response_hash",
        "public_response_hash",
        "offending_symbols",
        "public_symbols",
        "symbols",
        "symbol",
        "symbol_type",
        "selected_metadata",
        "target_metadata",
        "target_field_types",
        "target_metadata_types",
        "selected_symbol",
        "symbolStatus",
        "minTradeNum",
        "minTradeUSDT",
        "volumePlace",
        "pricePlace",
        "priceEndStep",
        "sizeMultiplier",
        "maxLever",
        "minLever",
        "productType",
        "status",
        "count",
        "truncated",
        "invalid_count",
        "duplicate_count",
        "match_count",
        "hash_encoding",
        "invalid_row_count",
        "matching_row_count",
        "matching_row_indexes",
        "unicode_symbol_count",
        "unicode_symbols",
        "maxOrderQty",
        "maxMarketOrderQty",
        "contractValue",
        "contractSize",
    }
)


def sanitize_audit_evidence(value: object) -> dict[str, Any]:
    """Copy only bounded public diagnostics; never stringify arbitrary objects."""
    remaining = 160

    def clean(item: object, depth: int) -> Any:
        nonlocal remaining
        if depth > 5 or remaining <= 0:
            return None
        remaining -= 1
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, str):
            # Public symbols/type names/codes are tokens, not messages, URLs,
            # headers, credentials, JSON bodies, or prompts.
            if len(item) > 128 or not all(c.isalnum() or c in "_-.:" for c in item):
                return "[redacted]"
            return item
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for key, child in item.items():
                if remaining <= 0 or len(result) >= 24:
                    break
                if isinstance(key, str) and key in _PUBLIC_AUDIT_KEYS:
                    result[key] = clean(child, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            return [clean(child, depth + 1) for child in item[:12] if remaining > 0]
        return None

    result = clean(value, 0)
    return result if isinstance(result, dict) else {}


def safe_transition_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    if 0 < len(reason) <= 160 and all(c.isalnum() or c in "-_:." for c in reason):
        return reason
    return "redacted-transition-reason"
