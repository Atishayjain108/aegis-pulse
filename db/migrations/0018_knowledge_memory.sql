-- =============================================================================
-- 0018_knowledge_memory.sql — PROJECT OMEGA Phase C (Knowledge Expansion)
--
-- Stage 1 slice: the durable OPPORTUNITY ledger + FAILURE intelligence.
--
-- Doctrine (Knowledge First / Reality Gate): every row in these tables is
-- derived from a SETTLED ground-truth outcome (signal_outcomes or
-- prediction_outcomes). Nothing here is invented from an unsettled prediction.
-- A failure row exists ONLY for an opportunity whose outcome = 'failed'.
--
-- Purely additive — no existing table is touched. Reversible by dropping the
-- two tables below.
--
-- Later Phase C stages add: entities, entity_outcomes, source_profiles,
-- market_epochs, knowledge_edges (separate migration, not shipped in this slice).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- opportunities — the permanent opportunity memory (Rule 2)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS opportunities (
    opportunity_id        UUID        NOT NULL DEFAULT gen_random_uuid(),
    tenant_id             UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',

    -- Unified global-opportunity taxonomy (Rule 9). Signal-trend claims map to
    -- 'info_arb'; geo/product/etc. opportunities use their own type later.
    opportunity_type      TEXT        NOT NULL DEFAULT 'info_arb',
    category              TEXT        NOT NULL DEFAULT 'general',
    region                TEXT,

    -- Re-observable handle linking back to the ground-truth row.
    trend_key             TEXT        NOT NULL,
    prediction_id         TEXT        NOT NULL,
    source_signal_ids     JSONB       NOT NULL DEFAULT '[]',
    evidence              JSONB       NOT NULL DEFAULT '{}',

    -- Verification scores (filled by the Reality layer in a later stage; NULL now).
    trust_score           REAL,
    reality_score         REAL,
    evidence_score        REAL,
    unknowns_score        REAL,

    -- What the model committed to.
    prediction_direction  TEXT,
    prediction_score      REAL,
    prediction_confidence REAL,

    -- Settled truth.
    outcome               TEXT        NOT NULL DEFAULT 'pending'
                                      CHECK (outcome IN ('pending','realized','failed','expired')),
    failure_id            UUID,                       -- set when outcome='failed'
    realization_path      JSONB       NOT NULL DEFAULT '{}',
    decay_horizon_hours   INT         NOT NULL DEFAULT 72,

    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settled_at            TIMESTAMPTZ,
    settlement_timestamp  TIMESTAMPTZ NOT NULL DEFAULT NOW(),   -- hypertable key
    metadata              JSONB       NOT NULL DEFAULT '{}',
    PRIMARY KEY (opportunity_id, settlement_timestamp)
);

SELECT create_hypertable(
    'opportunities', 'settlement_timestamp',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

ALTER TABLE opportunities ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS opportunities_tenant ON opportunities;
CREATE POLICY opportunities_tenant ON opportunities
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);

-- Idempotent backfill: one opportunity per (prediction_id, trend_key).
CREATE UNIQUE INDEX IF NOT EXISTS uq_opportunities_claim
    ON opportunities (tenant_id, prediction_id, trend_key, settlement_timestamp);
CREATE INDEX IF NOT EXISTS idx_opportunities_type_outcome
    ON opportunities (opportunity_type, outcome);
CREATE INDEX IF NOT EXISTS idx_opportunities_trend
    ON opportunities (trend_key);

-- -----------------------------------------------------------------------------
-- failures — failure intelligence (Rule 5)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS failures (
    failure_id            UUID        NOT NULL DEFAULT gen_random_uuid(),
    tenant_id             UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',

    opportunity_id        UUID        NOT NULL,
    trend_key             TEXT        NOT NULL,
    prediction_id         TEXT        NOT NULL,

    failure_category      TEXT        NOT NULL DEFAULT 'unclassified',
    root_cause            TEXT        NOT NULL DEFAULT '',
    evidence_quality      REAL,                       -- [0,1]; how strong the evidence was
    missing_information   JSONB       NOT NULL DEFAULT '[]',
    confidence_error      REAL,                       -- predicted_conf - observed_correctness

    detected_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),     -- hypertable key
    metadata              JSONB       NOT NULL DEFAULT '{}',
    PRIMARY KEY (failure_id, detected_at)
);

SELECT create_hypertable(
    'failures', 'detected_at',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

ALTER TABLE failures ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS failures_tenant ON failures;
CREATE POLICY failures_tenant ON failures
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);

CREATE UNIQUE INDEX IF NOT EXISTS uq_failures_claim
    ON failures (tenant_id, prediction_id, trend_key, detected_at);
CREATE INDEX IF NOT EXISTS idx_failures_category
    ON failures (failure_category);
