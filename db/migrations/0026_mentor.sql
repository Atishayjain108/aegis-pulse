-- =============================================================================
-- 0026_mentor.sql — AEGIS Mentor (the counsel engine)
--
-- Persists user profiles (who the user is — sector + intent + autonomy), the
-- append-only history of advice given, and detected knowledge gaps that drive
-- the teaching loop. Profiles are personal data → RLS on every table.
-- =============================================================================

-- One person → one evolving profile.
CREATE TABLE IF NOT EXISTS user_profiles (
    profile_id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id           UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    raw_description     TEXT        NOT NULL DEFAULT '',
    -- The keystone fields.
    sector              TEXT        NOT NULL DEFAULT 'undecided',
    sector_raw          TEXT        NOT NULL DEFAULT '',
    intent              TEXT        NOT NULL DEFAULT 'unknown'
        CHECK (intent IN ('income', 'learning', 'scale', 'research', 'validate', 'unknown')),
    autonomy_preference TEXT        NOT NULL DEFAULT 'coach_me'
        CHECK (autonomy_preference IN ('guide_me', 'coach_me', 'answer_me')),
    -- Tuning fields.
    capital_usd         DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (capital_usd >= 0),
    risk_tolerance      TEXT        NOT NULL DEFAULT 'med'
        CHECK (risk_tolerance IN ('low', 'med', 'high')),
    channels            TEXT        NOT NULL DEFAULT 'online_only'
        CHECK (channels IN ('online_only', 'local', 'both')),
    skills              JSONB       NOT NULL DEFAULT '[]',
    constraints         JSONB       NOT NULL DEFAULT '[]',
    time_per_week_hrs   DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (time_per_week_hrs >= 0),
    experience_level    TEXT        NOT NULL DEFAULT 'none'
        CHECK (experience_level IN ('none', 'some', 'experienced')),
    region              TEXT        NOT NULL DEFAULT 'IN',
    currency            TEXT        NOT NULL DEFAULT 'INR',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS user_profiles_tenant ON user_profiles;
CREATE POLICY user_profiles_tenant ON user_profiles
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);

CREATE INDEX IF NOT EXISTS idx_user_profiles_tenant_sector
    ON user_profiles (tenant_id, sector);


-- Append-only history of advice given.
CREATE TABLE IF NOT EXISTS mentor_sessions (
    session_id              UUID        NOT NULL DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    profile_id              UUID        NOT NULL,
    query                   TEXT        NOT NULL DEFAULT '',
    matched_opportunity_ids JSONB       NOT NULL DEFAULT '[]',
    counsel                 JSONB       NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_id, created_at)
);

ALTER TABLE mentor_sessions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mentor_sessions_tenant ON mentor_sessions;
CREATE POLICY mentor_sessions_tenant ON mentor_sessions
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);

CREATE INDEX IF NOT EXISTS idx_mentor_sessions_profile
    ON mentor_sessions (profile_id, created_at DESC);


-- Knowledge gaps drive the teaching loop.
CREATE TABLE IF NOT EXISTS knowledge_gaps (
    gap_id           UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id        UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    profile_id       UUID        NOT NULL,
    topic            TEXT        NOT NULL,
    detected_from    TEXT        NOT NULL DEFAULT '',
    lesson_delivered BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE knowledge_gaps ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS knowledge_gaps_tenant ON knowledge_gaps;
CREATE POLICY knowledge_gaps_tenant ON knowledge_gaps
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);

CREATE INDEX IF NOT EXISTS idx_knowledge_gaps_profile
    ON knowledge_gaps (profile_id, created_at DESC);
