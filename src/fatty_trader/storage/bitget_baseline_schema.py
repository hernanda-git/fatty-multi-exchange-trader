"""Additive audit-only retirement of explicitly approved historical uncertainty."""

BITGET_BASELINE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bitget_audited_baselines (
    id UUID PRIMARY KEY,
    exchange TEXT NOT NULL CHECK (exchange='bitget'),
    environment TEXT NOT NULL CHECK (environment IN ('LIVE','DEMO')),
    account_id TEXT NOT NULL CHECK (btrim(account_id) <> ''),
    approval_reference TEXT NOT NULL CHECK (btrim(approval_reference) <> ''),
    historical_before TIMESTAMPTZ NOT NULL,
    candidate_digest TEXT NOT NULL CHECK (length(candidate_digest)=64),
    provider_evidence JSONB NOT NULL,
    uncertainty_policy TEXT NOT NULL CHECK (
        uncertainty_policy='unresolved-history-not-verified-closure'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (environment, approval_reference)
);
CREATE TABLE IF NOT EXISTS bitget_baseline_records (
    baseline_id UUID NOT NULL REFERENCES bitget_audited_baselines(id),
    record_kind TEXT NOT NULL CHECK (record_kind IN ('intent','dispatch','reservation')),
    record_id UUID NOT NULL,
    prior_snapshot JSONB NOT NULL,
    PRIMARY KEY (record_kind, record_id)
);
REVOKE UPDATE, DELETE, TRUNCATE ON bitget_audited_baselines, bitget_baseline_records FROM PUBLIC;
CREATE OR REPLACE FUNCTION reject_bitget_baseline_mutation()
RETURNS trigger LANGUAGE plpgsql AS
E'BEGIN RAISE EXCEPTION ''immutable audited baseline'' \
USING ERRCODE = ''23514''\\073 END\\073';
CREATE TRIGGER bitget_audited_baselines_immutable
BEFORE UPDATE OR DELETE ON bitget_audited_baselines
FOR EACH ROW EXECUTE FUNCTION reject_bitget_baseline_mutation();
CREATE TRIGGER bitget_baseline_records_immutable
BEFORE UPDATE OR DELETE ON bitget_baseline_records
FOR EACH ROW EXECUTE FUNCTION reject_bitget_baseline_mutation();
CREATE TRIGGER bitget_audited_baselines_no_truncate
BEFORE TRUNCATE ON bitget_audited_baselines
FOR EACH STATEMENT EXECUTE FUNCTION reject_bitget_baseline_mutation();
CREATE TRIGGER bitget_baseline_records_no_truncate
BEFORE TRUNCATE ON bitget_baseline_records
FOR EACH STATEMENT EXECUTE FUNCTION reject_bitget_baseline_mutation();
ALTER TABLE live_order_intents ADD COLUMN IF NOT EXISTS planned_stop_loss NUMERIC;
ALTER TABLE live_order_intents ADD COLUMN IF NOT EXISTS planned_take_profits JSONB;
ALTER TABLE live_order_intents ADD CONSTRAINT intent_protection_plan_pair
CHECK ((planned_stop_loss IS NULL AND planned_take_profits IS NULL) OR
       (planned_stop_loss IS NOT NULL AND planned_stop_loss > 0
        AND planned_stop_loss < 'Infinity'::numeric
        AND planned_take_profits IS NOT NULL
        AND jsonb_typeof(planned_take_profits)='array'
        AND jsonb_array_length(planned_take_profits)>0));
"""
