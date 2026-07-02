-- ============================================================
-- Migration 0025: Global radar adapter platform_enum values
-- ============================================================
-- Phase 5 global radar adapters give AEGIS planetary coverage (every region,
-- country and continent) via keyless public APIs: GDELT (global news), Wikimedia
-- (pageview demand), and multi-region Google Trends. Inserting their signals
-- fails without these enum values:
--   invalid input value for enum platform_enum: "gdelt"
-- ADD VALUE IF NOT EXISTS is idempotent and safe to replay.
--
-- NOTE: ALTER TYPE ... ADD VALUE cannot run inside an explicit transaction
-- block on PostgreSQL < 12. Run as standalone statements, NOT wrapped in
-- BEGIN/COMMIT.

ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'gdelt';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'wikimedia';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'google_trends_global';
