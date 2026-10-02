"""Additive source-history coverage and entry eligibility metadata."""

INTAKE_COVERAGE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS telegram_catchup_coverage (
    channel_id BIGINT PRIMARY KEY,
    covered_message_id BIGINT NOT NULL CHECK (covered_message_id > 0)
);
INSERT INTO telegram_catchup_coverage (channel_id, covered_message_id)
SELECT channel_id, min(message_id) FROM telegram_messages GROUP BY channel_id
ON CONFLICT (channel_id) DO NOTHING;
ALTER TABLE telegram_messages
ADD COLUMN IF NOT EXISTS ingestion_origin TEXT NOT NULL DEFAULT 'legacy-unknown';
ALTER TABLE telegram_messages ADD COLUMN IF NOT EXISTS entry_expires_at TIMESTAMPTZ;
ALTER TABLE telegram_messages ADD COLUMN IF NOT EXISTS entry_rejection_reason TEXT;
UPDATE telegram_messages SET entry_expires_at = received_at + interval '5 minutes'
WHERE entry_expires_at IS NULL;
UPDATE telegram_messages
SET intake_state = 'EXPIRED', entry_rejection_reason = 'stale-source-message'
WHERE intake_state = 'RECEIVED' AND entry_expires_at <= now();
"""
