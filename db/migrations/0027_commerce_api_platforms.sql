-- ============================================================
-- Migration 0027: Free commerce-API adapter platform_enum values
-- ============================================================
-- The eBay Browse, Best Buy Products, and Etsy Open API adapters
-- (src/aegis/scrape/sources/{ebay_browse,bestbuy,etsy}.py) emit real
-- marketplace listings with prices. `etsy` and `ebay` were already present in
-- platform_enum, but `bestbuy` was MISSING — so a Best Buy fetch with a valid
-- API key crashes on insert with:
--   invalid input value for enum platform_enum: "bestbuy"
-- This silently broke the BestBuy adapter end-to-end at the DB layer while the
-- adapter, swarm registration, and CLI all looked correct.
--
-- ADD VALUE IF NOT EXISTS is idempotent and safe to replay; the ebay/etsy
-- lines are no-ops on existing databases and self-document the full set.
--
-- NOTE: ALTER TYPE ... ADD VALUE cannot run inside an explicit transaction
-- block. Run as standalone statements, NOT wrapped in BEGIN/COMMIT.

ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'ebay';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'etsy';
ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'bestbuy';
