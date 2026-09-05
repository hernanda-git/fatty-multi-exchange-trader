"""Initial PostgreSQL DDL for durable, fail-closed trading state.

Live-trading tables (``LIVE_SCHEMA_SQL``) ship as migration v1 in
``fatty_trader.storage.migrations``; the v0 ``INITIAL_SCHEMA_SQL`` below is
frozen so already-deployed databases can migrate forward without data loss.
"""

from typing import Final, Protocol


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


ORDER_INTENT_STATES: Final = frozenset(
    {
        "requested",
        "acknowledged",
        "filled",
        "cancelled",
        "rejected",
        "unknown",
        "reconciled",
    }
)

ORDER_INTENT_ROLES: Final = frozenset({"ENTRY", "SL", "TP", "CLOSE"})


def validate_order_intent_state(state: str) -> str:
    """Return ``state`` when it is a known live order-intent state.

    Raises:
        ValueError: If ``state`` is not one of ``ORDER_INTENT_STATES``.
    """
    if state not in ORDER_INTENT_STATES:
        raise ValueError(f"unknown order intent state: {state!r}")
    return state


LIVE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS live_order_intents (
    id UUID PRIMARY KEY,
    exchange TEXT NOT NULL CHECK (exchange IN ('binance', 'bitget')),
    client_order_id TEXT NOT NULL,
    provider_order_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    role TEXT NOT NULL CHECK (role IN ('ENTRY', 'SL', 'TP', 'CLOSE')),
    state TEXT NOT NULL CHECK (state IN (
        'requested', 'acknowledged', 'filled', 'cancelled',
        'rejected', 'unknown', 'reconciled'
    )),
    requested_qty NUMERIC NOT NULL CHECK (requested_qty > 0),
    acknowledged_qty NUMERIC CHECK (acknowledged_qty IS NULL OR acknowledged_qty > 0),
    filled_qty NUMERIC NOT NULL DEFAULT 0 CHECK (filled_qty >= 0),
    requested_price NUMERIC CHECK (requested_price IS NULL OR requested_price > 0),
    acknowledged_price NUMERIC CHECK (acknowledged_price IS NULL OR acknowledged_price > 0),
    filled_price NUMERIC CHECK (filled_price IS NULL OR filled_price > 0),
    fee NUMERIC NOT NULL DEFAULT 0,
    provider_fill_ids JSONB NOT NULL DEFAULT '[]',
    leverage NUMERIC CHECK (leverage IS NULL OR leverage > 0),
    margin_mode TEXT CHECK (margin_mode IS NULL OR margin_mode IN ('ISOLATED', 'CROSS')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (exchange, client_order_id),
    UNIQUE (exchange, provider_order_id)
);

CREATE TABLE IF NOT EXISTS fills (
    id UUID PRIMARY KEY,
    exchange TEXT NOT NULL CHECK (exchange IN ('binance', 'bitget')),
    client_order_id TEXT NOT NULL,
    provider_fill_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    price NUMERIC NOT NULL CHECK (price > 0),
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    fee NUMERIC NOT NULL DEFAULT 0,
    fee_ccy TEXT,
    realized_pnl NUMERIC NOT NULL DEFAULT 0,
    filled_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (exchange, provider_fill_id),
    FOREIGN KEY (exchange, client_order_id)
        REFERENCES live_order_intents (exchange, client_order_id)
);
CREATE INDEX IF NOT EXISTS fills_intent_lookup
ON fills (exchange, client_order_id);

CREATE TABLE IF NOT EXISTS balance_snapshots (
    id UUID PRIMARY KEY,
    exchange TEXT NOT NULL CHECK (exchange IN ('binance', 'bitget')),
    total_balance NUMERIC NOT NULL,
    available_balance NUMERIC NOT NULL,
    equity NUMERIC NOT NULL,
    margin_coin TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS balance_snapshots_exchange_time
ON balance_snapshots (exchange, captured_at);

CREATE TABLE IF NOT EXISTS position_snapshots (
    id UUID PRIMARY KEY,
    exchange TEXT NOT NULL CHECK (exchange IN ('binance', 'bitget')),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    size NUMERIC NOT NULL,
    entry_price NUMERIC CHECK (entry_price IS NULL OR entry_price > 0),
    mark_price NUMERIC CHECK (mark_price IS NULL OR mark_price > 0),
    liquidation_price NUMERIC CHECK (liquidation_price IS NULL OR liquidation_price > 0),
    leverage NUMERIC CHECK (leverage IS NULL OR leverage > 0),
    margin_mode TEXT CHECK (margin_mode IS NULL OR margin_mode IN ('ISOLATED', 'CROSS')),
    unrealized_pnl NUMERIC NOT NULL DEFAULT 0,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS position_snapshots_exchange_symbol_time
ON position_snapshots (exchange, symbol, captured_at);

CREATE TABLE IF NOT EXISTS protection_states (
    id UUID PRIMARY KEY,
    position_id UUID REFERENCES positions(id),
    order_ref TEXT,
    sl_order_id TEXT,
    tp_order_id TEXT,
    state TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (position_id)
);

CREATE TABLE IF NOT EXISTS reconciliation_state (
    scope TEXT PRIMARY KEY,
    last_run_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    mismatch_count INTEGER NOT NULL DEFAULT 0 CHECK (mismatch_count >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def apply_live_schema(cursor: SqlCursor) -> None:
    """Apply the live-trading schema inside the caller's transaction boundary."""
    cursor.execute(LIVE_SCHEMA_SQL)
