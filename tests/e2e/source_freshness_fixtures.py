"""Eligible SOURCE fixtures for independent admission tests (no analyzer dependency)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4


def eligible_dispatch_source(connection, dispatch_id):
    message_id, signal_id = uuid4(), uuid4()
    # received_at is the Telegram SOURCE date, never a retrieval/ingest clock.
    source_date = datetime.now(UTC)
    connection.execute(
        """INSERT INTO telegram_messages
        (id,channel_id,message_id,revision_hash,raw_text,received_at,
         intake_state,ingestion_origin,entry_expires_at)
        VALUES (%s,7,%s,%s,'offline fixture',%s,'ANALYZED','realtime',%s)""",
        (
            message_id,
            uuid4().int % 1000000000,
            "a" * 64,
            source_date,
            source_date + timedelta(minutes=5),
        ),
    )
    connection.execute(
        """INSERT INTO canonical_signals
        (id,message_id,revision,pair_token,direction,entry_price,stop_loss,take_profits)
        VALUES (%s,%s,%s,'BTCUSDT','LONG',100,90,'[110]'::jsonb)""",
        (signal_id, message_id, "a" * 64),
    )
    connection.execute(
        "UPDATE dispatches SET source_type='canonical_signal',source_id=%s WHERE id=%s",
        (signal_id, dispatch_id),
    )
