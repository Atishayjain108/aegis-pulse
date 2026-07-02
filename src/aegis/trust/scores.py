"""
aegis.trust.scores — Phase B trust scoring (Trust Score framework)
==================================================================

Aggregates realized calibration into a single ``trust`` in [0, 1] per entity.

    trust = w_cal · (1 - ECE)                 # is confidence honest?
          + w_acc · max(0, BrierSkillScore)   # better than the base rate?
          + w_vol · min(1, n / N_full)        # do we have enough evidence?

A constant or untested entity scores low on the volume term and gets a prior,
never a fake 1.0. No models, no neural networks — pure aggregation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from aegis.trust.calibration import brier_skill_score, ece
from aegis.trust.schemas import TrustScore

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.trust.scores")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

W_CAL = 0.5
W_ACC = 0.3
W_VOL = 0.2
N_FULL = 500  # outcomes at which the volume term saturates
PRIOR_TRUST = 0.3  # entities with no evidence start cautious, not at 0 or 1


def compute_trust(
    ps: list[float],
    ys: list[float],
    *,
    entity_kind: str,
    entity_id: str,
) -> TrustScore:
    """Combine calibration + skill + evidence-volume into a trust score."""
    n = len(ps)
    if n == 0:
        return TrustScore(
            entity_kind=entity_kind, entity_id=entity_id, trust=PRIOR_TRUST,
            n_outcomes=0, notes="no outcomes — prior",
        )
    distinct = len({round(p, 4) for p in ps})
    vol = min(1.0, n / N_FULL)
    base_rate = sum(ys) / n

    if distinct < 3:
        # Can't calibrate; trust reflects accuracy-vs-base-rate + volume only.
        bss = brier_skill_score(ps, ys)
        trust = max(0.0, min(1.0, W_ACC * max(0.0, bss) + W_VOL * vol + W_CAL * PRIOR_TRUST))
        return TrustScore(
            entity_kind=entity_kind, entity_id=entity_id, trust=trust,
            n_outcomes=n, brier_skill_score=bss, base_rate=base_rate,
            notes="insufficient confidence variance — calibration term uses prior",
        )

    e = ece(ps, ys)
    bss = brier_skill_score(ps, ys)
    trust = W_CAL * (1.0 - e) + W_ACC * max(0.0, bss) + W_VOL * vol
    trust = max(0.0, min(1.0, trust))
    return TrustScore(
        entity_kind=entity_kind, entity_id=entity_id, trust=trust,
        n_outcomes=n, ece=e, brier_skill_score=bss, base_rate=base_rate,
    )


class TrustScorer:
    """Compute trust scores for models, sources, and agents from outcomes."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id

    async def _settled(
        self, where: str, *args: object
    ) -> tuple[list[float], list[float]]:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
            )
            rows = await conn.fetch(
                f"""
                SELECT prediction_confidence AS p, resolution_status AS s
                FROM signal_outcomes
                WHERE resolution_status IN ('correct', 'incorrect') {where}
                """,  # noqa: S608 — `where` is a fixed literal, never user input
                *args,
            )
        ps = [float(r["p"]) for r in rows]
        ys = [1.0 if r["s"] == "correct" else 0.0 for r in rows]
        return ps, ys

    async def model_trust(self, model_id: str = "heuristic") -> TrustScore:
        """Trust for a prediction model (filtered by metadata source tag)."""
        ps, ys = await self._settled(
            "AND metadata->>'source' LIKE $1", f"%{model_id}%"
        )
        return compute_trust(ps, ys, entity_kind="model", entity_id=model_id)

    async def source_trust(self, *, min_outcomes: int = 10) -> list[TrustScore]:
        """
        Per-platform source reliability: over claims whose trend tag appears in a
        platform's signals, how calibrated/correct were they?
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
            )
            rows = await conn.fetch(
                """
                SELECT s.platform AS platform,
                       o.prediction_confidence AS p,
                       o.resolution_status AS st
                FROM signal_outcomes o
                JOIN LATERAL (
                    SELECT DISTINCT platform
                    FROM signals
                    WHERE o.trend_key = ANY(tags)
                ) s ON TRUE
                WHERE o.resolution_status IN ('correct', 'incorrect')
                """
            )
        by_platform: dict[str, tuple[list[float], list[float]]] = {}
        for r in rows:
            plat = str(r["platform"])
            ps, ys = by_platform.setdefault(plat, ([], []))
            ps.append(float(r["p"]))
            ys.append(1.0 if r["st"] == "correct" else 0.0)

        scores: list[TrustScore] = []
        for plat, (ps, ys) in sorted(by_platform.items()):
            if len(ps) < min_outcomes:
                continue
            scores.append(compute_trust(ps, ys, entity_kind="source", entity_id=plat))
        scores.sort(key=lambda t: t.trust, reverse=True)
        return scores
