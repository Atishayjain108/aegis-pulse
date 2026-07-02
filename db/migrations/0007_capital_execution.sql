-- =============================================================================
-- Migration 0007: Capital Execution Engine (Phase 6)
-- =============================================================================
-- Adds the data model for Phase 6: execution plans, orders, and daily PnL.
--
-- Tables:
--   execution_plans  — Kelly-sized execution plans derived from intents.
--   execution_orders — Individual fulfillment orders with outcome tracking.
--   daily_settlements — EOD PnL snapshots for audit and tax reporting.
--
-- All tables use the app.current_tenant Row-Level Security pattern.
-- Run after 0006_b2b_supply_chain.sql.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- execution_plans
-- ---------------------------------------------------------------------------
-- One plan per ExecutionIntent. Tracks the Kelly-sized plan and its status
-- through the approval → execution → fulfilled lifecycle.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS execution_plans (
    plan_id             UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID            NOT NULL,
    intent_id           TEXT            NOT NULL,
    trend_id            TEXT            NOT NULL,
    execution_mode      TEXT            NOT NULL DEFAULT 'advisory'
                                            CHECK (execution_mode IN ('advisory', 'staging', 'live')),
    quantity            INTEGER         NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    fulfillment_method  TEXT            NOT NULL
                                            CHECK (fulfillment_method IN ('pod', 'dropship', 'inventory')),
    unit_cost_usd       NUMERIC(12, 4)  NOT NULL DEFAULT 0,
    unit_price_usd      NUMERIC(12, 4)  NOT NULL DEFAULT 0,
    total_capital_usd   NUMERIC(12, 4)  NOT NULL DEFAULT 0,
    estimated_profit_usd NUMERIC(12, 4) NOT NULL DEFAULT 0,
    kelly_fraction_raw  REAL            NOT NULL DEFAULT 0,
    kelly_fraction_used REAL            NOT NULL DEFAULT 0,
    risk_score          REAL            NOT NULL DEFAULT 0 CHECK (risk_score BETWEEN 0 AND 1),
    requires_approval   BOOLEAN         NOT NULL DEFAULT TRUE,
    status              TEXT            NOT NULL DEFAULT 'pending'
                                            CHECK (status IN (
                                                'pending', 'approved', 'rejected',
                                                'executed', 'fulfilled', 'failed', 'cancelled'
                                            )),
    approved_by         TEXT,
    approved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_execution_plans_tenant_status
    ON execution_plans (tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_execution_plans_trend_id
    ON execution_plans (trend_id);

-- RLS
ALTER TABLE execution_plans ENABLE ROW LEVEL SECURITY;
CREATE POLICY execution_plans_tenant ON execution_plans
    USING (tenant_id::text = current_setting('app.current_tenant', TRUE));

-- ---------------------------------------------------------------------------
-- execution_orders
-- ---------------------------------------------------------------------------
-- Individual fulfillment orders created by the engine. Multiple orders may
-- map to one plan (one per unit for POD; one-to-one for dropship/inventory).
-- Outcome fields (revenue, refund, pnl) are populated by SettlementManager.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS execution_orders (
    order_id            TEXT            NOT NULL,
    plan_id             UUID            NOT NULL REFERENCES execution_plans (plan_id),
    tenant_id           UUID            NOT NULL,
    fulfillment_order_id TEXT,           -- external ID from Printful / CJ / Shopify
    unit_cost_usd       NUMERIC(12, 4)  NOT NULL DEFAULT 0,
    quantity            INTEGER         NOT NULL DEFAULT 1 CHECK (quantity > 0),
    revenue_usd         NUMERIC(12, 4)  NOT NULL DEFAULT 0,
    refund_usd          NUMERIC(12, 4)  NOT NULL DEFAULT 0,
    shipping_usd        NUMERIC(12, 4)  NOT NULL DEFAULT 5,
    platform_fee_usd    NUMERIC(12, 4)  NOT NULL DEFAULT 0,
    pnl_usd             NUMERIC(12, 4),  -- NULL until settled
    status              TEXT            NOT NULL DEFAULT 'pending'
                                            CHECK (status IN (
                                                'pending', 'processing', 'shipped',
                                                'delivered', 'returned', 'refunded', 'failed'
                                            )),
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    settled_at          TIMESTAMPTZ,
    -- Partition column must be part of any PK/UNIQUE index on a hypertable.
    PRIMARY KEY (order_id, created_at)
);

SELECT create_hypertable(
    'execution_orders',
    'created_at',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

CREATE INDEX IF NOT EXISTS idx_execution_orders_plan_id
    ON execution_orders (plan_id);

CREATE INDEX IF NOT EXISTS idx_execution_orders_tenant_id
    ON execution_orders (tenant_id, created_at DESC);

-- RLS
ALTER TABLE execution_orders ENABLE ROW LEVEL SECURITY;
CREATE POLICY execution_orders_tenant ON execution_orders
    USING (tenant_id::text = current_setting('app.current_tenant', TRUE));

-- ---------------------------------------------------------------------------
-- daily_settlements
-- ---------------------------------------------------------------------------
-- EOD PnL snapshot produced by SettlementManager.settle_daily().
-- Used for tax reporting and drawdown monitoring.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_settlements (
    id                  BIGSERIAL       PRIMARY KEY,
    tenant_id           UUID            NOT NULL,
    settlement_date     DATE            NOT NULL,
    order_count         INTEGER         NOT NULL DEFAULT 0,
    total_revenue_usd   NUMERIC(14, 4)  NOT NULL DEFAULT 0,
    total_cost_usd      NUMERIC(14, 4)  NOT NULL DEFAULT 0,
    total_pnl_usd       NUMERIC(14, 4)  NOT NULL DEFAULT 0,
    reconciliation_errors JSONB         NOT NULL DEFAULT '[]',
    settled_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, settlement_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_settlements_tenant_date
    ON daily_settlements (tenant_id, settlement_date DESC);

-- RLS
ALTER TABLE daily_settlements ENABLE ROW LEVEL SECURITY;
CREATE POLICY daily_settlements_tenant ON daily_settlements
    USING (tenant_id::text = current_setting('app.current_tenant', TRUE));

-- ---------------------------------------------------------------------------
-- updated_at trigger for execution_plans
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION _set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_execution_plans_updated ON execution_plans;
CREATE TRIGGER trg_execution_plans_updated
    BEFORE UPDATE ON execution_plans
    FOR EACH ROW EXECUTE PROCEDURE _set_updated_at();

COMMIT;
