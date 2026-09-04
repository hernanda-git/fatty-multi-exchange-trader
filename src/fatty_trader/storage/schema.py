"""Initial PostgreSQL DDL for durable, fail-closed trading state."""

from typing import Protocol


class SqlCursor(Protocol):
    def execute(self, statement: str) -> object: ...


INITIAL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS telegram_messages (
    id UUID PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    revision_hash CHAR(64) NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw_text TEXT NOT NULL,
    intake_state TEXT NOT NULL CHECK (
        intake_state IN ('RECEIVED', 'ANALYZED', 'FAILED', 'EXPIRED')
    ),
    UNIQUE (channel_id, message_id, revision_hash)
);

CREATE TABLE IF NOT EXISTS canonical_signals (
    id UUID PRIMARY KEY,
    message_id UUID NOT NULL REFERENCES telegram_messages(id),
    revision CHAR(64) NOT NULL,
    pair_token TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_price NUMERIC NOT NULL CHECK (entry_price > 0),
    stop_loss NUMERIC NOT NULL CHECK (stop_loss > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS canonical_signals_message_revision
ON canonical_signals (message_id, revision);

CREATE TABLE IF NOT EXISTS dispatches (
    id UUID PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id UUID NOT NULL,
    revision CHAR(64) NOT NULL,
    exchange TEXT NOT NULL CHECK (exchange IN ('binance', 'bitget')),
    state TEXT NOT NULL,
    claimed_by TEXT,
    lease_until TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    terminal_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_type, source_id, revision, exchange)
);

CREATE TABLE IF NOT EXISTS positions (
    id UUID PRIMARY KEY,
    exchange TEXT NOT NULL CHECK (exchange IN ('binance', 'bitget')),
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    protection_state TEXT NOT NULL CHECK (protection_state IN (
        'PENDING', 'VENUE_PROTECTED', 'BOT_FALLBACK', 'DEGRADED', 'FAILED'
    )),
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_position_per_venue_symbol
ON positions (exchange, symbol) WHERE closed_at IS NULL;

CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY,
    dispatch_id UUID REFERENCES dispatches(id),
    position_id UUID REFERENCES positions(id),
    exchange TEXT NOT NULL CHECK (exchange IN ('binance', 'bitget')),
    client_order_id TEXT NOT NULL,
    venue_order_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('ENTRY', 'SL', 'TP', 'CLOSE')),
    state TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (exchange, client_order_id),
    UNIQUE (exchange, venue_order_id)
);

CREATE TABLE IF NOT EXISTS notifications_outbox (
    id UUID PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    payload JSONB NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


CLAIM_DISPATCH_SQL = """
UPDATE dispatches
SET claimed_by = %(worker_id)s,
    lease_until = now() + (%(lease_seconds)s * interval '1 second'),
    attempts = attempts + 1,
    updated_at = now()
WHERE id = (
    SELECT id FROM dispatches
    WHERE state IN ('QUEUED', 'RETRY_WAIT')
      AND (claimed_by IS NULL OR lease_until <= now())
      AND exchange = %(exchange)s
    ORDER BY created_at, id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING *;
"""


def apply_initial_schema(cursor: SqlCursor) -> None:
    """Apply the initial schema inside the caller's transaction boundary."""
    cursor.execute(INITIAL_SCHEMA_SQL)
