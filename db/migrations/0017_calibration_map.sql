-- =============================================================================
-- 0017_calibration_map.sql — PROJECT OMEGA Phase C (Truthful Predictor)
--
-- Stores the fitted isotonic calibration map that converts the heuristic's raw
-- directional probability (1 - p_decline) into a calibrated P(rise). One latest
-- map per (tenant, name). The emitter applies it so stored/served confidence is
-- truthful; refit periodically from realized signal_outcomes.
-- =============================================================================

CREATE TABLE IF NOT EXISTS calibration_maps (
    tenant_id   UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    name        TEXT        NOT NULL DEFAULT 'heuristic_rise',
    knots_json  JSONB       NOT NULL DEFAULT '{}',
    n_fit       INT         NOT NULL DEFAULT 0,
    base_rate   DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, name)
);

ALTER TABLE calibration_maps ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS calibration_maps_tenant ON calibration_maps;
CREATE POLICY calibration_maps_tenant ON calibration_maps
    USING (tenant_id = current_setting('app.current_tenant', TRUE)::UUID);
