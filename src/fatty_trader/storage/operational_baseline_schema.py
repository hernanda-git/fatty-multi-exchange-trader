"""Append-only owner-approved operational baseline, never historical retirement."""

OPERATIONAL_BASELINE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bitget_operational_baselines (
    id UUID PRIMARY KEY,
    account_identity TEXT NOT NULL CHECK (length(trim(account_identity)) > 0),
    api_key_sha256 TEXT NOT NULL CHECK (api_key_sha256 ~ '^[0-9a-f]{64}$'),
    approval_reference TEXT NOT NULL CHECK (length(trim(approval_reference)) > 0),
    exchange TEXT NOT NULL DEFAULT 'bitget' CHECK (exchange = 'bitget'),
    environment TEXT NOT NULL DEFAULT 'LIVE' CHECK (environment = 'LIVE'),
    history_status TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (history_status = 'UNVERIFIED'),
    captured_rows JSONB NOT NULL CHECK (jsonb_typeof(captured_rows) = 'array'
        AND jsonb_array_length(captured_rows) = 25),
    created_global_inactive BOOLEAN NOT NULL DEFAULT false,
    prepared_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE OR REPLACE RULE baseline_no_update AS ON UPDATE TO bitget_operational_baselines
    DO INSTEAD NOTHING;
CREATE OR REPLACE RULE baseline_no_delete AS ON DELETE TO bitget_operational_baselines
    DO INSTEAD NOTHING;
CREATE OR REPLACE VIEW bitget_operational_baseline_rows AS
    SELECT b.id AS baseline_id, r->>'kind' AS kind, (r->>'id')::uuid AS row_id,
           r->'snapshot' AS snapshot
    FROM bitget_operational_baselines b, jsonb_array_elements(b.captured_rows) r;
CREATE OR REPLACE RULE baseline_rows_no_delete AS
    ON DELETE TO bitget_operational_baseline_rows DO INSTEAD NOTHING;
CREATE TABLE IF NOT EXISTS bitget_operational_baseline_activations (
    baseline_id UUID PRIMARY KEY REFERENCES bitget_operational_baselines(id),
    epoch_id UUID NOT NULL UNIQUE,
    exchange TEXT NOT NULL DEFAULT 'bitget' UNIQUE CHECK (exchange = 'bitget'),
    activated_at TIMESTAMPTZ NOT NULL,
    source_cutoff TIMESTAMPTZ NOT NULL CHECK (source_cutoff = activated_at),
    proof JSONB NOT NULL
);
CREATE OR REPLACE RULE baseline_activation_no_update AS
    ON UPDATE TO bitget_operational_baseline_activations DO INSTEAD NOTHING;
CREATE OR REPLACE RULE baseline_activation_no_delete AS
    ON DELETE TO bitget_operational_baseline_activations DO INSTEAD NOTHING;
CREATE TABLE IF NOT EXISTS bitget_operational_baseline_incident_releases (
    baseline_id UUID NOT NULL REFERENCES bitget_operational_baseline_activations(baseline_id),
    scope TEXT NOT NULL CHECK (scope IN ('bitget','bitget-protection-stream')),
    prior_latch JSONB NOT NULL,
    released_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (baseline_id,scope)
);
CREATE OR REPLACE RULE baseline_release_no_update AS
    ON UPDATE TO bitget_operational_baseline_incident_releases DO INSTEAD NOTHING;
CREATE OR REPLACE RULE baseline_release_no_delete AS
    ON DELETE TO bitget_operational_baseline_incident_releases DO INSTEAD NOTHING;
CREATE TABLE IF NOT EXISTS bitget_operational_baseline_queue_retirements (
    baseline_id UUID NOT NULL REFERENCES bitget_operational_baseline_activations(baseline_id),
    kind TEXT NOT NULL CHECK (kind IN ('dispatch','management')),
    row_id UUID NOT NULL,
    prior_row JSONB NOT NULL,
    retired_at TIMESTAMPTZ NOT NULL,
    reason TEXT NOT NULL DEFAULT 'operational-baseline-cutoff'
        CHECK (reason='operational-baseline-cutoff'),
    PRIMARY KEY (baseline_id,kind,row_id)
);
CREATE OR REPLACE RULE baseline_queue_no_update AS
    ON UPDATE TO bitget_operational_baseline_queue_retirements DO INSTEAD NOTHING;
CREATE OR REPLACE RULE baseline_queue_no_delete AS
    ON DELETE TO bitget_operational_baseline_queue_retirements DO INSTEAD NOTHING;
CREATE OR REPLACE VIEW bitget_operational_baseline_exclusions AS
    SELECT r.* FROM bitget_operational_baseline_rows r
    JOIN bitget_operational_baseline_activations a ON a.baseline_id=r.baseline_id
    JOIN bitget_operational_baselines b ON b.id=a.baseline_id
    WHERE b.account_identity=current_setting('fatty.baseline_uid',true)
      AND b.api_key_sha256=current_setting('fatty.baseline_key',true)
      AND NOT EXISTS (
        SELECT 1 FROM bitget_operational_baseline_rows x
        LEFT JOIN live_order_intents i ON x.kind='intent' AND i.id=x.row_id
        LEFT JOIN bitget_margin_reservations m ON x.kind='reservation' AND m.id=x.row_id
        LEFT JOIN dispatches d ON x.kind='dispatch' AND d.id=x.row_id
        WHERE x.baseline_id=r.baseline_id AND NOT COALESCE(
            CASE x.kind
                WHEN 'intent' THEN (to_jsonb(i) - 'updated_at')=x.snapshot
                WHEN 'reservation' THEN (to_jsonb(m) - 'updated_at')=x.snapshot
                WHEN 'dispatch' THEN (to_jsonb(d) - 'updated_at')=x.snapshot
                ELSE false END, false)
    );
"""
