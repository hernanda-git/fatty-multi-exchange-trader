# ruff: noqa: E501
"""Additive schema for iOS/Web Push subscriptions and their delivery ledger.

Subscriptions are per-browser/device endpoints registered from the authenticated
console UI. Deliveries are one durable row per (notification, subscription) pair
so push transport failures retry independently of the Telegram outbox state.
"""

WEB_PUSH_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS web_push_subscriptions (
    id UUID PRIMARY KEY,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    disabled_at TIMESTAMPTZ,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS web_push_deliveries (
    notification_id UUID NOT NULL,
    subscription_id UUID NOT NULL REFERENCES web_push_subscriptions(id) ON DELETE CASCADE,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    next_attempt_at TIMESTAMPTZ,
    claimed_by TEXT,
    lease_until TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    last_error TEXT,
    PRIMARY KEY (notification_id, subscription_id)
);

CREATE INDEX IF NOT EXISTS web_push_deliveries_pending
    ON web_push_deliveries (created_at)
    WHERE sent_at IS NULL AND failed_at IS NULL;
"""
