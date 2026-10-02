"""Additive close-to-entry identity; never reuses ENTRY admission columns."""

VERIFIED_CLOSE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bitget_verified_close_bindings (
    close_client_order_id TEXT PRIMARY KEY,
    exchange TEXT NOT NULL DEFAULT 'bitget' CHECK (exchange = 'bitget'),
    reservation_id UUID NOT NULL REFERENCES bitget_margin_reservations(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (exchange, close_client_order_id)
        REFERENCES live_order_intents(exchange, client_order_id)
);
"""
