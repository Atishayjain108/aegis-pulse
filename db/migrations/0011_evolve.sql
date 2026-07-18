-- Phase 9: Autonomous Self-Evolution
-- Adds tables for: trade outcome recording, model candidates, drift snapshots, RL policy state.

-- ---------------------------------------------------------------------------
-- Trade outcomes — ground truth labels for model retraining
-- ---------------------------------------------------------------------------
-- FRESH-REPLAY FIX (2026-07-17, integration CI): 0001_init.sql ships an
-- OLDER, different-shaped prediction_outcomes ("feedback intelligence",
-- hypertable on made_at, no trend_id). On a fresh database that table
-- survives, the CREATE IF NOT EXISTS below silently skips, and the
-- trend_id index at the bottom of this section fails —
-- `UndefinedColumnError: column "trend_id" does not exist` (first caught by
-- the integration workflow's from-zero replay; production never hit it
-- because its 0001-shaped table was dropped out-of-band long ago, which is
-- itself recorded drift — see RECOVERY_PROTOCOL.md DEBT-6).
-- The 0001 scaffold is dead-on-arrival: nothing between 0002 and 0010
-- writes to it. Dropping it here is a no-op on every database where 0011
-- is already recorded as applied.
DROP TABLE IF EXISTS prediction_outcomes;
CREATE TABLE IF NOT EXISTS prediction_outcomes (
    outcome_id              UUID        NOT NULL DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    execution_plan_id       TEXT        NOT NULL,
    trend_id                TEXT        NOT NULL,
    prediction_score        REAL        NOT NULL CHECK (prediction_score BETWEEN 0 AND 1),
    prediction_confidence   REAL        NOT NULL CHECK (prediction_confidence BETWEEN 0 AND 1),
    actual_roi_pct          NUMERIC(10, 4),
    pnl_usd                 NUMERIC(14, 4),
    units_sold              INT         NOT NULL DEFAULT 0,
    units_returned          INT         NOT NULL DEFAULT 0,
    settlement_timestamp    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolution_status       TEXT        NOT NULL DEFAULT 'pending'
                                        CHECK (resolution_status IN (
                                            'successful', 'partial_refund',
                                            'full_refund', 'dispute', 'pending'
                                        )),
    metadata                JSONB       NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (outcome_id, settlement_timestamp)
);

-- Make it a TimescaleDB hypertable (partitioned on settlement_timestamp)
SELECT create_hypertable(
    'prediction_outcomes', 'settlement_timestamp',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- RLS
ALTER TABLE prediction_outcomes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prediction_outcomes_tenant ON prediction_outcomes;
CREATE POLICY prediction_outcomes_tenant ON prediction_outcomes
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);

CREATE INDEX IF NOT EXISTS idx_prediction_outcomes_trend
    ON prediction_outcomes (trend_id, settlement_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_prediction_outcomes_plan
    ON prediction_outcomes (execution_plan_id);
CREATE INDEX IF NOT EXISTS idx_prediction_outcomes_status
    ON prediction_outcomes (resolution_status, settlement_timestamp DESC);

-- ---------------------------------------------------------------------------
-- Model candidates — training run results for champion comparison
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_candidates (
    candidate_id        TEXT        NOT NULL PRIMARY KEY,
    tenant_id           UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    architecture        TEXT        NOT NULL,
    train_auc           REAL        NOT NULL DEFAULT 0.5,
    val_auc             REAL        NOT NULL DEFAULT 0.5,
    test_auc            REAL        NOT NULL DEFAULT 0.5,
    test_precision      REAL        NOT NULL DEFAULT 0.5,
    test_recall         REAL        NOT NULL DEFAULT 0.5,
    test_f1             REAL        NOT NULL DEFAULT 0.5,
    training_duration_s REAL        NOT NULL DEFAULT 0,
    artifact_path       TEXT,
    hyperparameters     JSONB       NOT NULL DEFAULT '{}',
    is_champion         BOOLEAN     NOT NULL DEFAULT FALSE,
    promoted_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_model_candidates_champion
    ON model_candidates (is_champion, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_candidates_arch
    ON model_candidates (architecture, test_auc DESC);

-- ---------------------------------------------------------------------------
-- Drift snapshots — daily drift monitoring history
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS drift_snapshots (
    snapshot_id     UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    drift_score     REAL        NOT NULL CHECK (drift_score BETWEEN 0 AND 1),
    is_drifted      BOOLEAN     NOT NULL DEFAULT FALSE,
    feature_stats   JSONB       NOT NULL DEFAULT '{}',
    precision_now   REAL,
    precision_prev  REAL,
    precision_drop  REAL,
    should_rollback BOOLEAN     NOT NULL DEFAULT FALSE,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_drift_snapshots_time
    ON drift_snapshots (captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_drift_snapshots_drifted
    ON drift_snapshots (is_drifted, captured_at DESC) WHERE is_drifted = TRUE;

-- ---------------------------------------------------------------------------
-- RL policy state — persisted pricing policy weights
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rl_policy_state (
    policy_id       TEXT        NOT NULL PRIMARY KEY DEFAULT 'default',
    tenant_id       UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    weights         JSONB       NOT NULL DEFAULT '[0.25, 0.35, 0.20, 0.20]',
    update_count    INT         NOT NULL DEFAULT 0,
    learning_rate   REAL        NOT NULL DEFAULT 0.01,
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed default policy row
INSERT INTO rl_policy_state (policy_id, weights, update_count)
VALUES ('default', '[0.25, 0.35, 0.20, 0.20]', 0)
ON CONFLICT (policy_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Retraining audit log — record every weekly retrain attempt
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS retrain_audit (
    run_id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    triggered_by    TEXT        NOT NULL DEFAULT 'scheduler'
                                CHECK (triggered_by IN ('scheduler', 'drift', 'manual')),
    outcomes_count  INT         NOT NULL DEFAULT 0,
    candidates      JSONB       NOT NULL DEFAULT '[]',
    champion_before TEXT,
    champion_after  TEXT,
    improvement_pct REAL,
    status          TEXT        NOT NULL DEFAULT 'running'
                                CHECK (status IN ('running', 'completed', 'failed', 'no_improvement')),
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_retrain_audit_time
    ON retrain_audit (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_retrain_audit_status
    ON retrain_audit (status, started_at DESC);
