"""aegis.trust.store — persist calibration snapshots + trust scores (Phase B)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog

from aegis.trust.calibrator import Calibrator
from aegis.trust.schemas import CalibrationReport, TrustScore

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.trust.store")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


class TrustStore:
    """Best-effort persistence — write failures log, never raise."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id

    async def save_snapshot(self, report: CalibrationReport) -> bool:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                await conn.execute(
                    """
                    INSERT INTO calibration_snapshots
                        (entity_kind, entity_id, n, status, ece, mce, brier,
                         brier_skill, base_rate, bins)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                    """,
                    report.entity_kind, report.entity_id, report.n, report.status,
                    report.ece, report.mce, report.brier, report.brier_skill_score,
                    report.base_rate,
                    json.dumps([b.model_dump() for b in report.bins]),
                )
            return True
        except Exception as exc:
            _log.error("trust.snapshot_save_failed", error=str(exc))
            return False

    async def save_map(self, calibrator: Calibrator, *, name: str = "heuristic_rise") -> bool:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                await conn.execute(
                    """
                    INSERT INTO calibration_maps (name, knots_json, n_fit, base_rate, updated_at)
                    VALUES ($1, $2, $3, $4, NOW())
                    ON CONFLICT (tenant_id, name) DO UPDATE SET
                        knots_json = EXCLUDED.knots_json,
                        n_fit = EXCLUDED.n_fit,
                        base_rate = EXCLUDED.base_rate,
                        updated_at = EXCLUDED.updated_at
                    """,
                    name, calibrator.to_json(), calibrator.n_fit, calibrator.base_rate,
                )
            return True
        except Exception as exc:
            _log.error("trust.map_save_failed", error=str(exc))
            return False

    async def load_map(self, *, name: str = "heuristic_rise") -> Calibrator:
        """Load the latest calibration map; identity when none exists."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                row = await conn.fetchrow(
                    "SELECT knots_json FROM calibration_maps WHERE name = $1", name
                )
            if row and row["knots_json"]:
                return Calibrator.from_json(row["knots_json"])
        except Exception as exc:
            _log.error("trust.map_load_failed", error=str(exc))
        return Calibrator.identity()

    async def save_trust(self, score: TrustScore) -> bool:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                await conn.execute(
                    """
                    INSERT INTO trust_scores
                        (entity_kind, entity_id, trust, n_outcomes, ece,
                         brier_skill, base_rate, notes, computed_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8, NOW())
                    ON CONFLICT (tenant_id, entity_kind, entity_id) DO UPDATE SET
                        trust = EXCLUDED.trust,
                        n_outcomes = EXCLUDED.n_outcomes,
                        ece = EXCLUDED.ece,
                        brier_skill = EXCLUDED.brier_skill,
                        base_rate = EXCLUDED.base_rate,
                        notes = EXCLUDED.notes,
                        computed_at = EXCLUDED.computed_at
                    """,
                    score.entity_kind, score.entity_id, score.trust, score.n_outcomes,
                    score.ece, score.brier_skill_score, score.base_rate, score.notes,
                )
            return True
        except Exception as exc:
            _log.error("trust.trust_save_failed", error=str(exc))
            return False
