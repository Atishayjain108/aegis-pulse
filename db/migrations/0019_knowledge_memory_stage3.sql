-- =============================================================================
-- 0019_knowledge_memory_stage3.sql — PROJECT OMEGA Phase C (Stage 3)
--
-- Source Memory (Rule 4): per-platform reliability derived from SETTLED outcomes.
-- Extends the single scalar in trust_scores with freshness + reliability +
-- per-category accuracy, all computed from ground truth — never asserted.
--
-- Fields that cannot yet be measured honestly (manipulation_risk,
-- per_category_accuracy) are NULLABLE and left NULL rather than faked (Rule 1).
--
-- Additive only — reversible by dropping the table. The RealityVerifier
-- (Stage 3) reads this table + the opportunities ledger; it adds no schema.
-- =============================================================================

CREATE TABLE IF NOT EXISTS source_profiles (
    tenant_id             UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    source_id             TEXT        NOT NULL,          -- platform / adapter name

    trust                 REAL        NOT NULL DEFAULT 0.3,   -- from compute_trust
    reliability_score     REAL,                               -- settled-correct rate
    freshness_score       REAL,                               -- recency of useful signals
    manipulation_risk     REAL,                               -- NULL = not yet measured
    per_category_accuracy JSONB       NOT NULL DEFAULT '{}',

    n_signals             INT         NOT NULL DEFAULT 0,
    n_outcomes            INT         NOT NULL DEFAULT 0,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, source_id)
);

ALTER TABLE source_profiles ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS source_profiles_tenant ON source_profiles;
CREATE POLICY source_profiles_tenant ON source_profiles
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);

CREATE INDEX IF NOT EXISTS idx_source_profiles_trust
    ON source_profiles (trust DESC);
