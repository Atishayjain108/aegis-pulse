-- =============================================================================
-- 0014_lin_ucb_state.sql — FIX-2 (forensic audit remediation)
--
-- Persistent storage for the real LinUCB contextual-bandit pricing policy
-- (aegis.evolve.rl_policy.LinUCBPricingPolicy), which replaces the fake
-- "REINFORCE" OnlinePricingPolicy. The full per-arm (A, b) parameter set is
-- serialised as JSONB so the bandit resumes learning across restarts instead
-- of cold-starting every process.
--
-- Run after 0013_feature_snapshots.sql. Idempotent.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS lin_ucb_state (
    policy_id        TEXT        PRIMARY KEY,
    -- {n_arms, context_dim, alpha, A: [[...]], b: [[...]]}
    state            JSONB       NOT NULL,
    update_count     BIGINT      NOT NULL DEFAULT 0,
    last_updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
