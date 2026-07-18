-- =============================================================================
-- 0012_shadow_models.sql — PASS3-3C shadow model deployment
--
-- Adds shadow-deployment tracking to model_candidates: a retrained candidate
-- that beats the champion is first registered as a shadow (is_shadow = TRUE)
-- and only promoted after a 72-hour parallel evaluation window confirms the
-- improvement on fresh outcomes (see aegis.evolve.retrain.evaluate_shadows).
--
-- Run after 0011_evolve.sql.
-- =============================================================================

BEGIN;

ALTER TABLE model_candidates
    ADD COLUMN IF NOT EXISTS is_shadow BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS shadow_registered_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_model_candidates_shadow
    ON model_candidates (is_shadow, shadow_registered_at)
    WHERE is_shadow = TRUE;

-- The retrain audit row now records the shadow outcome as its own status.
ALTER TABLE retrain_audit
    DROP CONSTRAINT IF EXISTS retrain_audit_status_check;
ALTER TABLE retrain_audit
    ADD CONSTRAINT retrain_audit_status_check
    CHECK (status IN ('running', 'completed', 'failed', 'no_improvement', 'shadow_deployed'));

COMMIT;
