-- =============================================================================
-- AEGIS PULSE OMEGA v2 — Migration 0001 (initial schema)
-- -----------------------------------------------------------------------------
-- This file creates the entire Phase 1 storage layer in one transaction:
--
--   1. Extensions: timescaledb, pgvector, pg_stat_statements, btree_gist, citext
--   2. Enums: platform, source_tier, intent, modality, scrape_method, tos_risk
--   3. Core tables: signals, authors, velocity_snapshots, media, prediction_outcomes
--   4. Hypertables (signals, velocity_snapshots) — 1-day chunks
--   5. HNSW indexes on embedding columns
--   6. Continuous aggregates: signal 1h / 6h / 24h rollups
--   7. Row-Level Security policies (tenant_id)
--   8. Logical replication publication (for future read-replicas)
--   9. Retention policies
--
-- Conventions:
-- - Every table has created_at / updated_at; updated_at auto-trigger below.
-- - Every table has tenant_id uuid for future multi-tenant hosting.
-- - TIMESTAMPTZ everywhere (never TIMESTAMP) — matches pydantic AwareDatetime.
-- - pgvector columns are vector(N) with N matching EMBEDDING_DIM_* constants
--   in src/aegis/constants.py — keep them in sync.
--
-- Run via:  alembic upgrade head
-- Or raw:   psql -f db/migrations/0001_init.sql
-- =============================================================================

\echo 'AEGIS 0001_init starting...'

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Extensions
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS vector;              -- pgvector
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;  -- query performance insight
CREATE EXTENSION IF NOT EXISTS btree_gist;          -- composite GiST indexes (hypertable + filter)
CREATE EXTENSION IF NOT EXISTS citext;              -- case-insensitive text (emails, handles)
CREATE EXTENSION IF NOT EXISTS pgcrypto;            -- gen_random_uuid()

