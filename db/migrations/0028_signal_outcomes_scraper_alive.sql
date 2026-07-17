-- 0028_signal_outcomes_scraper_alive.sql
-- Recovery protocol STAGE 1.3 — quarantine the poisoned labels.
--
-- Context: ingestion was dead 2026-07-03 05:47 .. 2026-07-16 UTC (import
-- failure in the autonomous image). The settlement loop kept settling
-- claims against the dead signals table: 633 settlements in that era are
-- measurements of a dead scraper, not of markets (fall-claims 97.1%
-- "correct", rise-claims 16.4%). They share signal_outcomes with the
-- calibration refit; left unflagged they would be baked into the isotonic
-- confidence map.
--
-- window_scraper_alive semantics:
--   TRUE  — ingestion was continuously alive across the claim's observation
--           window (no scraped_at gap > AEGIS_INGEST_HEALTH_MAX_AGE_H,
--           default 2h — the SAME freshness definition as the Block-D
--           container healthcheck; one notion of "alive", not two).
--   FALSE — window overlaps a known ingestion gap; label is void of market
--           meaning. Rows are RETAINED (never deleted) and excluded from
--           every calibration/accuracy/knowledge consumer.
--   NULL  — pending claims: decided at settlement time by the live
--           precondition in SignalOutcomeSettler.
--
-- resolution_status gains 'void': a claim whose observation window had an
-- ingestion gap settles to VOID, not to a direction.

ALTER TABLE signal_outcomes
    ADD COLUMN IF NOT EXISTS window_scraper_alive BOOLEAN;

ALTER TABLE signal_outcomes
    DROP CONSTRAINT IF EXISTS signal_outcomes_resolution_status_check;
ALTER TABLE signal_outcomes
    ADD CONSTRAINT signal_outcomes_resolution_status_check
    CHECK (resolution_status = ANY (ARRAY[
        'pending'::text, 'correct'::text, 'incorrect'::text, 'void'::text
    ]));

COMMENT ON COLUMN signal_outcomes.window_scraper_alive IS
    'TRUE = ingestion continuously alive across the observation window '
    '(max scraped_at gap <= AEGIS_INGEST_HEALTH_MAX_AGE_H); FALSE = window '
    'overlaps an ingestion gap, label void of market meaning; NULL = pending. '
    'All calibration/accuracy consumers MUST filter = TRUE. Stage 1.3, 2026-07-17.';
