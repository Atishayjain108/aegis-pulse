-- ============================================================
-- Migration 0005: Swarm Intelligence Tables
-- ============================================================

-- Materialised view: per-platform signal counts (hourly, 7-day window).
-- Uses source_confidence as the score proxy — signals has no raw sentiment column.
CREATE MATERIALIZED VIEW IF NOT EXISTS platform_signal_counts AS
  SELECT
    platform,
    DATE_TRUNC('hour', scraped_at)                                           AS hour,
    COUNT(*)                                                                  AS signal_count,
    AVG(source_confidence)                                                    AS avg_score,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY source_confidence)           AS median_score
  FROM signals
  WHERE scraped_at >= NOW() - INTERVAL '7 days'
  GROUP BY platform, DATE_TRUNC('hour', scraped_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_psc_platform_hour
  ON platform_signal_counts (platform, hour);

-- Refresh function (call via pg_cron or app-level scheduler)
CREATE OR REPLACE FUNCTION refresh_platform_signal_counts()
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY platform_signal_counts;
END;
$$;

-- Swarm run results
CREATE TABLE IF NOT EXISTS swarm_results (
    run_id              UUID PRIMARY KEY,
    tenant_id           UUID NOT NULL REFERENCES tenants(tenant_id),
    started_at          TIMESTAMPTZ NOT NULL,
    finished_at         TIMESTAMPTZ NOT NULL,
    total_signals       INT NOT NULL DEFAULT 0,
    unique_signals      INT NOT NULL DEFAULT 0,
    dedup_removed       INT NOT NULL DEFAULT 0,
    by_platform         JSONB NOT NULL DEFAULT '{}',
    by_tier             JSONB NOT NULL DEFAULT '{}',
    batch_confidence    FLOAT NOT NULL DEFAULT 1.0,
    market_pulse        TEXT NOT NULL DEFAULT 'neutral',
    conclusion          TEXT NOT NULL DEFAULT '',
    cross_platform_themes JSONB NOT NULL DEFAULT '[]',
    hot_categories      JSONB NOT NULL DEFAULT '[]',
    wave_stats          JSONB NOT NULL DEFAULT '{}',   -- per-wave timing + counts
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE swarm_results ENABLE ROW LEVEL SECURITY;
CREATE POLICY swarm_tenant_isolation ON swarm_results
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

-- ON CONFLICT for idempotent inserts (used by _SwarmPersistence.publish_db)
CREATE UNIQUE INDEX IF NOT EXISTS idx_swarm_results_run_id ON swarm_results(run_id);

-- Hypertable conversion: swarm_results.run_id is a plain UUID PRIMARY KEY that
-- does not include created_at, so create_hypertable will be rejected by
-- TimescaleDB. Guard with exception so migration succeeds either way.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        BEGIN
            PERFORM create_hypertable('swarm_results', 'created_at',
                if_not_exists => TRUE, migrate_data => TRUE);
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'create_hypertable(swarm_results) skipped: % — table remains a regular Postgres table', SQLERRM;
        END;
    END IF;
END$$;

-- Agent health audit log
CREATE TABLE IF NOT EXISTS swarm_agent_health (
    id                   BIGSERIAL,
    agent_name           TEXT NOT NULL,
    tenant_id            UUID NOT NULL REFERENCES tenants(tenant_id),
    checked_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    health_status        TEXT NOT NULL,
    last_signal_count    INT NOT NULL DEFAULT 0,
    avg_latency_ms       FLOAT NOT NULL DEFAULT 0.0,
    consecutive_failures INT NOT NULL DEFAULT 0,
    last_error_type      TEXT,       -- AdapterStatus enum value
    last_error_msg       TEXT
);
ALTER TABLE swarm_agent_health ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_health_tenant_isolation ON swarm_agent_health
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

-- swarm_agent_health has no unique constraint on non-time columns, so the
-- hypertable conversion should succeed. Still wrapped defensively.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        BEGIN
            PERFORM create_hypertable('swarm_agent_health', 'checked_at',
                if_not_exists => TRUE, migrate_data => TRUE);
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'create_hypertable(swarm_agent_health) skipped: % — table remains a regular Postgres table', SQLERRM;
        END;
    END IF;
END$$;

-- Schema drift log
CREATE TABLE IF NOT EXISTS adapter_schema_fingerprints (
    platform            TEXT NOT NULL,
    fingerprint         TEXT NOT NULL,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_current          BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (platform, fingerprint)
);

-- Dedup lookup index: signals is a TimescaleDB hypertable partitioned by ts,
-- so a UNIQUE index on (platform, md5(url)) is not possible without ts.
-- Use a plain index for O(1) duplicate lookup; Python-level semantic dedup and
-- application ON CONFLICT DO NOTHING handle actual duplicate suppression.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes WHERE indexname = 'idx_signals_url_platform_dedup'
  ) THEN
    CREATE INDEX idx_signals_url_platform_dedup
      ON signals (platform, md5(url))
      WHERE url IS NOT NULL;
  END IF;
END $$;
