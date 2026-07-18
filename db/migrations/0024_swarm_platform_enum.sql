-- ============================================================
-- Migration 0024: Swarm adapter platform_enum values
-- ============================================================
-- The Swarm Intelligence Layer (migration 0005) added ~30 new adapters but
-- never extended platform_enum with their platform names. Inserting a swarm
-- signal therefore fails with:
--   invalid input value for enum platform_enum: "amazon_in"
-- This migration adds every swarm adapter platform. ADD VALUE IF NOT EXISTS is
-- idempotent and safe to replay.
--
-- NOTE: ALTER TYPE ... ADD VALUE cannot run inside an explicit transaction
-- block on PostgreSQL < 12. Run this file as standalone statements (the
-- migration runner applies each statement directly), NOT wrapped in BEGIN/COMMIT.

-- News / editorial RSS
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'techcrunch';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'wired';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'bbc_news';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'reuters';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'ndtv_profit';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'mint';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'business_standard';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'yahoo_finance';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'investing_com';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'medium';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'moneycontrol';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'economic_times';

-- Developer / tech
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'devto';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'github_public';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'npm_trends';

-- Social
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'reddit_finance';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'reddit_ecommerce';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'youtube_rss';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'google_trends_india';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'producthunt';

-- Indian markets / finance
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'nse_bse';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'screener_in';

-- E-commerce
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'amazon_in';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'flipkart';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'meesho';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'myntra';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'ajio';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'nykaa';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'snapdeal';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'indiamart';