-- -----------------------------------------------------------------------------
-- 2. Enums — mirrors src/aegis/schemas/enums.py
--    CRITICAL: these string values MUST match StrEnum values 1:1.
--    If you add a member in Python, add it here and write a migration.
-- -----------------------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE platform_enum AS ENUM (
        'tiktok', 'youtube', 'instagram', 'pinterest', 'reddit',
        'amazon', 'shopify', 'etsy', 'aliexpress', 'dhgate',
        'google_shopping', 'ebay',
        'google_trends', 'meta_ad_library', 'tiktok_creative_center',
        'uspto', 'euipo', 'common_crawl',
        'x_twitter', 'nitter', 'bluesky', 'mastodon', 'gdelt',
        'twitch', 'discord', 'app_store', 'google_play',
        'github_trending', 'hacker_news', 'product_hunt',
        'shopify_apps', 'wayback_machine'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE source_tier_enum AS ENUM (
        'T1_intent', 'T2_commerce', 'T3_search', 'T4_cultural', 'T5_alternative'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE content_modality_enum AS ENUM (
        'text', 'image', 'video', 'audio', 'multimodal', 'structured'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE intent_enum AS ENUM (
        'purchase', 'save', 'search', 'engage', 'passive', 'unknown'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE scrape_method_enum AS ENUM (
        'official_api', 'public_api_unofficial', 'playwright_headless',
        'patchright_stealth', 'curl_impersonate', 'undetected_chromedriver',
        'rss_feed', 'firehose_stream'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE tos_risk_enum AS ENUM ('green', 'amber', 'red', 'black');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- -----------------------------------------------------------------------------
-- 3. Helper functions
-- -----------------------------------------------------------------------------

-- updated_at auto-setter
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER
    LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- Tenant context helper — set by the app via SET app.current_tenant = '...'
-- RLS policies consult this. Returns '00000000-...' (nil) if unset, which
-- matches no row (RLS fails safely closed).
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS UUID
    LANGUAGE plpgsql STABLE AS $$
DECLARE
    v text;
BEGIN
    v := current_setting('app.current_tenant', true);
    IF v IS NULL OR v = '' THEN
        RETURN '00000000-0000-0000-0000-000000000000'::uuid;
    END IF;
    RETURN v::uuid;
END;
$$;

-- -----------------------------------------------------------------------------
-- 4. Tenants (single-tenant in v1 but hooks wired)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name           TEXT NOT NULL UNIQUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed the default tenant (v1 deployments only).
INSERT INTO tenants (tenant_id, name)
    VALUES ('00000000-0000-0000-0000-000000000001'::uuid, 'default')
    ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- 5. authors — account dimension
--    Not time-series, so NOT a hypertable. One row per (platform, platform_user_id).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS authors (
    author_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(tenant_id),

    platform            platform_enum NOT NULL,
    platform_user_id    TEXT NOT NULL,

    handle              TEXT,
    display_name        TEXT,
    verified            BOOLEAN,
    account_created_at  TIMESTAMPTZ,
    profile_url         TEXT,
    bio_text            TEXT,

    -- Most-recently-observed counts (NOT time-series; velocity on
    -- account stats lives in a separate hypertable if we need it later).
    follower_count      INTEGER,
    following_count     INTEGER,
    total_posts         INTEGER,

    -- Creator tier classification cache (feature #9)
    creator_tier        TEXT CHECK (creator_tier IN (
        'bot', 'nano', 'micro', 'mid', 'macro', 'mega', 'unknown'
    )),
    creator_tier_confidence  REAL CHECK (creator_tier_confidence BETWEEN 0 AND 1),
    creator_tier_computed_at TIMESTAMPTZ,

    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT authors_platform_user_unique
        UNIQUE (platform, platform_user_id)
);

CREATE INDEX IF NOT EXISTS ix_authors_tenant       ON authors (tenant_id);
CREATE INDEX IF NOT EXISTS ix_authors_handle_lower ON authors (lower(handle));
CREATE INDEX IF NOT EXISTS ix_authors_last_seen    ON authors (last_seen_at DESC);

CREATE TRIGGER trg_authors_updated_at BEFORE UPDATE ON authors
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- -----------------------------------------------------------------------------
-- 6. signals — the fact table. HYPERTABLE on posted_at (fall back to scraped_at
--    if the source doesn't give us posted_at; column is NOT NULL — we coalesce
--    in the adapter).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    -- Composite key is (signal_id, ts) because hypertables require the
    -- partition column (ts) to appear in every unique constraint.
    signal_id           UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(tenant_id),
    schema_version      TEXT NOT NULL DEFAULT '1.0',

    -- Source attribution
    platform            platform_enum NOT NULL,
    tier                source_tier_enum NOT NULL,
    external_id         TEXT NOT NULL,
    url                 TEXT,

    -- Time (ts = hypertable partition key)
    ts                  TIMESTAMPTZ NOT NULL,  -- COALESCE(posted_at, scraped_at)
    posted_at           TIMESTAMPTZ,
    scraped_at          TIMESTAMPTZ NOT NULL,

    -- Content
    title               TEXT,
    raw_text            TEXT,         -- WILL be scrubbed out after PII pipeline
    pii_scrubbed_text   TEXT,         -- This is the column queries hit
    language            TEXT,
    modality            content_modality_enum NOT NULL,
    tags                TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    intent              intent_enum NOT NULL DEFAULT 'unknown',

    -- Author — FK to authors (nullable for anonymous sources).
    author_id           UUID REFERENCES authors(author_id),

    -- Engagement (denormalised for query speed; structured JSON would require
    -- a functional index for every query. Flat columns + NULLs are clearer).
    views               BIGINT,
    likes               BIGINT,
    comments            BIGINT,
    shares              BIGINT,
    saves               BIGINT,
    watch_time_seconds  DOUBLE PRECISION,
    reactions           JSONB,

    -- Price (T2 commerce only)
    price_amount        NUMERIC(14, 4),
    price_currency      CHAR(3),
    price_original      NUMERIC(14, 4),
    price_on_sale       BOOLEAN,

    -- Location (flat for index-friendliness)
    loc_country         CHAR(2),
    loc_region          TEXT,
    loc_city            TEXT,
    loc_lat             DOUBLE PRECISION CHECK (loc_lat BETWEEN -90 AND 90),
    loc_lng             DOUBLE PRECISION CHECK (loc_lng BETWEEN -180 AND 180),

    -- Provenance
    scrape_method       scrape_method_enum NOT NULL,
    scraper_version     TEXT NOT NULL,
    proxy_id            TEXT,
    user_agent          TEXT,
    ja3_fingerprint     TEXT,
    tos_risk            tos_risk_enum NOT NULL,
    rate_limit_hit      BOOLEAN NOT NULL DEFAULT FALSE,
    captcha_encountered BOOLEAN NOT NULL DEFAULT FALSE,

    -- Quality
    completeness        REAL NOT NULL CHECK (completeness BETWEEN 0 AND 1),
    source_confidence   REAL NOT NULL CHECK (source_confidence BETWEEN 0 AND 1),
    freshness_seconds   DOUBLE PRECISION,

    -- Cross-modal coherence (nullable; populated by embedding stage)
    cross_modal_sim     REAL CHECK (cross_modal_sim BETWEEN -1 AND 1),
    ai_generated_score  REAL CHECK (ai_generated_score BETWEEN 0 AND 1),

    -- Embeddings (pgvector columns). Dims MUST match EMBEDDING_DIM_* constants.
    -- Nullable because we insert the raw signal first, then embed async.
    text_embedding      vector(1024),   -- BGE-M3
    image_embedding     vector(768),    -- CLIP ViT-L/14
    cross_modal_embed   vector(512),    -- projected shared space

    -- Content addressing
    content_hash        TEXT NOT NULL,

    -- Free-form bag
    platform_specific   JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Invariants
    CONSTRAINT signals_pk PRIMARY KEY (signal_id, ts),
    CONSTRAINT signals_external_unique UNIQUE (platform, external_id, ts),
    CONSTRAINT signals_commerce_needs_price
        CHECK (tier <> 'T2_commerce' OR price_amount IS NOT NULL),
    CONSTRAINT signals_price_currency_when_amount
        CHECK (price_amount IS NULL OR price_currency IS NOT NULL)
);

CREATE TRIGGER trg_signals_updated_at BEFORE UPDATE ON signals
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ==== Hypertable: signals ====================================================
-- 1-day chunks (prompt spec). if_not_exists prevents failure on re-run.
SELECT create_hypertable(
    'signals', 'ts',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- ==== Indexes on signals =====================================================
-- Queries we know we'll run:
--   a) Latest N signals per (platform, tier) — dashboard live feed.
--   b) Signals by author, time-ordered — creator-economy features.
--   c) Signals by tag (GIN) — trend discovery.
--   d) Content-hash dedup — idempotent ingestion.
--   e) Vector similarity — kNN over text/image embeddings (HNSW).
CREATE INDEX IF NOT EXISTS ix_signals_tenant_ts
    ON signals (tenant_id, ts DESC);
CREATE INDEX IF NOT EXISTS ix_signals_platform_ts
    ON signals (platform, ts DESC);
CREATE INDEX IF NOT EXISTS ix_signals_tier_ts
    ON signals (tier, ts DESC);
CREATE INDEX IF NOT EXISTS ix_signals_author_ts
    ON signals (author_id, ts DESC)
    WHERE author_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_signals_tags_gin
    ON signals USING gin (tags);
CREATE INDEX IF NOT EXISTS ix_signals_content_hash
    ON signals (content_hash);
CREATE INDEX IF NOT EXISTS ix_signals_intent_ts
    ON signals (intent, ts DESC)
    WHERE intent IN ('purchase', 'save', 'search');
CREATE INDEX IF NOT EXISTS ix_signals_loc_country_ts
    ON signals (loc_country, ts DESC) WHERE loc_country IS NOT NULL;

-- JSONB GIN for platform_specific arbitrary queries.
CREATE INDEX IF NOT EXISTS ix_signals_platform_specific
    ON signals USING gin (platform_specific jsonb_path_ops);

-- ==== Vector indexes (HNSW) ==================================================
-- Prompt-specified: HNSW over IVFFlat (better recall/latency at our scale).
-- m = 16, ef_construction = 64 are pgvector defaults that work well up to ~10M rows.
-- Cosine ops because our embeddings are L2-normalised.
-- NOTE: HNSW build is O(N log N) — on an empty table this is instant; on a
-- backfill migration, consider running CREATE INDEX CONCURRENTLY.
CREATE INDEX IF NOT EXISTS ix_signals_text_embedding_hnsw
    ON signals USING hnsw (text_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE text_embedding IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_signals_image_embedding_hnsw
    ON signals USING hnsw (image_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE image_embedding IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_signals_cross_modal_hnsw
    ON signals USING hnsw (cross_modal_embed vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE cross_modal_embed IS NOT NULL;

-- -----------------------------------------------------------------------------
-- 7. media — referenced by signals; hypertable so large TikTok hauls don't
--    blow up a single btree.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS media (
    media_id            UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(tenant_id),
    signal_id           UUID NOT NULL,
    signal_ts           TIMESTAMPTZ NOT NULL,

    url                 TEXT NOT NULL,
    modality            content_modality_enum NOT NULL,
    width_px            INTEGER,
    height_px           INTEGER,
    duration_seconds    DOUBLE PRECISION,
    byte_size           BIGINT,
    content_hash        TEXT,

    -- When fetched, where stored (MinIO bucket/key).
    minio_bucket        TEXT,
    minio_key           TEXT,
    fetched_at          TIMESTAMPTZ,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT media_pk PRIMARY KEY (media_id, signal_ts),
    -- No FK: FK to hypertables is not fully supported. We enforce via app code.
    CONSTRAINT media_mime_width_sanity
        CHECK ((modality IN ('image', 'video')) = (width_px IS NOT NULL))
);

SELECT create_hypertable(
    'media', 'signal_ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS ix_media_signal       ON media (signal_id, signal_ts DESC);
CREATE INDEX IF NOT EXISTS ix_media_content_hash ON media (content_hash) WHERE content_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_media_unfetched    ON media (signal_ts) WHERE fetched_at IS NULL;

-- -----------------------------------------------------------------------------
-- 8. velocity_snapshots — precomputed velocity over rolling windows.
--    Written by the feature pipeline; read heavily by agents. Hypertable.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS velocity_snapshots (
    snapshot_id         UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(tenant_id),
    ts                  TIMESTAMPTZ NOT NULL,

    -- What we measured velocity for — a tag, hashtag, product cluster, etc.
    subject_type        TEXT NOT NULL CHECK (subject_type IN (
        'tag', 'hashtag', 'product', 'creator', 'cluster'
    )),
    subject_key         TEXT NOT NULL,

    -- Windows (seconds) and their computed derivatives.
    window_seconds      INTEGER NOT NULL CHECK (window_seconds > 0),
    signal_count        BIGINT NOT NULL CHECK (signal_count >= 0),
    velocity            DOUBLE PRECISION NOT NULL,       -- dN/dt
    acceleration        DOUBLE PRECISION NOT NULL,       -- d²N/dt²
    ema_velocity        DOUBLE PRECISION,                -- exponential moving avg
    z_score             DOUBLE PRECISION,                -- vs historical baseline

    -- Platforms contributing (so we can detect cross-platform lag feature).
    platforms           platform_enum[] NOT NULL DEFAULT ARRAY[]::platform_enum[],

    CONSTRAINT velocity_pk PRIMARY KEY (snapshot_id, ts)
);

SELECT create_hypertable(
    'velocity_snapshots', 'ts',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS ix_velocity_subject_ts
    ON velocity_snapshots (subject_type, subject_key, ts DESC);
CREATE INDEX IF NOT EXISTS ix_velocity_window_ts
    ON velocity_snapshots (window_seconds, ts DESC);
CREATE INDEX IF NOT EXISTS ix_velocity_hot
    ON velocity_snapshots (z_score DESC, ts DESC)
    WHERE z_score > 2.0;

-- -----------------------------------------------------------------------------
-- 9. prediction_outcomes — feedback intelligence: every prediction's realized
--    result. Feature #5 in the core principles: "every prediction is a future
--    training label". Hypertable by made_at.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prediction_outcomes (
    outcome_id          UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(tenant_id),

    made_at             TIMESTAMPTZ NOT NULL,
    resolved_at         TIMESTAMPTZ,

    -- Pointers back into predictions (tables defined in Phase 3).
    -- Keep as raw UUIDs — no FK because prediction table is in a separate phase.
    prediction_id       UUID NOT NULL,
    model_version       TEXT NOT NULL,

    -- What was predicted (free-form enough to cover all model heads).
    prediction_label    TEXT NOT NULL,
    prediction_score    DOUBLE PRECISION NOT NULL,
    prediction_ci_low   DOUBLE PRECISION,
    prediction_ci_high  DOUBLE PRECISION,

    -- Realised outcome.
    realized_label      TEXT,
    realized_score      DOUBLE PRECISION,
    is_correct          BOOLEAN,
    profit_usd          NUMERIC(14, 4),   -- for execution predictions
    drawdown_usd        NUMERIC(14, 4),

    -- Signed digest for Phase 20 audit trail.
    ed25519_signature   BYTEA,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT outcomes_pk PRIMARY KEY (outcome_id, made_at)
);

SELECT create_hypertable(
    'prediction_outcomes', 'made_at',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS ix_outcomes_prediction
    ON prediction_outcomes (prediction_id, made_at DESC);
CREATE INDEX IF NOT EXISTS ix_outcomes_model_ver
    ON prediction_outcomes (model_version, made_at DESC);
CREATE INDEX IF NOT EXISTS ix_outcomes_unresolved
    ON prediction_outcomes (made_at DESC) WHERE resolved_at IS NULL;

-- -----------------------------------------------------------------------------
-- 10. Continuous aggregates (TimescaleDB) — incremental 1h/6h/24h rollups.
--     Consumed by the dashboard and by the HEDGE/SENTINEL agents.
-- -----------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS signals_hourly
WITH (timescaledb.continuous) AS
SELECT
    tenant_id,
    platform,
    tier,
    time_bucket('1 hour', ts)            AS bucket,
    count(*)                             AS signal_count,
    count(DISTINCT author_id)            AS unique_authors,
    count(*) FILTER (WHERE intent = 'purchase') AS purchase_intent_count,
    count(*) FILTER (WHERE intent = 'save')     AS save_intent_count,
    avg(source_confidence)               AS avg_confidence,
    sum(likes)                           AS total_likes,
    sum(shares)                          AS total_shares
FROM signals
GROUP BY tenant_id, platform, tier, bucket
WITH NO DATA;  -- materialize asynchronously

SELECT add_continuous_aggregate_policy('signals_hourly',
    start_offset => INTERVAL '3 days',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE
);

CREATE MATERIALIZED VIEW IF NOT EXISTS signals_6hourly
WITH (timescaledb.continuous) AS
SELECT
    tenant_id,
    platform,
    tier,
    time_bucket('6 hours', ts)           AS bucket,
    count(*)                             AS signal_count,
    count(DISTINCT author_id)            AS unique_authors,
    avg(source_confidence)               AS avg_confidence
FROM signals
GROUP BY tenant_id, platform, tier, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy('signals_6hourly',
    start_offset => INTERVAL '14 days',
    end_offset   => INTERVAL '6 hours',
    schedule_interval => INTERVAL '2 hours',
    if_not_exists => TRUE
);

CREATE MATERIALIZED VIEW IF NOT EXISTS signals_daily
WITH (timescaledb.continuous) AS
SELECT
    tenant_id,
    platform,
    tier,
    time_bucket('1 day', ts)             AS bucket,
    count(*)                             AS signal_count,
    count(DISTINCT author_id)            AS unique_authors,
    avg(source_confidence)               AS avg_confidence
FROM signals
GROUP BY tenant_id, platform, tier, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy('signals_daily',
    start_offset => INTERVAL '90 days',
    end_offset   => INTERVAL '1 day',
    schedule_interval => INTERVAL '12 hours',
    if_not_exists => TRUE
);

-- -----------------------------------------------------------------------------
-- 11. Retention policies — keep 90 days of raw signals, forever on aggregates.
--     Raw signals that fall out of hot storage go to MinIO as Parquet audit
--     trail before the chunk is dropped (handled in app-level archival job).
-- -----------------------------------------------------------------------------
SELECT add_retention_policy('signals',
    drop_after => INTERVAL '90 days',
    if_not_exists => TRUE
);
SELECT add_retention_policy('media',
    drop_after => INTERVAL '90 days',
    if_not_exists => TRUE
);
SELECT add_retention_policy('velocity_snapshots',
    drop_after => INTERVAL '180 days',
    if_not_exists => TRUE
);

-- -----------------------------------------------------------------------------
-- 12. Row-Level Security
--     Every table is RLS-enabled. Policies match tenant_id against the
--     session's app.current_tenant setting.
-- -----------------------------------------------------------------------------
ALTER TABLE tenants             ENABLE ROW LEVEL SECURITY;
ALTER TABLE authors             ENABLE ROW LEVEL SECURITY;
ALTER TABLE signals             ENABLE ROW LEVEL SECURITY;
ALTER TABLE media               ENABLE ROW LEVEL SECURITY;
ALTER TABLE velocity_snapshots  ENABLE ROW LEVEL SECURITY;
ALTER TABLE prediction_outcomes ENABLE ROW LEVEL SECURITY;

-- A "rls_read" and "rls_write" policy per table. App role must match tenant.
-- Superusers bypass RLS (ensure app doesn't connect as superuser in prod).
DROP POLICY IF EXISTS p_tenants_read ON tenants;
CREATE POLICY p_tenants_read ON tenants
    FOR SELECT USING (tenant_id = current_tenant_id());

DROP POLICY IF EXISTS p_authors_rw ON authors;
CREATE POLICY p_authors_rw ON authors
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

DROP POLICY IF EXISTS p_signals_rw ON signals;
CREATE POLICY p_signals_rw ON signals
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

DROP POLICY IF EXISTS p_media_rw ON media;
CREATE POLICY p_media_rw ON media
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

DROP POLICY IF EXISTS p_velocity_rw ON velocity_snapshots;
CREATE POLICY p_velocity_rw ON velocity_snapshots
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

DROP POLICY IF EXISTS p_outcomes_rw ON prediction_outcomes;
CREATE POLICY p_outcomes_rw ON prediction_outcomes
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

-- -----------------------------------------------------------------------------
-- 13. Roles — app role with least privilege. Used by asyncpg pool.
--     Password is set by the bootstrapper (read from Vault/SOPS).
-- -----------------------------------------------------------------------------
DO $$ BEGIN
    CREATE ROLE aegis_app NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

GRANT USAGE ON SCHEMA public TO aegis_app;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO aegis_app;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO aegis_app;
-- Future tables inherit these grants.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO aegis_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON SEQUENCES TO aegis_app;

-- NEVER grant DELETE/TRUNCATE to aegis_app. Retention is handled by Timescale's
-- drop_chunks, which requires owner rights; admin scripts connect as a
-- separate migrate role.
DO $$ BEGIN
    CREATE ROLE aegis_migrate NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

GRANT ALL ON ALL TABLES IN SCHEMA public TO aegis_migrate;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO aegis_migrate;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL ON TABLES TO aegis_migrate;

-- -----------------------------------------------------------------------------
-- 14. Logical replication publication — read replica hook (prompt-specified).
-- -----------------------------------------------------------------------------
DO $$ BEGIN
    CREATE PUBLICATION aegis_pub FOR TABLE
        tenants, authors, signals, media, velocity_snapshots, prediction_outcomes;
EXCEPTION WHEN duplicate_object THEN
    -- Already exists; ensure it covers the right tables.
    ALTER PUBLICATION aegis_pub SET TABLE
        tenants, authors, signals, media, velocity_snapshots, prediction_outcomes;
END $$;

-- NOTE: Chunk compression (timescaledb.compress) is intentionally omitted
-- here.  TimescaleDB 2.25+ uses "columnstore" compression which is
-- incompatible with Row-Level Security on the same table (error:
-- "columnstore cannot be used on table with row security").  Compression
-- will be enabled in a separate migration once TimescaleDB ships a fix, or
-- when we move to a version that re-supports rowstore compression with RLS.

-- -----------------------------------------------------------------------------
-- Done.
-- -----------------------------------------------------------------------------
COMMIT;

\echo 'AEGIS 0001_init complete.'
