-- =============================================================================
-- 0015_signal_outcomes.sql — PROJECT OMEGA Phase A (Reality Loop Activation)
--
-- The capital trade-outcome table (prediction_outcomes) only fills when a real
-- Phase 6 trade settles. In advisory mode (the default) zero trades execute, so
-- the learning loop never closes. This migration adds a SECOND, capital-free
-- ground-truth stream: self-supervised "falsifiable claims" about a trend's
-- re-observable signal trajectory.
--
-- A prediction commits to a falsifiable claim ("this trend's signal COUNT over
-- the next H hours will RISE / FALL / FLAT"). The claim is persisted with
-- resolution_status='pending'. After the horizon elapses the SignalOutcomeSettler
-- re-reads the signals table, observes what actually happened, and settles the
-- row to 'correct' / 'incorrect'. No capital, no fulfillment, no manual step.
--
-- This table is deliberately SEPARATE from prediction_outcomes so capital trade
-- truth and self-supervised signal truth never contaminate each other. The
-- weekly learners read the UNION via their own queries.
-- =============================================================================

CREATE TABLE IF NOT EXISTS signal_outcomes (
    outcome_id              UUID        NOT NULL DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',

    -- The prediction that made the claim (may be a synthetic id for backfill).
    prediction_id           TEXT        NOT NULL,
    -- Re-observable handle: the trend tag / topic the claim is about.
    trend_key               TEXT        NOT NULL,

    -- What the model committed to at prediction time.
    metric                  TEXT        NOT NULL DEFAULT 'signal_count'
                                        CHECK (metric IN ('signal_count', 'velocity')),
    claimed_direction       TEXT        NOT NULL
                                        CHECK (claimed_direction IN ('rise', 'fall', 'flat')),
    prediction_score        REAL        NOT NULL CHECK (prediction_score BETWEEN 0 AND 1),
    prediction_confidence   REAL        NOT NULL CHECK (prediction_confidence BETWEEN 0 AND 1),

    -- Baseline measured strictly from data known at/before claim_ts (no look-ahead).
    baseline_value          DOUBLE PRECISION NOT NULL DEFAULT 0,
    horizon_hours           INT         NOT NULL DEFAULT 72 CHECK (horizon_hours > 0),
    claim_ts                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settle_after            TIMESTAMPTZ NOT NULL,

    -- Filled at settlement time.
    observed_value          DOUBLE PRECISION,
    observed_direction      TEXT        CHECK (observed_direction IN ('rise', 'fall', 'flat')),
    settled_at              TIMESTAMPTZ,
    settlement_timestamp    TIMESTAMPTZ NOT NULL DEFAULT NOW(),  -- hypertable partition key

    resolution_status       TEXT        NOT NULL DEFAULT 'pending'
                                        CHECK (resolution_status IN (
                                            'pending', 'correct', 'incorrect'
                                        )),
    metadata                JSONB       NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (outcome_id, settlement_timestamp)
);

-- Hypertable partitioned on settlement_timestamp (same pattern as prediction_outcomes).
SELECT create_hypertable(
    'signal_outcomes', 'settlement_timestamp',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- RLS — same tenant pattern as every other table.
ALTER TABLE signal_outcomes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS signal_outcomes_tenant ON signal_outcomes;
CREATE POLICY signal_outcomes_tenant ON signal_outcomes
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);

-- Idempotency: one live claim per (prediction_id, trend_key, metric).
CREATE UNIQUE INDEX IF NOT EXISTS uq_signal_outcomes_claim
    ON signal_outcomes (prediction_id, trend_key, metric, settlement_timestamp);

-- The settler's hot query: find pending claims whose horizon has elapsed.
CREATE INDEX IF NOT EXISTS idx_signal_outcomes_pending
    ON signal_outcomes (resolution_status, settle_after)
    WHERE resolution_status = 'pending';

CREATE INDEX IF NOT EXISTS idx_signal_outcomes_settled
    ON signal_outcomes (resolution_status, settlement_timestamp DESC);
