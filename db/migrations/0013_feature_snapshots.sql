-- =============================================================================
-- 0013_feature_snapshots.sql — FIX-1 + FIX-3 (forensic audit remediation)
--
-- Two changes that together make training and drift detection statistically
-- honest:
--
--   FIX-1 (target leakage): a per-trade PRE-TRADE feature snapshot so the
--   retraining pipeline can train on what was known BEFORE trade entry instead
--   of leaking realized outcomes (actual_roi_pct / pnl_usd / units_sold) into
--   the feature matrix. The snapshot is stored on prediction_outcomes as a
--   JSONB column captured at execution time.
--
--   FIX-3 (synthetic drift baseline): the champion model's real training
--   feature distribution (per-feature mean + std) is recorded on
--   model_candidates at promotion time. DriftDetector compares live features
--   against THIS distribution instead of an arbitrary zeros/ones origin.
--
-- Run after 0012_shadow_models.sql. Idempotent.
-- =============================================================================

BEGIN;

-- FIX-1: pre-trade feature snapshot captured at trade entry.
-- A JSONB map of {feature_name: value} holding ONLY signals available before
-- the trade was entered (velocity_*, signal_count, author_diversity, sentiment,
-- commercial_intent, novelty, coordination_risk, …). Post-trade results must
-- never be written here.
ALTER TABLE prediction_outcomes
    ADD COLUMN IF NOT EXISTS feature_snapshot JSONB;

-- FIX-3: champion training feature distribution, recorded at promotion time.
-- JSONB arrays of length EVOLVE_FEATURE_DIM (24).
ALTER TABLE model_candidates
    ADD COLUMN IF NOT EXISTS training_feature_mean JSONB,
    ADD COLUMN IF NOT EXISTS training_feature_std  JSONB;

COMMIT;
