-- =============================================================================
-- 0016_trust.sql — PROJECT OMEGA Phase B (Trust Reconstruction)
--
-- Persists calibration snapshots and per-entity trust scores. These are
-- report-only artifacts in the initial rollout — nothing in decisioning reads
-- them until calibration is proven stable over >= 500 outcomes.
-- =============================================================================

CREATE TABLE IF NOT EXISTS calibration_snapshots (
    snapshot_id   UUID        NOT NULL DEFAULT gen_random_uuid(),
    tenant_id     UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    entity_kind   TEXT        NOT NULL DEFAULT 'model',  -- model|source|agent|global
    entity_id     TEXT        NOT NULL DEFAULT 'global',
    n             INT         NOT NULL DEFAULT 0,
    status        TEXT        NOT NULL DEFAULT 'ok',      -- ok|insufficient_*
    ece           DOUBLE PRECISION,
    mce           DOUBLE PRECISION,
    brier         DOUBLE PRECISION,
    brier_skill   DOUBLE PRECISION,
    base_rate     DOUBLE PRECISION,
    bins          JSONB       NOT NULL DEFAULT '[]',
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_id, computed_at)
);

SELECT create_hypertable(
    'calibration_snapshots', 'computed_at',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

ALTER TABLE calibration_snapshots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS calibration_snapshots_tenant ON calibration_snapshots;
CREATE POLICY calibration_snapshots_tenant ON calibration_snapshots
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);

CREATE INDEX IF NOT EXISTS idx_calib_entity
    ON calibration_snapshots (entity_kind, entity_id, computed_at DESC);


-- Current trust score per entity (latest wins; upsert by entity).
CREATE TABLE IF NOT EXISTS trust_scores (
    tenant_id     UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    entity_kind   TEXT        NOT NULL,                  -- model|source|agent
    entity_id     TEXT        NOT NULL,
    trust         DOUBLE PRECISION NOT NULL CHECK (trust BETWEEN 0 AND 1),
    n_outcomes    INT         NOT NULL DEFAULT 0,
    ece           DOUBLE PRECISION,
    brier_skill   DOUBLE PRECISION,
    base_rate     DOUBLE PRECISION,
    notes         TEXT        NOT NULL DEFAULT '',
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, entity_kind, entity_id)
);

ALTER TABLE trust_scores ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS trust_scores_tenant ON trust_scores;
CREATE POLICY trust_scores_tenant ON trust_scores
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);
