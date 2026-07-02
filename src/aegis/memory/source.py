"""
aegis.memory.source — Source Memory (Rule 4).

Per-platform reliability built strictly from SETTLED outcomes + observed signal
recency. Reuses the Phase B ``compute_trust`` engine for the trust scalar and
adds reliability (settled-correct rate) and freshness (signal recency).

Honesty doctrine (Rule 1): ``manipulation_risk`` and ``per_category_accuracy``
are NOT yet measurable from available data, so they are left ``None``/empty —
never faked. They are populated when a real signal exists to compute them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from aegis.memory.schemas import SourceProfile
from aegis.trust.scores import compute_trust

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.memory.source")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

# Freshness decays to 0 over this many days since a platform's last signal.
_FRESHNESS_HORIZON_DAYS = 30.0


def _freshness(age_days: float) -> float:
    """Linear decay in [0, 1]; 1.0 today, 0.0 at the horizon."""
    return max(0.0, min(1.0, 1.0 - age_days / _FRESHNESS_HORIZON_DAYS))


class SourceMemory:
    """Build + persist + query per-platform source profiles."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id

    async def build_profiles(self, *, min_outcomes: int = 5) -> list[SourceProfile]:
        """Recompute every source profile from settled outcomes + signals.

        Returns the profiles (also persisted). Best-effort: on DB error returns
        ``[]`` and logs — never raises.
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                # (platform, p, correct?) for every settled claim whose tag was
                # seen on that platform — same join the Phase B source_trust uses.
                outcome_rows = await conn.fetch(
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
                # Per-platform signal volume + recency (freshness).
                signal_rows = await conn.fetch(
                    """
                    SELECT platform::text AS platform,
                           COUNT(*) AS n_signals,
                           EXTRACT(EPOCH FROM (NOW() - MAX(ts))) / 86400.0 AS age_days
                    FROM signals
                    GROUP BY platform
                    """
                )
        except Exception as exc:
            _log.error("memory.source_build_fetch_failed", error=str(exc))
            return []

        by_platform: dict[str, tuple[list[float], list[float]]] = {}
        for r in outcome_rows:
            plat = str(r["platform"])
            ps, ys = by_platform.setdefault(plat, ([], []))
            ps.append(float(r["p"]))
            ys.append(1.0 if r["st"] == "correct" else 0.0)

        signal_stats = {
            str(r["platform"]): (int(r["n_signals"]), float(r["age_days"] or 0.0))
            for r in signal_rows
        }

        profiles: list[SourceProfile] = []
        for plat in sorted(set(by_platform) | set(signal_stats)):
            ps, ys = by_platform.get(plat, ([], []))
            n_out = len(ps)
            n_sig, age_days = signal_stats.get(plat, (0, 999.0))
            reliability = (sum(ys) / n_out) if n_out else None
            trust = (
                compute_trust(ps, ys, entity_kind="source", entity_id=plat).trust
                if n_out >= min_outcomes
                else 0.3  # PRIOR — too few outcomes to judge
            )
            profile = SourceProfile(
                source_id=plat,
                trust=trust,
                reliability_score=reliability,
                freshness_score=_freshness(age_days),
                n_signals=n_sig,
                n_outcomes=n_out,
            )
            await self._persist(profile)
            profiles.append(profile)

        profiles.sort(key=lambda p: p.trust, reverse=True)
        _log.info("memory.source_profiles_built", count=len(profiles))
        return profiles

    async def _persist(self, profile: SourceProfile) -> bool:
        import json

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                await conn.execute(
                    """
                    INSERT INTO source_profiles (
                        source_id, trust, reliability_score, freshness_score,
                        manipulation_risk, per_category_accuracy, n_signals,
                        n_outcomes, updated_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8, NOW())
                    ON CONFLICT (tenant_id, source_id) DO UPDATE SET
                        trust = EXCLUDED.trust,
                        reliability_score = EXCLUDED.reliability_score,
                        freshness_score = EXCLUDED.freshness_score,
                        manipulation_risk = EXCLUDED.manipulation_risk,
                        per_category_accuracy = EXCLUDED.per_category_accuracy,
                        n_signals = EXCLUDED.n_signals,
                        n_outcomes = EXCLUDED.n_outcomes,
                        updated_at = EXCLUDED.updated_at
                    """,
                    profile.source_id,
                    profile.trust,
                    profile.reliability_score,
                    profile.freshness_score,
                    profile.manipulation_risk,
                    json.dumps(profile.per_category_accuracy),
                    profile.n_signals,
                    profile.n_outcomes,
                )
            return True
        except Exception as exc:
            _log.error("memory.source_persist_failed", error=str(exc))
            return False

    async def trust_for(self, source_ids: list[str]) -> dict[str, float]:
        """Latest trust scalar for each given platform; missing → prior 0.3."""
        if not source_ids:
            return {}
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                rows = await conn.fetch(
                    "SELECT source_id, trust FROM source_profiles "
                    "WHERE source_id = ANY($1)",
                    source_ids,
                )
            found = {str(r["source_id"]): float(r["trust"]) for r in rows}
        except Exception as exc:
            _log.error("memory.source_trust_for_failed", error=str(exc))
            found = {}
        return {sid: found.get(sid, 0.3) for sid in source_ids}
