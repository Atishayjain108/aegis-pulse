-- =============================================================================
-- 0020_knowledge_memory_stage4.sql — PROJECT OMEGA Phase C (Stage 4, final)
--
-- Completes the knowledge layer: Entity Memory (Rule 3), Market Memory (Rule 6),
-- and the Knowledge Graph (Rule 7). All additive; reversible by dropping the
-- four tables. Every populated row still traces to a settled outcome or an
-- observed signal — knowledge is never invented (Rule 1 / Rule 12).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- entities + entity_outcomes — long-term entity memory (Rule 3)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entities (
    entity_id        UUID        NOT NULL DEFAULT gen_random_uuid(),
    tenant_id        UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    entity_kind      TEXT        NOT NULL,   -- taxonomy.EntityKind
    canonical_name   TEXT        NOT NULL,
    attributes       JSONB       NOT NULL DEFAULT '{}',
    trust            REAL        NOT NULL DEFAULT 0.5,
    n_observations   INT         NOT NULL DEFAULT 0,
    first_seen       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata         JSONB       NOT NULL DEFAULT '{}',
    PRIMARY KEY (entity_id),
    CONSTRAINT entities_unique UNIQUE (tenant_id, entity_kind, canonical_name)
);

ALTER TABLE entities ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS entities_tenant ON entities;
CREATE POLICY entities_tenant ON entities
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);
CREATE INDEX IF NOT EXISTS idx_entities_kind ON entities (entity_kind, trust DESC);

CREATE TABLE IF NOT EXISTS entity_outcomes (
    eo_id           UUID        NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    entity_id       UUID        NOT NULL,
    opportunity_id  UUID        NOT NULL,
    outcome         TEXT        NOT NULL,   -- realized | failed
    contribution    REAL        NOT NULL DEFAULT 1.0,
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (eo_id),
    CONSTRAINT entity_outcomes_unique UNIQUE (tenant_id, entity_id, opportunity_id)
);

ALTER TABLE entity_outcomes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS entity_outcomes_tenant ON entity_outcomes;
CREATE POLICY entity_outcomes_tenant ON entity_outcomes
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);
CREATE INDEX IF NOT EXISTS idx_entity_outcomes_entity ON entity_outcomes (entity_id);

-- -----------------------------------------------------------------------------
-- market_epochs — long-term market memory (Rule 6)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_epochs (
    epoch_id             UUID        NOT NULL DEFAULT gen_random_uuid(),
    tenant_id            UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    period_start         TIMESTAMPTZ NOT NULL,
    period_end           TIMESTAMPTZ NOT NULL,
    category             TEXT        NOT NULL DEFAULT 'general',
    region               TEXT,
    trend_summary        JSONB       NOT NULL DEFAULT '{}',
    demand_index         REAL,
    seasonality_tag      TEXT,
    recurring_pattern_ids JSONB      NOT NULL DEFAULT '[]',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (epoch_id)
);

ALTER TABLE market_epochs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS market_epochs_tenant ON market_epochs;
CREATE POLICY market_epochs_tenant ON market_epochs
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);
CREATE INDEX IF NOT EXISTS idx_market_epochs_cat
    ON market_epochs (category, region, period_start DESC);

-- -----------------------------------------------------------------------------
-- knowledge_edges — SQL-backed relationship graph (Rule 7)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_edges (
    edge_id        UUID        NOT NULL DEFAULT gen_random_uuid(),
    tenant_id      UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    src_kind       TEXT        NOT NULL,
    src_id         TEXT        NOT NULL,
    dst_kind       TEXT        NOT NULL,
    dst_id         TEXT        NOT NULL,
    relation       TEXT        NOT NULL,   -- taxonomy.RelationType
    weight         REAL        NOT NULL DEFAULT 1.0,
    evidence_count INT         NOT NULL DEFAULT 1,
    first_seen     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata       JSONB       NOT NULL DEFAULT '{}',
    PRIMARY KEY (edge_id),
    CONSTRAINT knowledge_edges_unique
        UNIQUE (tenant_id, src_kind, src_id, dst_kind, dst_id, relation)
);

ALTER TABLE knowledge_edges ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS knowledge_edges_tenant ON knowledge_edges;
CREATE POLICY knowledge_edges_tenant ON knowledge_edges
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_src
    ON knowledge_edges (src_kind, src_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_relation
    ON knowledge_edges (relation, weight DESC);
