-- ====================================================================
-- AEGIS Pulse — Phase 3 schema migration
--   0002_predictions.sql
--
-- Adds:
--   * predictions     — one row per PredictionRecord
--   * prediction_audit — sidecar (halt_reasons, causal, graph_summary)
--   * model_manifest  — registry mirror (the on-disk ModelStore is
--                        authoritative; this table exists so SQL
--                        consumers — Grafana, Superset — can join)
--   * backtest_results — one row per fold (matches BacktestResult)
--
-- All tables:
--   * tenant_id with RLS — same model as Phase 1
--   * timezone-aware UTC timestamps
--   * created_at default now() — never trust client clocks
--   * indexes for the queries the UI / alerting actually run
--
-- Idempotency: every CREATE uses IF NOT EXISTS so re-applying the
-- migration is safe. Schema changes go in NEW migration files, not
-- by editing this one.
-- ====================================================================

BEGIN;

-- --------------------------------------------------------------------
-- predictions
-- --------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id     TEXT PRIMARY KEY,
    tenant_id         UUID NOT NULL,
    trend_id          TEXT NOT NULL,
    correlation_id    TEXT NOT NULL,

    -- Bundle-level metadata (denormalised from `bundle_json` for fast filters).
    model_id          TEXT NOT NULL,
    model_name        TEXT NOT NULL DEFAULT 'heuristic',
    model_kind        TEXT NOT NULL,
    model_version     TEXT NOT NULL,
    schema_version    TEXT NOT NULL,
    is_heuristic_only BOOLEAN NOT NULL DEFAULT TRUE,
    seed              BIGINT NOT NULL DEFAULT 0,

    -- Lifecycle.
    started_at        TIMESTAMPTZ NOT NULL,
    finished_at       TIMESTAMPTZ NOT NULL,
    duration_ms       DOUBLE PRECISION NOT NULL,

    -- Replay key.
    feature_window_hash TEXT NOT NULL,

    -- The full PredictionBundle as JSON — the canonical record.
    bundle_json       JSONB NOT NULL,

    -- Tamper-evidence.
    signature         TEXT NOT NULL,        -- Ed25519 hex; "UNSIGNED" before Phase 20

    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (duration_ms >= 0),
    CHECK (finished_at >= started_at)
);

-- Tenant scoping (Row-Level Security — same convention as Phase 1).
ALTER TABLE predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE predictions FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS predictions_tenant_isolation ON predictions;
CREATE POLICY predictions_tenant_isolation
    ON predictions
    USING (tenant_id::text = current_setting('app.current_tenant', TRUE))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant', TRUE));

CREATE INDEX IF NOT EXISTS idx_predictions_tenant_trend_finished
    ON predictions (tenant_id, trend_id, finished_at DESC);

CREATE INDEX IF NOT EXISTS idx_predictions_finished_at
    ON predictions (finished_at DESC);

CREATE INDEX IF NOT EXISTS idx_predictions_correlation_id
    ON predictions (correlation_id);

CREATE INDEX IF NOT EXISTS idx_predictions_model_id
    ON predictions (model_id);

-- TimescaleDB hypertable — 1-day chunks. Lets us drop chunks > 90d cheaply.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        PERFORM create_hypertable(
            'predictions',
            'finished_at',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists       => TRUE,
            migrate_data        => TRUE
        );
    END IF;
END$$;


