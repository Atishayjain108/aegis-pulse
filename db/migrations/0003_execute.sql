-- =====================================================================
-- AEGIS Pulse — Phase 4 schema
-- Migration: 0003_execute.sql
-- Depends on: 0001_init.sql (tenants), 0002_predictions.sql (predictions)
--
-- Rationale:
--   * `alerts`            : the canonical decision log; hypertable on created_at
--   * `alert_outbox`      : at-least-once delivery queue (Postgres-backed)
--   * `alert_deliveries`  : immutable per-channel attempt history
--   * `execution_intents` : read-only handoff to Phase 6
--   * `killswitch_audit`  : every toggle, who/when/why
--
-- RLS: all tables enforce app.current_tenant matching, consistent with Phases 1-3.
-- =====================================================================

-- =====================================================================
-- alerts
-- =====================================================================
CREATE TABLE IF NOT EXISTS alerts (
    alert_id            TEXT PRIMARY KEY,
    tenant_id           UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    trend_id            TEXT NOT NULL,
    decision_window     TEXT NOT NULL DEFAULT 'default',
    verdict             TEXT NOT NULL CHECK (verdict IN ('ENTER','HOLD','EXIT','BLOCK','DEGRADED')),
    priority            SMALLINT NOT NULL CHECK (priority BETWEEN 0 AND 3),
    score               DOUBLE PRECISION NOT NULL CHECK (score BETWEEN 0 AND 1),
    confidence          DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    source              TEXT NOT NULL,
    p_breakout_24h      DOUBLE PRECISION,
    p_decline_6h        DOUBLE PRECISION,
    p_saturation        DOUBLE PRECISION,
    expected_margin_usd DOUBLE PRECISION,
    loss_probability    DOUBLE PRECISION,
    advised_units       INTEGER,
    advised_capital_usd DOUBLE PRECISION,
    halt_reason         TEXT,
    blocked_by          TEXT[] NOT NULL DEFAULT '{}',
    title               TEXT NOT NULL,
    summary_text        TEXT NOT NULL DEFAULT '',
    correlation_id      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_tenant_created  ON alerts (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_trend           ON alerts (tenant_id, trend_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_priority        ON alerts (tenant_id, priority, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_verdict         ON alerts (tenant_id, verdict);

-- TimescaleDB hypertable skipped: alerts.alert_id is a plain TEXT PRIMARY KEY
-- that does not include created_at, so create_hypertable would be rejected.
-- The table works correctly as a plain Postgres table with the time-based
-- indexes above. Promote to a hypertable in a later migration once the schema
-- moves to a composite PK (alert_id, created_at).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        BEGIN
            PERFORM create_hypertable('alerts', 'created_at',
                chunk_time_interval => INTERVAL '1 day',
                if_not_exists => TRUE);
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'create_hypertable(alerts) skipped: % — table remains a regular Postgres table', SQLERRM;
        END;
    END IF;
END$$;

ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS alerts_tenant_isolation ON alerts;
CREATE POLICY alerts_tenant_isolation ON alerts
    USING (tenant_id::text = current_setting('app.current_tenant', TRUE))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant', TRUE));

-- =====================================================================
-- alert_outbox
-- =====================================================================
CREATE TABLE IF NOT EXISTS alert_outbox (
    alert_id            TEXT PRIMARY KEY REFERENCES alerts(alert_id) ON DELETE CASCADE,
    tenant_id           UUID NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('pending','delivering','delivered','failed','acked','suppressed')),
    attempts            INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_retry_at       TIMESTAMPTZ,
    last_error_code     TEXT,
    last_error_message  TEXT,
    enqueued_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outbox_pending_ready
    ON alert_outbox (status, COALESCE(next_retry_at, enqueued_at))
    WHERE status IN ('pending','delivering');

CREATE INDEX IF NOT EXISTS idx_outbox_tenant_status
    ON alert_outbox (tenant_id, status);

ALTER TABLE alert_outbox ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS alert_outbox_tenant_isolation ON alert_outbox;
CREATE POLICY alert_outbox_tenant_isolation ON alert_outbox
    USING (tenant_id::text = current_setting('app.current_tenant', TRUE))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant', TRUE));

-- =====================================================================
-- alert_deliveries (immutable attempt log)
-- =====================================================================
CREATE TABLE IF NOT EXISTS alert_deliveries (
    delivery_id     BIGSERIAL PRIMARY KEY,
    alert_id        TEXT NOT NULL REFERENCES alerts(alert_id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL,
    channel         TEXT NOT NULL,
    attempt_no      INTEGER NOT NULL CHECK (attempt_no >= 1),
    status          TEXT NOT NULL CHECK (status IN ('success','failure','skipped','timeout')),
    http_status     INTEGER,
    latency_ms      DOUBLE PRECISION NOT NULL CHECK (latency_ms >= 0),
    error_code      TEXT,
    error_message   TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_deliveries_alert    ON alert_deliveries (alert_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_deliveries_tenant   ON alert_deliveries (tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_deliveries_channel  ON alert_deliveries (channel, occurred_at DESC);

ALTER TABLE alert_deliveries ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS alert_deliveries_tenant_isolation ON alert_deliveries;
CREATE POLICY alert_deliveries_tenant_isolation ON alert_deliveries
    USING (tenant_id::text = current_setting('app.current_tenant', TRUE))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant', TRUE));

-- =====================================================================
-- execution_intents (Phase 6 handoff, read-only from Phase 4's POV)
-- =====================================================================
CREATE TABLE IF NOT EXISTS execution_intents (
    intent_id           TEXT PRIMARY KEY,
    alert_id            TEXT NOT NULL REFERENCES alerts(alert_id) ON DELETE CASCADE,
    tenant_id           UUID NOT NULL,
    trend_id            TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('enter_position','exit_position','hold_position')),
    status              TEXT NOT NULL CHECK (status IN ('proposed','approved','rejected','executed','expired')),
    advised_units       INTEGER NOT NULL CHECK (advised_units >= 0),
    advised_capital_usd DOUBLE PRECISION NOT NULL CHECK (advised_capital_usd >= 0),
    expected_margin_usd DOUBLE PRECISION,
    loss_probability    DOUBLE PRECISION,
    horizon_hours       INTEGER NOT NULL DEFAULT 24,
    rationale           TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_intents_tenant_status ON execution_intents (tenant_id, status, created_at DESC);

ALTER TABLE execution_intents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS execution_intents_tenant_isolation ON execution_intents;
CREATE POLICY execution_intents_tenant_isolation ON execution_intents
    USING (tenant_id::text = current_setting('app.current_tenant', TRUE))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant', TRUE));

-- =====================================================================
-- killswitch_audit (append-only)
-- =====================================================================
CREATE TABLE IF NOT EXISTS killswitch_audit (
    audit_id        BIGSERIAL PRIMARY KEY,
    tenant_id       UUID,             -- nullable: a system-wide trip
    actor           TEXT NOT NULL,    -- e.g. "operator:alice" or "system:drift"
    action          TEXT NOT NULL CHECK (action IN ('trip','arm')),
    reason          TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_killswitch_audit_time ON killswitch_audit (occurred_at DESC);

-- killswitch_audit is intentionally NOT RLS-gated: ops & SREs need
-- visibility across tenants. Access is gated at the application layer.
