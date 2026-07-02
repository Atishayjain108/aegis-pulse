-- =============================================================================
-- Migration 0006: B2B Supply Chain Ontology
-- =============================================================================
-- Adds the data model for wholesale/textile supply-chain arbitrage detection.
-- Target use case: mill → trader → retailer price/lot arbitrage, MOQ mismatches,
-- and logistics deadhead opportunities (Gandhi Nagar archetype).
--
-- All tables use the same app.current_tenant Row-Level Security pattern as the
-- rest of the AEGIS schema.  Run this after 0003_execute.sql.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- supply_chain_node
-- ---------------------------------------------------------------------------
-- Represents any participant in the supply chain: mill, trader, cluster, port.
-- trust_score is updated by the AUDITOR agent after verification.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS supply_chain_node (
    node_id         UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID            NOT NULL,
    node_type       TEXT            NOT NULL CHECK (node_type IN ('mill', 'trader', 'cluster', 'retailer', 'port', 'other')),
    display_name    TEXT            NOT NULL,
    geo_lat         DOUBLE PRECISION,
    geo_lon         DOUBLE PRECISION,
    geo_region      TEXT,           -- e.g. 'Gandhi Nagar', 'Surat', 'Tirupur'
    gstin           TEXT,           -- GSTIN for verified Indian entities
    trust_score     REAL            NOT NULL DEFAULT 0.5 CHECK (trust_score BETWEEN 0 AND 1),
    source_adapter  TEXT,           -- which scrape adapter discovered this node
    raw_json        JSONB,          -- original scraped payload for audit
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

ALTER TABLE supply_chain_node ENABLE ROW LEVEL SECURITY;
ALTER TABLE supply_chain_node FORCE ROW LEVEL SECURITY;

CREATE POLICY supply_chain_node_tenant_isolation ON supply_chain_node
    USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE INDEX IF NOT EXISTS supply_chain_node_tenant_idx
    ON supply_chain_node (tenant_id, node_type);
CREATE INDEX IF NOT EXISTS supply_chain_node_geo_idx
    ON supply_chain_node (geo_region)
    WHERE geo_region IS NOT NULL;

-- ---------------------------------------------------------------------------
-- sku_lot
-- ---------------------------------------------------------------------------
-- A tradeable unit of fabric/goods: SKU, grade, MOQ, unit of measure.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sku_lot (
    sku_id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID            NOT NULL,
    display_name    TEXT            NOT NULL,
    category        TEXT,           -- e.g. 'cotton', 'polyester', 'silk', 'blend'
    gsm             INTEGER,        -- grams per square metre (fabric weight)
    composition     TEXT,           -- e.g. '60% cotton 40% polyester'
    min_order_qty   INTEGER,        -- MOQ in units (metres, pieces, kg)
    unit            TEXT            NOT NULL DEFAULT 'metres' CHECK (unit IN ('metres', 'kg', 'pieces', 'units')),
    freshness_ts    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

ALTER TABLE sku_lot ENABLE ROW LEVEL SECURITY;
ALTER TABLE sku_lot FORCE ROW LEVEL SECURITY;

CREATE POLICY sku_lot_tenant_isolation ON sku_lot
    USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE INDEX IF NOT EXISTS sku_lot_tenant_category_idx
    ON sku_lot (tenant_id, category);

-- ---------------------------------------------------------------------------
-- price_quote
-- ---------------------------------------------------------------------------
-- A price observed at a supply chain node for a specific SKU.
-- Valid until valid_until (NULL = no known expiry).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS price_quote (
    quote_id        UUID            NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       UUID            NOT NULL,
    node_id         UUID            NOT NULL REFERENCES supply_chain_node(node_id) ON DELETE CASCADE,
    sku_id          UUID            NOT NULL REFERENCES sku_lot(sku_id) ON DELETE CASCADE,
    price_inr       NUMERIC(14, 2)  NOT NULL CHECK (price_inr > 0),
    currency        TEXT            NOT NULL DEFAULT 'INR',
    valid_until     TIMESTAMPTZ,
    source_adapter  TEXT,
    raw_json        JSONB,
    observed_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    -- Partition column must be part of any PK/UNIQUE index on a hypertable.
    PRIMARY KEY (quote_id, observed_at)
);

SELECT create_hypertable('price_quote', 'observed_at', if_not_exists => TRUE);

ALTER TABLE price_quote ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_quote FORCE ROW LEVEL SECURITY;

CREATE POLICY price_quote_tenant_isolation ON price_quote
    USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE INDEX IF NOT EXISTS price_quote_node_sku_idx
    ON price_quote (tenant_id, node_id, sku_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS price_quote_sku_time_idx
    ON price_quote (sku_id, observed_at DESC);

-- ---------------------------------------------------------------------------
-- logistics_lane
-- ---------------------------------------------------------------------------
-- Estimated freight cost and SLA between two geographic hubs.
-- Refreshed by the geo-arbitrage adapter; cost_per_kg is an estimate.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS logistics_lane (
    lane_id         UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID            NOT NULL,
    origin_hub      TEXT            NOT NULL,   -- e.g. 'Surat', 'Tirupur'
    dest_hub        TEXT            NOT NULL,
    mode            TEXT            NOT NULL DEFAULT 'road' CHECK (mode IN ('road', 'rail', 'air', 'sea')),
    cost_per_kg     NUMERIC(10, 2),             -- INR per kg
    sla_hours       INTEGER,                    -- estimated transit hours
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

ALTER TABLE logistics_lane ENABLE ROW LEVEL SECURITY;
ALTER TABLE logistics_lane FORCE ROW LEVEL SECURITY;

CREATE POLICY logistics_lane_tenant_isolation ON logistics_lane
    USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE UNIQUE INDEX IF NOT EXISTS logistics_lane_route_idx
    ON logistics_lane (tenant_id, origin_hub, dest_hub, mode);

-- ---------------------------------------------------------------------------
-- arbitrage_opportunity
-- ---------------------------------------------------------------------------
-- A detected buy-low / sell-high opportunity linking two price quotes with
-- a logistics estimate and a confidence score from the agent pipeline.
-- The vyapar_action field carries the draft Vyapar invoice action when
-- AEGIS_EXECUTE_MODE=live and the killswitch is armed.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS arbitrage_opportunity (
    opportunity_id  UUID            NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       UUID            NOT NULL,
    -- price_quote is a hypertable: quote_id alone is not unique, so no FK here.
    buy_quote_id    UUID            NOT NULL,
    sell_quote_id   UUID            NOT NULL,
    sku_id          UUID            NOT NULL REFERENCES sku_lot(sku_id) ON DELETE CASCADE,
    margin_pct      REAL            NOT NULL CHECK (margin_pct BETWEEN -100 AND 1000),
    confidence      REAL            NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
    logistics_cost_inr NUMERIC(14, 2),
    net_margin_pct  REAL,           -- margin_pct minus estimated logistics cost fraction
    verdict         TEXT            NOT NULL DEFAULT 'HOLD' CHECK (verdict IN ('ENTER', 'HOLD', 'BLOCK')),
    vyapar_action   JSONB,          -- draft Vyapar webhook payload (null until execute-api sends it)
    halt_reason     TEXT,
    detected_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    -- Partition column must be part of any PK/UNIQUE index on a hypertable.
    PRIMARY KEY (opportunity_id, detected_at)
);

SELECT create_hypertable('arbitrage_opportunity', 'detected_at', if_not_exists => TRUE);

ALTER TABLE arbitrage_opportunity ENABLE ROW LEVEL SECURITY;
ALTER TABLE arbitrage_opportunity FORCE ROW LEVEL SECURITY;

CREATE POLICY arbitrage_opportunity_tenant_isolation ON arbitrage_opportunity
    USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE INDEX IF NOT EXISTS arb_opportunity_tenant_verdict_idx
    ON arbitrage_opportunity (tenant_id, verdict, detected_at DESC);
CREATE INDEX IF NOT EXISTS arb_opportunity_sku_idx
    ON arbitrage_opportunity (sku_id, detected_at DESC);

-- ---------------------------------------------------------------------------
-- Catalog entry (matches Phase 10 datalake catalog pattern)
-- ---------------------------------------------------------------------------
COMMENT ON TABLE supply_chain_node      IS 'B2B supply chain participants (mills, traders, clusters)';
COMMENT ON TABLE sku_lot                IS 'Tradeable SKU/lot definitions with MOQ and grade';
COMMENT ON TABLE price_quote            IS 'Time-series price observations per node/SKU (hypertable)';
COMMENT ON TABLE logistics_lane         IS 'Freight cost and SLA estimates between hubs';
COMMENT ON TABLE arbitrage_opportunity  IS 'Detected arbitrage spreads with agent confidence scores (hypertable)';

COMMIT;
