-- =============================================================================
-- 0021_execution_intel.sql — PROJECT OMEGA Phase D (Execution Intelligence)
--
-- Stages S1 + S2 slice: the durable EXECUTION KNOWLEDGE ENGINE (Rule 2) +
-- per-supplier RELIABILITY rollup (Rule 3).
--
-- Doctrine (Reality First / Rule 1): an execution_record is created when a plan
-- is recommended; its outcome/cost/pnl are filled ONLY from a settled order
-- (execution_orders / SettlementManager). Nothing here invents a sale.
-- Every assumption the recommendation rests on is logged with an explicit
-- status: 'unverified' is the default — it is NEVER silently treated as true.
--
-- Purely additive — no existing table is touched. Reversible by dropping the
-- three tables below. supplier_reliability is keyed on the existing `entities`
-- row (EntityKind.SUPPLIER) so identity + base trust are reused, not duplicated.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- execution_records — plan → outcome → cost → failure (Rule 2)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS execution_records (
    record_id             UUID        NOT NULL DEFAULT gen_random_uuid(),
    tenant_id             UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',

    -- Re-observable handles back into the live execution pipeline.
    plan_id               TEXT        NOT NULL,
    trend_id              TEXT        NOT NULL DEFAULT 'unknown',
    opportunity_type      TEXT        NOT NULL DEFAULT 'product',
    region                TEXT,

    -- The supplier this plan depended on (canonical_name in `entities`), if any.
    supplier_name         TEXT,

    -- What the plan committed to at recommendation time.
    planned_units         INT         NOT NULL DEFAULT 0,
    planned_unit_cost_usd REAL,
    planned_margin_pct    REAL,

    -- Settled truth (filled by record_outcome; NULL while pending).
    outcome               TEXT        NOT NULL DEFAULT 'pending'
                                      CHECK (outcome IN (
                                          'pending','succeeded','failed',
                                          'cancelled','delayed','unverified')),
    realized_units        INT,
    realized_pnl_usd      REAL,
    realized_cost_usd     REAL,
    delay_hours           REAL,
    failure_category      TEXT        NOT NULL DEFAULT 'none',
    failure_detail        TEXT        NOT NULL DEFAULT '',

    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settled_at            TIMESTAMPTZ,
    settlement_timestamp  TIMESTAMPTZ NOT NULL DEFAULT NOW(),   -- hypertable key
    metadata              JSONB       NOT NULL DEFAULT '{}',
    PRIMARY KEY (record_id, settlement_timestamp)
);

SELECT create_hypertable(
    'execution_records', 'settlement_timestamp',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

ALTER TABLE execution_records ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS execution_records_tenant ON execution_records;
CREATE POLICY execution_records_tenant ON execution_records
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);

-- One record per plan (idempotent record_plan / record_outcome).
CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_records_plan
    ON execution_records (tenant_id, plan_id, settlement_timestamp);
CREATE INDEX IF NOT EXISTS idx_execution_records_outcome
    ON execution_records (outcome);
CREATE INDEX IF NOT EXISTS idx_execution_records_supplier
    ON execution_records (supplier_name);

-- -----------------------------------------------------------------------------
-- execution_assumptions — Reality First ledger (Rule 1)
-- Every assumption a recommendation rests on, with an explicit verification
-- status. Default 'unverified' is the honest baseline — never assumed true.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS execution_assumptions (
    assumption_id   UUID        NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',

    plan_id         TEXT        NOT NULL,
    kind            TEXT        NOT NULL,   -- supplier_exists / inventory / margin / demand / buyer / ...
    claim           TEXT        NOT NULL DEFAULT '',
    status          TEXT        NOT NULL DEFAULT 'unverified'
                                CHECK (status IN ('verified','unverified','falsified')),
    evidence        JSONB       NOT NULL DEFAULT '{}',
    verified_via    TEXT,                   -- e.g. 'printful_api' when status='verified'

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (assumption_id)
);

ALTER TABLE execution_assumptions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS execution_assumptions_tenant ON execution_assumptions;
CREATE POLICY execution_assumptions_tenant ON execution_assumptions
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);

CREATE INDEX IF NOT EXISTS idx_execution_assumptions_plan
    ON execution_assumptions (plan_id);

-- -----------------------------------------------------------------------------
-- supplier_reliability — Supplier Trust Score inputs (Rule 3)
-- Keyed on the supplier's canonical_name (same key as entities.canonical_name
-- with entity_kind='supplier'). All counters are incremented from REAL events:
-- verification API calls + settled fulfillment outcomes. Trust is derived, not
-- asserted; with zero outcomes the score is NULL (UNVERIFIED), not a guess.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS supplier_reliability (
    tenant_id            UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    supplier_name        TEXT        NOT NULL,

    n_verifications      INT         NOT NULL DEFAULT 0,   -- inventory/cost checks attempted
    n_verified           INT         NOT NULL DEFAULT 0,   -- of those, succeeded
    n_fulfillments       INT         NOT NULL DEFAULT 0,   -- settled orders attributed
    n_fulfilled_ok       INT         NOT NULL DEFAULT 0,   -- of those, succeeded (pnl>=0, not cancelled)
    n_delays             INT         NOT NULL DEFAULT 0,
    n_cancellations      INT         NOT NULL DEFAULT 0,
    total_response_ms    BIGINT      NOT NULL DEFAULT 0,   -- sum of verification latencies
    n_responses          INT         NOT NULL DEFAULT 0,

    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, supplier_name)
);

ALTER TABLE supplier_reliability ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS supplier_reliability_tenant ON supplier_reliability;
CREATE POLICY supplier_reliability_tenant ON supplier_reliability
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);
