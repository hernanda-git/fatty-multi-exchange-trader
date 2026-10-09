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
    has_media BOOLEAN NOT NULL DEFAULT FALSE,
    media_path TEXT,
    media_sha256 CHAR(64),
    media_mime_type TEXT,
    media_size_bytes INTEGER,
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
        "submitted",
        "partially_filled",
        "filled",
        "cancelled",
        "rejected",
        "unknown",
        "reconciled",
    }
)

ORDER_INTENT_ROLES: Final = frozenset({"ENTRY", "SL", "TP", "CLOSE", "EMERGENCY_CLOSE"})


def validate_order_intent_state(state: str) -> str:
    """Return ``state`` when it is a known live order-intent state.

    Raises:
        ValueError: If ``state`` is not one of ``ORDER_INTENT_STATES``.
    """
    if state not in ORDER_INTENT_STATES:
        raise ValueError(f"unknown order intent state: {state!r}")
    return state


BITGET_DISPATCH_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dispatch_transitions (
    id UUID PRIMARY KEY,
    dispatch_id UUID NOT NULL REFERENCES dispatches(id),
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS dispatch_transitions_dispatch_time
ON dispatch_transitions (dispatch_id, created_at);
"""


LIVE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS live_order_intents (
    id UUID PRIMARY KEY,
    exchange TEXT NOT NULL CHECK (exchange IN ('binance', 'bitget')),
    client_order_id TEXT NOT NULL,
    provider_order_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    role TEXT NOT NULL CONSTRAINT live_order_intents_role_check CHECK (
        role IN ('ENTRY', 'SL', 'TP', 'CLOSE', 'EMERGENCY_CLOSE')
    ),
    state TEXT NOT NULL CONSTRAINT live_order_intents_state_check CHECK (state IN (
        'staged', 'requested', 'acknowledged', 'submitted',
        'partially_filled', 'filled', 'cancelled', 'rejected', 'unknown', 'reconciled'
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
    planned_margin_usdt NUMERIC CHECK (planned_margin_usdt IS NULL OR planned_margin_usdt > 0),
    planned_notional_usdt NUMERIC CHECK (
        planned_notional_usdt IS NULL OR planned_notional_usdt > 0
    ),
    balance_snapshot_id UUID,
    margin_reservation_id UUID,
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

CREATE TABLE IF NOT EXISTS bitget_margin_reservations (
    id UUID PRIMARY KEY,
    exchange TEXT NOT NULL CHECK (exchange = 'bitget'),
    dispatch_id UUID NOT NULL REFERENCES dispatches(id),
    client_order_id TEXT NOT NULL,
    balance_snapshot_id UUID NOT NULL REFERENCES balance_snapshots(id),
    planned_margin_usdt NUMERIC NOT NULL CHECK (planned_margin_usdt > 0),
    state TEXT NOT NULL CHECK (state IN ('reserved', 'consumed', 'released', 'unknown')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ,
    resolution_reason TEXT,
    UNIQUE (exchange, client_order_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS bitget_margin_reservations_active_dispatch
ON bitget_margin_reservations (exchange, dispatch_id) WHERE state IN ('reserved', 'unknown');
CREATE INDEX IF NOT EXISTS bitget_margin_reservations_expiry
ON bitget_margin_reservations (exchange, state, expires_at);

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

CREATE TABLE IF NOT EXISTS source_management_updates (
    id UUID PRIMARY KEY,
    source_message_id UUID NOT NULL REFERENCES telegram_messages(id),
    revision TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('TP1_BOOKED', 'TP_BOOKED', 'SL_TO_ENTRY', 'CLOSE')),
    state TEXT NOT NULL CHECK (state IN (
        'queued', 'claimed', 'reconciliation-pending', 'failed',
        'reconciled', 'cancelled-flat', 'entries-cancelled'
    )),
    claimed_by TEXT,
    claimed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_message_id, revision, symbol, action)
);
CREATE INDEX IF NOT EXISTS source_management_updates_claim
ON source_management_updates (created_at, id) WHERE state = 'queued';

CREATE TABLE IF NOT EXISTS source_management_provider_intents (
    management_update_id UUID NOT NULL REFERENCES source_management_updates(id),
    client_order_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (management_update_id, client_order_id)
);

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


# Additive admission DDL; historical bootstrap text above is intentionally frozen.
DURABLE_ADMISSION_SCHEMA_SQL = """
ALTER TABLE bitget_margin_reservations ADD COLUMN IF NOT EXISTS symbol TEXT;
ALTER TABLE bitget_margin_reservations ADD COLUMN IF NOT EXISTS environment TEXT;
-- Freeze provenance for exact pre-admission bindings, never infer LIVE/DEMO.
-- The registry is migration-owned evidence, not a runtime admission write path.
LOCK TABLE live_order_intents, bitget_margin_reservations IN ACCESS EXCLUSIVE MODE;
CREATE TABLE IF NOT EXISTS admission_legacy_bindings (
    reservation_id UUID PRIMARY KEY REFERENCES bitget_margin_reservations(id),
    exchange TEXT NOT NULL,
    client_order_id TEXT NOT NULL,
    balance_snapshot_id UUID NOT NULL,
    symbol TEXT NOT NULL CHECK (btrim(symbol) <> ''),
    planned_margin_usdt NUMERIC NOT NULL,
    UNIQUE (reservation_id, exchange, client_order_id, balance_snapshot_id,
            symbol, planned_margin_usdt)
);
REVOKE INSERT, UPDATE, DELETE ON admission_legacy_bindings FROM PUBLIC;
INSERT INTO admission_legacy_bindings
    (reservation_id, exchange, client_order_id, balance_snapshot_id, symbol,
     planned_margin_usdt)
SELECT r.id, r.exchange, r.client_order_id, r.balance_snapshot_id, min(i.symbol),
       r.planned_margin_usdt
FROM bitget_margin_reservations r
JOIN live_order_intents i ON i.margin_reservation_id = r.id
WHERE r.symbol IS NULL AND r.environment IS NULL
GROUP BY r.id
HAVING count(DISTINCT i.symbol) = 1
   AND bool_and(i.role = 'ENTRY' AND i.exchange = r.exchange
       AND i.client_order_id = r.client_order_id
       AND i.balance_snapshot_id IS NOT NULL
       AND i.balance_snapshot_id = r.balance_snapshot_id
       AND i.planned_margin_usdt IS NOT NULL
       AND i.planned_margin_usdt = r.planned_margin_usdt
       AND btrim(i.symbol) <> '')
ON CONFLICT (reservation_id) DO NOTHING;
-- Escaped PL/pgSQL separators preserve the historical migration SQL splitter.
CREATE OR REPLACE FUNCTION reject_legacy_admission_provenance_write()
RETURNS trigger LANGUAGE plpgsql AS
E'BEGIN RAISE EXCEPTION ''immutable legacy admission provenance'' \
USING ERRCODE = ''23514''\\073 END\\073';
CREATE TRIGGER admission_legacy_provenance_immutable
BEFORE INSERT OR UPDATE OR DELETE ON admission_legacy_bindings
FOR EACH ROW EXECUTE FUNCTION reject_legacy_admission_provenance_write();
CREATE TRIGGER admission_legacy_provenance_no_truncate
BEFORE TRUNCATE ON admission_legacy_bindings
FOR EACH STATEMENT EXECUTE FUNCTION reject_legacy_admission_provenance_write();
ALTER TABLE bitget_margin_reservations ADD COLUMN IF NOT EXISTS legacy_binding_symbol TEXT;
ALTER TABLE bitget_margin_reservations ADD COLUMN IF NOT EXISTS binding_symbol TEXT
GENERATED ALWAYS AS (coalesce(symbol, legacy_binding_symbol)) STORED;
UPDATE bitget_margin_reservations r
SET legacy_binding_symbol = b.symbol
FROM admission_legacy_bindings b
WHERE r.id = b.reservation_id AND r.symbol IS NULL AND r.environment IS NULL
      AND r.legacy_binding_symbol IS NULL;
ALTER TABLE bitget_margin_reservations ADD CONSTRAINT admission_legacy_binding_provenance
FOREIGN KEY (id, exchange, client_order_id, balance_snapshot_id,
             legacy_binding_symbol, planned_margin_usdt)
REFERENCES admission_legacy_bindings
    (reservation_id, exchange, client_order_id, balance_snapshot_id, symbol,
     planned_margin_usdt);
ALTER TABLE bitget_margin_reservations ADD CONSTRAINT admission_symbol_environment
CHECK ((symbol IS NULL AND environment IS NULL) OR
       (symbol IS NOT NULL AND btrim(symbol) <> '' AND environment IS NOT NULL
        AND environment IN ('DEMO', 'LIVE')));
CREATE UNIQUE INDEX IF NOT EXISTS bitget_margin_reservations_symbol_owner
ON bitget_margin_reservations (exchange, environment, symbol)
WHERE state IN ('reserved', 'unknown', 'consumed');
"""


ADMISSION_CONSTRAINTS_SCHEMA_SQL = """
ALTER TABLE bitget_margin_reservations
ADD COLUMN IF NOT EXISTS has_exposure BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE live_order_intents ADD CONSTRAINT admission_planned_margin_finite
CHECK (planned_margin_usdt IS NULL OR
       (planned_margin_usdt > 0 AND planned_margin_usdt < 'Infinity'::numeric));
ALTER TABLE live_order_intents ADD CONSTRAINT admission_planned_notional_finite
CHECK (planned_notional_usdt IS NULL OR
       (planned_notional_usdt > 0 AND planned_notional_usdt < 'Infinity'::numeric));
ALTER TABLE bitget_margin_reservations ADD CONSTRAINT admission_reserved_margin_finite
CHECK (planned_margin_usdt > 0 AND planned_margin_usdt < 'Infinity'::numeric);
ALTER TABLE bitget_post_fill_reconciliations ADD CONSTRAINT admission_post_fill_planned_finite
CHECK (planned_margin_usdt > 0 AND planned_margin_usdt < 'Infinity'::numeric
       AND planned_leverage > 0 AND planned_leverage < 'Infinity'::numeric
       AND (planned_notional_usdt IS NULL OR
            (planned_notional_usdt > 0 AND planned_notional_usdt < 'Infinity'::numeric)));
ALTER TABLE balance_snapshots ADD CONSTRAINT admission_snapshot_exchange_key UNIQUE (id, exchange);
ALTER TABLE bitget_margin_reservations ADD CONSTRAINT admission_reservation_snapshot_exchange
FOREIGN KEY (balance_snapshot_id, exchange) REFERENCES balance_snapshots (id, exchange);
ALTER TABLE bitget_margin_reservations ADD CONSTRAINT admission_reservation_binding_key
UNIQUE (id, exchange, client_order_id, balance_snapshot_id, binding_symbol, planned_margin_usdt);
ALTER TABLE live_order_intents ADD CONSTRAINT admission_intent_binding_required
CHECK (margin_reservation_id IS NULL OR
       (role = 'ENTRY' AND exchange = 'bitget' AND balance_snapshot_id IS NOT NULL
        AND planned_margin_usdt IS NOT NULL));
ALTER TABLE live_order_intents ADD CONSTRAINT admission_intent_reservation_binding
FOREIGN KEY (margin_reservation_id, exchange, client_order_id,
             balance_snapshot_id, symbol, planned_margin_usdt)
REFERENCES bitget_margin_reservations
    (id, exchange, client_order_id, balance_snapshot_id, binding_symbol, planned_margin_usdt);
"""


def apply_live_schema(cursor: SqlCursor) -> None:
    """Apply the live-trading schema inside the caller's transaction boundary."""
    cursor.execute(LIVE_SCHEMA_SQL)
