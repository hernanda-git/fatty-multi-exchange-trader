"""Database-clock SOURCE entry eligibility, never ingest-clock freshness."""

import json
from typing import Any
from uuid import UUID, uuid4


class SourceFreshnessExpired(ValueError):
    """An audited SOURCE retirement, not an ambiguous provider outcome."""


def expire_source_dispatch(cursor: Any, dispatch_id: UUID, state: str) -> None:
    """Expire only known-unsent entries while holding the dispatch lock.

    UNKNOWN is never evidence of no send, even if its intent is unavailable.
    SUBMITTING needs durable-intent evidence before age can retire it.
    """
    if state not in {"QUEUED", "PREFLIGHT", "SUBMITTING"}:
        return
    # A durable request may already have crossed the POST boundary even when
    # its acknowledgement never survived. SOURCE age is not provider evidence.
    cursor.execute(
        "SELECT EXISTS(SELECT 1 FROM bitget_margin_reservations "
        "WHERE dispatch_id=%s AND state IN ('unknown','consumed'))",
        (dispatch_id,),
    )
    if cursor.fetchone()[0]:
        return
    cursor.execute(
        "SELECT EXISTS(SELECT 1 FROM live_order_intents i "
        "JOIN bitget_margin_reservations m ON m.exchange=i.exchange "
        "AND m.client_order_id=i.client_order_id WHERE m.dispatch_id=%s "
        "AND i.role='ENTRY' AND (i.filled_qty>0 OR i.state NOT IN "
        "('rejected','cancelled','reconciled')))",
        (dispatch_id,),
    )
    if cursor.fetchone()[0]:
        return
    reason = "stale-source-message"
    cursor.execute(
        "UPDATE dispatches SET state='EXPIRED', terminal_reason=%s, claimed_by=NULL, "
        "lease_until=NULL, updated_at=clock_timestamp() WHERE id=%s",
        (reason, dispatch_id),
    )
    cursor.execute(
        "INSERT INTO dispatch_transitions (id,dispatch_id,from_state,to_state,reason) "
        "VALUES (%s,%s,%s,'EXPIRED',%s)",
        (uuid4(), dispatch_id, state, reason),
    )
    cursor.execute(
        "INSERT INTO notifications_outbox (id,dedup_key,payload) VALUES "
        "(%s,%s,%s::jsonb) ON CONFLICT (dedup_key) DO NOTHING",
        (
            uuid4(),
            f"dispatch-transition:{dispatch_id}:{state}:EXPIRED:{reason}",
            json.dumps(
                {
                    "kind": "execution-event",
                    "dispatch_id": str(dispatch_id),
                    "from_state": state,
                    "to_state": "EXPIRED",
                    "reason": reason,
                }
            ),
        ),
    )
    cursor.execute(
        "UPDATE bitget_margin_reservations SET state='released', resolved_at=clock_timestamp(), "
        "resolution_reason='stale-source-message' WHERE dispatch_id=%s AND state='reserved'",
        (dispatch_id,),
    )
    cursor.execute("DELETE FROM canary_entry_reservations WHERE dispatch_id=%s", (dispatch_id,))


# PostgreSQL timestamptz is aware; isfinite also excludes +/- infinity before
# psycopg attempts to decode those values as Python datetimes.
SOURCE_ELIGIBLE_SQL = """
{alias}.ingestion_origin IN ('realtime', 'catchup', 'backfill')
AND isfinite({alias}.received_at)
AND isfinite({alias}.entry_expires_at)
AND {alias}.entry_rejection_reason IS NULL
-- An audited edit is a permanent message-level veto, including reverted
-- content and subsequent retrieval of the old unchanged revision.
AND NOT EXISTS (
    SELECT 1 FROM telegram_messages source_edit
    WHERE source_edit.channel_id = {alias}.channel_id
      AND source_edit.message_id = {alias}.message_id
      AND source_edit.entry_rejection_reason = 'edited-source-message'
)
AND {alias}.entry_expires_at > clock_timestamp()
AND {alias}.received_at > clock_timestamp() - interval '5 minutes'
AND {alias}.received_at <= clock_timestamp() + interval '30 seconds'
-- Baseline cutoff never resurrects preactivation entries, even if exclusions
-- become invalid or authenticated runtime context is missing.
AND NOT EXISTS (
    SELECT 1 FROM bitget_operational_baseline_activations baseline_epoch
    WHERE {alias}.received_at <= baseline_epoch.source_cutoff
)
"""
