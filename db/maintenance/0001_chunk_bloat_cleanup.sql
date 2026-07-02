-- Maintenance: clear TimescaleDB chunk bloat (audit P4-1)
--
-- The `signals` hypertable had accumulated 1012 chunks, 921 of them EMPTY and
-- spanning ranges back to 2007 — the residue of a since-fixed timestamp bug that
-- once inserted rows with very old scraped_at. The 90-day retention policy runs
-- (job status Success) but had not reclaimed these old/empty chunks. Every query
-- paid a planning cost proportional to ~1000 chunks.
--
-- This script drops chunks whose entire range is older than the retention window.
-- SAFE: verified 0 live rows older than 90 days before running
--   (SELECT count(*) FROM signals WHERE scraped_at < now() - INTERVAL '90 days';)
-- Result: signals 1012 -> 91 chunks, zero rows lost (min(scraped_at) unchanged).
--
-- Run as a role that can drop chunks (table owner / superuser):
--   psql "$AEGIS_PG_DSN" -f db/maintenance/0001_chunk_bloat_cleanup.sql
-- Idempotent: re-running only drops whatever is now older than the window.

SELECT drop_chunks('signals',             older_than => INTERVAL '90 days');
SELECT drop_chunks('media',               older_than => INTERVAL '180 days');
SELECT drop_chunks('velocity_snapshots',  older_than => INTERVAL '180 days');
SELECT drop_chunks('signal_outcomes',     older_than => INTERVAL '180 days');
SELECT drop_chunks('prediction_outcomes', older_than => INTERVAL '180 days');

-- NOTE (audit P4-2): native columnar compression was ATTEMPTED but TimescaleDB
-- rejects it on RLS-enabled tables:
--   ERROR: columnstore cannot be used on table with row security
-- signals/media/velocity_snapshots all use RLS (SET app.current_tenant), so
-- compression is blocked by design in this engine version. Options (deferred,
-- need a decision): (a) move compression to a non-RLS mirror, (b) upgrade to a
-- TimescaleDB version that supports RLS + hypercore, or (c) accept uncompressed
-- storage given the 90/180-day retention already caps growth.
