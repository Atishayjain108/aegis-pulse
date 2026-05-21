-- =============================================================================
-- Migration 0004: Add new platform enum values for topic-based scraping
-- =============================================================================
-- These platforms were added to support the `aegis topic` command which
-- scrapes news and web sources without requiring API keys.
-- Postgres 9.1+ supports ADD VALUE IF NOT EXISTS for ALTER TYPE ... ADD VALUE.
-- =============================================================================

ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'google_news';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'bing_news';
