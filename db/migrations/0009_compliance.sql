-- Phase 8: Regulatory & Compliance Engine
-- Migration: 0009_compliance.sql
--
-- Tables:
--   compliance_assessments     — full assessment records (immutable audit trail)
--   compliance_blocks          — blocked executions (for reporting / appeals)
--   trademark_cache            — local cache for trademark lookup results
--   sanction_hits              — OFAC / FATF matches (regulatory record)
--
-- All tables use TimescaleDB hypertables where applicable.
-- All tables enforce RLS via app.current_tenant.

-- ---------------------------------------------------------------------------
-- Enable required extensions (idempotent)
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- compliance_assessments
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS compliance_assessments (
    assessment_id       UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id           UUID        NOT NULL,
    product_sku         TEXT        NOT NULL,
    product_title       TEXT        NOT NULL,
    origin_country      CHAR(3)     NOT NULL DEFAULT 'US',
    destination_country CHAR(3)     NOT NULL DEFAULT 'US',
    category            TEXT        NOT NULL DEFAULT 'general',

    -- Risk scores (0.0–1.0)
    overall_risk_score  DECIMAL(5,4) NOT NULL CHECK (overall_risk_score BETWEEN 0 AND 1),
    trademark_risk      DECIMAL(5,4) NOT NULL DEFAULT 0,
    patent_risk         DECIMAL(5,4) NOT NULL DEFAULT 0,
    fda_risk            DECIMAL(5,4) NOT NULL DEFAULT 0,
    counterfeit_risk    DECIMAL(5,4) NOT NULL DEFAULT 0,
    ftc_risk            DECIMAL(5,4) NOT NULL DEFAULT 0,
    privacy_risk        DECIMAL(5,4) NOT NULL DEFAULT 0,
    aml_risk            DECIMAL(5,4) NOT NULL DEFAULT 0,

    -- Decision
    recommendation      TEXT        NOT NULL CHECK (recommendation IN ('PROCEED','ESCALATE','BLOCK')),
    reasons             JSONB       NOT NULL DEFAULT '[]',

    -- Detail records (embedded JSON for immutability)
    trademark_matches   JSONB       NOT NULL DEFAULT '[]',
    patent_matches      JSONB       NOT NULL DEFAULT '[]',
    fda_enforcements    JSONB       NOT NULL DEFAULT '[]',
    sanction_matches    JSONB       NOT NULL DEFAULT '[]',
    counterfeit_signals JSONB       NOT NULL DEFAULT '[]',
    ftc_violations      JSONB       NOT NULL DEFAULT '[]',
    privacy_risk_detail JSONB,

    -- Metadata
    duration_ms         DECIMAL(10,2),
    cached              BOOLEAN     NOT NULL DEFAULT FALSE,
    error_code          TEXT,
    assessed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Immutability marker (chain-hash for audit integrity)
    integrity_hash      TEXT
);

-- RLS
ALTER TABLE compliance_assessments ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON compliance_assessments
    USING (tenant_id = current_setting('app.current_tenant')::UUID);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_compliance_assessments_tenant_ts
    ON compliance_assessments (tenant_id, assessed_at DESC);
CREATE INDEX IF NOT EXISTS idx_compliance_assessments_sku
    ON compliance_assessments (tenant_id, product_sku, assessed_at DESC);
CREATE INDEX IF NOT EXISTS idx_compliance_assessments_recommendation
    ON compliance_assessments (tenant_id, recommendation, assessed_at DESC);

-- TimescaleDB hypertable
SELECT create_hypertable(
    'compliance_assessments', 'assessed_at',
    if_not_exists => TRUE,
    migrate_data   => TRUE
);

-- ---------------------------------------------------------------------------
-- compliance_blocks
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS compliance_blocks (
    block_id        UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID        NOT NULL,
    assessment_id   UUID        REFERENCES compliance_assessments(assessment_id),
    product_sku     TEXT        NOT NULL,
    plan_id         TEXT,           -- Phase 6 ExecutionPlan.plan_id
    intent_id       TEXT,           -- Phase 6 ExecutionIntent.intent_id
    block_reason    TEXT        NOT NULL,
    risk_score      DECIMAL(5,4) NOT NULL,
    error_code      TEXT        NOT NULL DEFAULT 'AEGIS-COMPLY-0001',
    blocked_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    appealed        BOOLEAN     NOT NULL DEFAULT FALSE,
    appeal_reason   TEXT,
    appeal_approved BOOLEAN,
    reviewed_by     TEXT,
    reviewed_at     TIMESTAMPTZ
);

ALTER TABLE compliance_blocks ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON compliance_blocks
    USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE INDEX IF NOT EXISTS idx_compliance_blocks_tenant_ts
    ON compliance_blocks (tenant_id, blocked_at DESC);
CREATE INDEX IF NOT EXISTS idx_compliance_blocks_sku
    ON compliance_blocks (tenant_id, product_sku);

-- ---------------------------------------------------------------------------
-- trademark_cache
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trademark_cache (
    cache_id            UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    query_term          TEXT        NOT NULL,
    jurisdiction        TEXT        NOT NULL DEFAULT 'MULTI',
    registered_mark     TEXT        NOT NULL,
    registration_number TEXT        NOT NULL,
    owner               TEXT        NOT NULL,
    status              TEXT        NOT NULL,
    goods_services      TEXT,
    confidence_score    DECIMAL(5,4),
    source              TEXT        NOT NULL DEFAULT 'euipo_tmview',
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours'),
    UNIQUE (query_term, registration_number)
);

CREATE INDEX IF NOT EXISTS idx_trademark_cache_query
    ON trademark_cache (query_term, expires_at DESC);

-- Auto-prune expired entries
CREATE OR REPLACE FUNCTION prune_expired_trademark_cache()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM trademark_cache WHERE expires_at < NOW() - INTERVAL '1 hour';
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_prune_trademark_cache ON trademark_cache;
CREATE TRIGGER trg_prune_trademark_cache
    AFTER INSERT ON trademark_cache
    FOR EACH STATEMENT EXECUTE FUNCTION prune_expired_trademark_cache();

-- ---------------------------------------------------------------------------
-- sanction_hits
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sanction_hits (
    hit_id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID        NOT NULL,
    assessment_id   UUID        REFERENCES compliance_assessments(assessment_id),
    match_type      TEXT        NOT NULL,   -- 'country_sanction' | 'fatf_high_risk' | 'entity_match'
    matched_value   TEXT        NOT NULL,
    program         TEXT,
    source          TEXT        NOT NULL,
    risk_score      DECIMAL(5,4) NOT NULL,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE sanction_hits ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON sanction_hits
    USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE INDEX IF NOT EXISTS idx_sanction_hits_tenant_ts
    ON sanction_hits (tenant_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_sanction_hits_value
    ON sanction_hits (matched_value, detected_at DESC);

-- ---------------------------------------------------------------------------
-- Comments
-- ---------------------------------------------------------------------------
COMMENT ON TABLE compliance_assessments IS
    'Immutable Phase 8 compliance assessment records — one row per assess() call.';
COMMENT ON TABLE compliance_blocks IS
    'Phase 8 execution blocks — records every trade blocked by compliance engine.';
COMMENT ON TABLE trademark_cache IS
    'Short-lived cache for USPTO/EUIPO trademark lookup results (24 h TTL).';
COMMENT ON TABLE sanction_hits IS
    'OFAC / FATF / Trade.gov sanction matches for audit and regulatory reporting.';