-- --------------------------------------------------------------------
-- prediction_audit
-- --------------------------------------------------------------------
-- Sidecar — never read by the runtime, only by humans + offline tools.
-- Append-only. We keep it in the same DB (rather than only on MinIO)
-- so an operator triaging an alert can JOIN against it.
CREATE TABLE IF NOT EXISTS prediction_audit (
    correlation_id    TEXT PRIMARY KEY REFERENCES predictions(correlation_id)
                          DEFERRABLE INITIALLY DEFERRED,
    tenant_id         UUID NOT NULL,
    trend_id          TEXT NOT NULL,
    bundle_id         TEXT NOT NULL,
    halt_reasons      TEXT[] NOT NULL DEFAULT '{}',
    causal_top        JSONB NOT NULL DEFAULT '[]'::jsonb,
    graph_summary     JSONB NOT NULL DEFAULT '{}'::jsonb,
    duration_ms       DOUBLE PRECISION NOT NULL,
    is_heuristic_only BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE prediction_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE prediction_audit FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS prediction_audit_tenant_isolation ON prediction_audit;
CREATE POLICY prediction_audit_tenant_isolation
    ON prediction_audit
    USING (tenant_id::text = current_setting('app.current_tenant', TRUE))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant', TRUE));

CREATE INDEX IF NOT EXISTS idx_prediction_audit_tenant_trend
    ON prediction_audit (tenant_id, trend_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_prediction_audit_halt_reasons
    ON prediction_audit USING GIN (halt_reasons);


-- --------------------------------------------------------------------
-- model_manifest (registry mirror)
-- --------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_manifest (
    model_id          TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    kind              TEXT NOT NULL,
    version           TEXT NOT NULL,
    schema_version    TEXT NOT NULL,
    weights_uri       TEXT NOT NULL,
    onnx_uri          TEXT NOT NULL DEFAULT '',
    sha256            TEXT NOT NULL,
    trained_at        TIMESTAMPTZ NOT NULL,
    train_window_start TIMESTAMPTZ NOT NULL,
    train_window_end   TIMESTAMPTZ NOT NULL,
    n_train_samples   BIGINT NOT NULL,
    backtest_summary  JSONB NOT NULL DEFAULT '{}'::jsonb,
    stage             TEXT NOT NULL DEFAULT 'staging'
                          CHECK (stage IN ('staging','shadow','production','archived')),
    notes             TEXT NOT NULL DEFAULT '',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);

CREATE INDEX IF NOT EXISTS idx_model_manifest_name_stage
    ON model_manifest (name, stage);

CREATE INDEX IF NOT EXISTS idx_model_manifest_trained_at
    ON model_manifest (trained_at DESC);


-- --------------------------------------------------------------------
-- backtest_results
-- --------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS backtest_results (
    backtest_id       BIGSERIAL PRIMARY KEY,
    model_id          TEXT NOT NULL REFERENCES model_manifest(model_id) ON DELETE CASCADE,
    fold_index        INTEGER NOT NULL,
    train_start       TIMESTAMPTZ NOT NULL,
    train_end         TIMESTAMPTZ NOT NULL,
    test_start        TIMESTAMPTZ NOT NULL,
    test_end          TIMESTAMPTZ NOT NULL,
    n_train           BIGINT NOT NULL,
    n_test            BIGINT NOT NULL,

    accuracy            DOUBLE PRECISION NOT NULL,
    macro_f1            DOUBLE PRECISION NOT NULL,
    breakout_precision  DOUBLE PRECISION NOT NULL,
    breakout_recall     DOUBLE PRECISION NOT NULL,
    mae_log_velocity    DOUBLE PRECISION NOT NULL,
    pinball_p10         DOUBLE PRECISION NOT NULL,
    pinball_p90         DOUBLE PRECISION NOT NULL,
    ece                 DOUBLE PRECISION NOT NULL,
    coverage_90         DOUBLE PRECISION NOT NULL,
    sharpness           DOUBLE PRECISION NOT NULL,
    expected_pnl        DOUBLE PRECISION NOT NULL DEFAULT 0,
    max_drawdown        DOUBLE PRECISION NOT NULL DEFAULT 0,
    notes               TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (train_end <= test_start),
    UNIQUE (model_id, fold_index)
);

CREATE INDEX IF NOT EXISTS idx_backtest_results_model
    ON backtest_results (model_id, fold_index);


-- --------------------------------------------------------------------
-- Continuous aggregate: daily breakout precision per model.
-- Used by the auto-rollback job to detect production drift.
-- --------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        EXECUTE $caggs$
            CREATE MATERIALIZED VIEW IF NOT EXISTS predictions_daily_summary
            WITH (timescaledb.continuous) AS
            SELECT
                tenant_id,
                model_id,
                time_bucket(INTERVAL '1 day', finished_at) AS day,
                COUNT(*)                                      AS n_predictions,
                AVG(duration_ms)                              AS avg_duration_ms,
                COUNT(*) FILTER (
                    WHERE (bundle_json->'predictions'->0->>'p_breakout')::float >= 0.55
                ) AS n_breakout_calls
            FROM predictions
            GROUP BY tenant_id, model_id, day
            WITH NO DATA;
        $caggs$;
    END IF;
END$$;

COMMIT;
