-- =============================================================================
-- Migration 0008: Geospatial Intelligence & Cross-Market Arbitrage (Phase 7)
-- =============================================================================
-- Adds the data model for Phase 7: geo opportunities, price snapshots, and FX
-- rate history. All tables use app.current_tenant Row-Level Security pattern.
-- Run after 0007_capital_execution.sql.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- geo_opportunities
-- ---------------------------------------------------------------------------
-- Persisted cross-market arbitrage opportunities identified by Phase 7.
-- One row per (product_sku, origin_region, destination_region, analysis_ts).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS geo_opportunities (
    opportunity_id          UUID            NOT NULL DEFAULT gen_random_uuid(),
    tenant_id               UUID            NOT NULL,
    product_sku             TEXT            NOT NULL,
    product_title           TEXT            NOT NULL,
    category                TEXT            NOT NULL DEFAULT 'general',
    hs_code                 TEXT            NOT NULL DEFAULT '',
    origin_region           TEXT            NOT NULL,
    destination_region      TEXT            NOT NULL,
    origin_price_local      NUMERIC(14, 4)  NOT NULL,
    origin_currency         TEXT            NOT NULL DEFAULT 'USD',
    destination_price_local NUMERIC(14, 4)  NOT NULL,
    destination_currency    TEXT            NOT NULL DEFAULT 'USD',
    destination_price_usd   NUMERIC(14, 4)  NOT NULL,
    shipping_cost_usd       NUMERIC(14, 4)  NOT NULL DEFAULT 0,
    duty_cost_usd           NUMERIC(14, 4)  NOT NULL DEFAULT 0,
    platform_fee_usd        NUMERIC(14, 4)  NOT NULL DEFAULT 0,
    total_landed_cost_usd   NUMERIC(14, 4)  NOT NULL DEFAULT 0,
    gross_margin_usd        NUMERIC(14, 4)  NOT NULL DEFAULT 0,
    gross_margin_pct        NUMERIC(8, 4)   NOT NULL DEFAULT 0,
    demand_intensity        REAL            NOT NULL DEFAULT 0 CHECK (demand_intensity BETWEEN 0 AND 1),
    market_size_score       REAL            NOT NULL DEFAULT 0,
    opportunity_score       REAL            NOT NULL DEFAULT 0,
    fx_rate_used            NUMERIC(14, 6)  NOT NULL DEFAULT 1,
    status                  TEXT            NOT NULL DEFAULT 'identified'
                                                CHECK (status IN (
                                                    'identified', 'approved', 'executing',
                                                    'completed', 'rejected', 'expired'
                                                )),
    execution_plan_id       UUID,           -- FK to execution_plans when acted on
    analysis_ts             TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    expires_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW() + INTERVAL '24 hours',
    metadata                JSONB           NOT NULL DEFAULT '{}',
    -- Partition column must be part of any PK/UNIQUE index on a hypertable.
    PRIMARY KEY (opportunity_id, analysis_ts)
);

SELECT create_hypertable(
    'geo_opportunities',
    'analysis_ts',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

CREATE INDEX IF NOT EXISTS idx_geo_opportunities_tenant_score
    ON geo_opportunities (tenant_id, opportunity_score DESC);

CREATE INDEX IF NOT EXISTS idx_geo_opportunities_sku
    ON geo_opportunities (product_sku, analysis_ts DESC);

CREATE INDEX IF NOT EXISTS idx_geo_opportunities_regions
    ON geo_opportunities (origin_region, destination_region);

ALTER TABLE geo_opportunities ENABLE ROW LEVEL SECURITY;
CREATE POLICY geo_opportunities_tenant ON geo_opportunities
    USING (tenant_id::text = current_setting('app.current_tenant', TRUE));

-- ---------------------------------------------------------------------------
-- geo_price_snapshots
-- ---------------------------------------------------------------------------
-- Regional price observations scraped from marketplace signals.
-- Populated by demand.py when signals with price_amount are ingested.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS geo_price_snapshots (
    snapshot_id     UUID            NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       UUID            NOT NULL,
    product_sku     TEXT            NOT NULL,
    region          TEXT            NOT NULL,
    platform        TEXT            NOT NULL,
    price_local     NUMERIC(14, 4)  NOT NULL,
    currency        TEXT            NOT NULL DEFAULT 'USD',
    price_usd       NUMERIC(14, 4)  NOT NULL,
    signal_id       UUID,           -- source signal if available
    observed_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    -- Partition column must be part of any PK/UNIQUE index on a hypertable.
    PRIMARY KEY (snapshot_id, observed_at)
);

SELECT create_hypertable(
    'geo_price_snapshots',
    'observed_at',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

CREATE INDEX IF NOT EXISTS idx_geo_price_snapshots_sku_region
    ON geo_price_snapshots (product_sku, region, observed_at DESC);

ALTER TABLE geo_price_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY geo_price_snapshots_tenant ON geo_price_snapshots
    USING (tenant_id::text = current_setting('app.current_tenant', TRUE));

-- ---------------------------------------------------------------------------
-- geo_fx_snapshots
-- ---------------------------------------------------------------------------
-- FX rate history for audit and backtesting. Written by fx.py on each fetch.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS geo_fx_snapshots (
    id              BIGSERIAL       PRIMARY KEY,
    base_currency   TEXT            NOT NULL,
    quote_currency  TEXT            NOT NULL,
    rate            NUMERIC(14, 6)  NOT NULL,
    source          TEXT            NOT NULL DEFAULT 'frankfurter',
    fetched_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_geo_fx_snapshots_pair_time
    ON geo_fx_snapshots (base_currency, quote_currency, fetched_at DESC);

COMMIT;
