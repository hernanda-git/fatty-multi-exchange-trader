"""Additive fallback identity DDL. Legacy ownership is deliberately not backfilled."""

FALLBACK_OWNERSHIP_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fallback_protection (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    exchange text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    entry_price numeric NOT NULL,
    stop_loss numeric NOT NULL,
    take_profits jsonb NOT NULL DEFAULT '[]'::jsonb,
    quantity numeric NOT NULL,
    position_key text,
    state text NOT NULL DEFAULT 'active',
    close_price numeric,
    close_reason text,
    close_order_id text,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fallback_protection_active
    ON fallback_protection (exchange, symbol) WHERE state = 'active';
ALTER TABLE fallback_protection ADD COLUMN IF NOT EXISTS position_key text;
CREATE UNIQUE INDEX IF NOT EXISTS fallback_protection_active_key
    ON fallback_protection (position_key)
    WHERE state IN ('active', 'closing') AND position_key IS NOT NULL;

ALTER TABLE fallback_protection ADD COLUMN IF NOT EXISTS provider_position_epoch text
    CHECK (provider_position_epoch IS NULL OR provider_position_epoch ~ '^[1-9][0-9]*$');
ALTER TABLE fallback_protection ADD COLUMN IF NOT EXISTS environment text
    CHECK (environment IS NULL OR environment IN ('DEMO', 'LIVE'));
"""
