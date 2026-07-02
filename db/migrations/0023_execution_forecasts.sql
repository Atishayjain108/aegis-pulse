-- =============================================================================
-- 0023_execution_forecasts.sql — PROJECT OMEGA Phase D (S5: Failure Prediction)
--
-- Rule 8: predict execution/supplier/buyer/logistics/compliance failure, STORE
-- the prediction, then MEASURE it against the settled outcome and learn.
--
-- This table stores the forecast made at recommendation time. The settled
-- truth lives in execution_records (migration 0021); ForecastAccuracy joins the
-- two on plan_id to compute Brier skill. Nothing here is settled truth — it is
-- a recorded prediction, scored later (no look-ahead).
--
-- Additive only — reversible by dropping the table.
-- =============================================================================

CREATE TABLE IF NOT EXISTS execution_forecasts (
    forecast_id    UUID        NOT NULL DEFAULT gen_random_uuid(),
    tenant_id      UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',

    plan_id        TEXT        NOT NULL,
    -- Per-mode P(failure) in [0,1]: inventory/supplier/shipping/compliance/
    -- payment/demand. Plus a derived p_any_failure for quick Brier scoring.
    probabilities  JSONB       NOT NULL DEFAULT '{}',
    p_any_failure  REAL,
    abstained      BOOLEAN     NOT NULL DEFAULT FALSE,

    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (forecast_id)
);

ALTER TABLE execution_forecasts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS execution_forecasts_tenant ON execution_forecasts;
CREATE POLICY execution_forecasts_tenant ON execution_forecasts
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);

-- One scored forecast per plan (idempotent record_forecast).
CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_forecasts_plan
    ON execution_forecasts (tenant_id, plan_id);
CREATE INDEX IF NOT EXISTS idx_execution_forecasts_created
    ON execution_forecasts (created_at);
