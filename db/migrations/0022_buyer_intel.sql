-- =============================================================================
-- 0022_buyer_intel.sql — PROJECT OMEGA Phase D (S3: Buyer Intelligence, Rule 4)
--
-- HONEST GAP (Rule 1): AEGIS scrapes signals, not orders or customers. We have
-- NO real buyer identity / order / fulfillment data. Fabricating individual
-- buyers would be architectural theater. So this table models DEMAND as a
-- proxy, keyed by (region, category) — not fake people — and leaves every
-- order/fulfillment counter at 0 (UNVERIFIED) until a real order source exists.
--
-- ``buyer_trust`` is intentionally NOT a column: with zero real orders there is
-- nothing to score, and the schema must not invite a guessed number. Trust is
-- returned as None/UNVERIFIED by BuyerIntel until n_orders > 0.
--
-- Additive only — reversible by dropping the table.
-- =============================================================================

CREATE TABLE IF NOT EXISTS buyer_demand (
    tenant_id              UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    region                 TEXT        NOT NULL DEFAULT 'GLOBAL',
    category               TEXT        NOT NULL DEFAULT 'general',

    -- Demand PROXY derived from real signal velocity/engagement (geo.demand).
    -- This is an observed proxy, NOT verified purchase demand — labelled as such.
    demand_intensity       REAL,                               -- running mean of observations, [0,1] or NULL
    n_demand_observations  INT         NOT NULL DEFAULT 0,

    -- Real order/fulfillment counters. Stay 0 until a real order source is wired
    -- (Rule 1: never incremented from synthetic data).
    n_orders               INT         NOT NULL DEFAULT 0,
    n_fulfilled            INT         NOT NULL DEFAULT 0,
    n_cancelled            INT         NOT NULL DEFAULT 0,

    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, region, category)
);

ALTER TABLE buyer_demand ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS buyer_demand_tenant ON buyer_demand;
CREATE POLICY buyer_demand_tenant ON buyer_demand
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);

CREATE INDEX IF NOT EXISTS idx_buyer_demand_intensity
    ON buyer_demand (demand_intensity DESC NULLS LAST);
