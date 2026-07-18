"""Dynamic threshold adaptation for AEGIS core decision gates (PASS2-2C / BRAIN-3).

Phase cross-cutting module. Replaces static env constants for three key gates:

  - confidence_gate: AEGIS_SCRAPE_CONFIDENCE_THRESHOLD          (default 0.85)
  - velocity_slope:  AEGIS_SCRAPE_VELOCITY_HIGH_PRIORITY_SLOPE  (default 2.0)
  - comply_block:    AEGIS_COMPLY_BLOCK_THRESHOLD               (default 0.70)

Each threshold adapts weekly from realized outcome accuracy:
  - precision > target → relax slightly (more signals pass)
  - precision < target → tighten (fewer, better signals)

Adaptation is conservative: ±0.02 per week, hard bounds per threshold.
Falls back to env-based static values when Redis is empty/unavailable or
outcome data is insufficient. State is shared across processes via the Redis
key ``aegis:core:thresholds``; the weekly update runs in the autonomous
scheduler (``job_threshold_update``, Sunday 3 AM UTC).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

_log = structlog.get_logger("aegis.core.dynamic_thresholds")

_ADAPT_STEP = 0.02  # max change per weekly update
_CONFIDENCE_BOUNDS = (0.60, 0.95)
_VELOCITY_BOUNDS = (1.0, 5.0)
_COMPLY_BOUNDS = (0.50, 0.90)
_REDIS_KEY = "aegis:core:thresholds"
_REDIS_TTL = 8 * 86400  # 8 days — survives the weekly update + buffer
_CACHE_TTL = timedelta(hours=1)


@dataclass
class ThresholdState:
    """Snapshot of the three adaptive thresholds plus provenance."""

    confidence_gate: float
    velocity_slope: float
    comply_block: float
    updated_at: datetime
    outcome_count: int  # how many outcomes drove this update
    precision: float  # realized precision at time of last update


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


class DynamicThresholds:
    """Redis-backed adaptive threshold manager with a 1-hour local cache.

    Usage (read side — any async context)::

        thresholds = DynamicThresholds(redis=redis_client)
        gate = await thresholds.get_confidence_gate()

    Weekly update (autonomous scheduler)::

        await thresholds.update_from_outcomes(pool, tenant_id)
    """

    def __init__(self, redis: Any | None = None) -> None:
        self._redis = redis
        self._cache: ThresholdState | None = None
        self._cache_until = datetime.min.replace(tzinfo=UTC)

    async def get_confidence_gate(self, fallback: float | None = None) -> float:
        state = await self._get()
        if state.outcome_count == 0 and fallback is not None:
            return fallback
        return state.confidence_gate

    async def get_velocity_slope(self, fallback: float | None = None) -> float:
        state = await self._get()
        if state.outcome_count == 0 and fallback is not None:
            return fallback
        return state.velocity_slope

    async def get_comply_block(self, fallback: float | None = None) -> float:
        """Adaptive thresholds only apply once real outcome data has driven an
        update (``outcome_count > 0``); until then a caller-supplied fallback
        (e.g. an injected settings object) wins over the env-based default."""
        state = await self._get()
        if state.outcome_count == 0 and fallback is not None:
            return fallback
        return state.comply_block

    def invalidate_cache(self) -> None:
        """Test/maintenance hook: force the next read to hit Redis."""
        self._cache = None
        self._cache_until = datetime.min.replace(tzinfo=UTC)

    async def _get(self) -> ThresholdState:
        now = datetime.now(UTC)
        if self._cache is not None and now < self._cache_until:
            return self._cache

        state = await self._load_from_redis()
        if state is None:
            state = self._defaults()

        self._cache = state
        self._cache_until = now + _CACHE_TTL
        return state

    async def _load_from_redis(self) -> ThresholdState | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(_REDIS_KEY)
            if not raw:
                return None
            data = json.loads(raw)
            return ThresholdState(
                confidence_gate=float(data["confidence_gate"]),
                velocity_slope=float(data["velocity_slope"]),
                comply_block=float(data["comply_block"]),
                updated_at=datetime.fromisoformat(data["updated_at"]),
                outcome_count=int(data.get("outcome_count", 0)),
                precision=float(data.get("precision", 0.0)),
            )
        except Exception as exc:
            _log.warning("dynamic_thresholds.load_failed", error=str(exc))
            return None

    @staticmethod
    def _defaults() -> ThresholdState:
        return ThresholdState(
            confidence_gate=float(
                os.environ.get("AEGIS_SCRAPE_CONFIDENCE_THRESHOLD", "0.85")
            ),
            velocity_slope=float(
                os.environ.get("AEGIS_SCRAPE_VELOCITY_HIGH_PRIORITY_SLOPE", "2.0")
            ),
            comply_block=float(os.environ.get("AEGIS_COMPLY_BLOCK_THRESHOLD", "0.70")),
            updated_at=datetime.now(UTC),
            outcome_count=0,
            precision=0.0,
        )

    async def update_from_outcomes(
        self,
        pool: Any,
        tenant_id: str,
        target_precision: float = 0.80,
        min_outcomes: int = 30,
    ) -> ThresholdState | None:
        """Adapt thresholds from the last 30 days of realized outcomes.

        Called weekly by the autonomous scheduler (Sunday 3 AM UTC).

        1. Fetch recent ``prediction_outcomes`` (RLS tenant set first, §2).
        2. Realized precision = ENTER-grade outcomes with roi > 0 / total.
        3. precision > target → relax by ``_ADAPT_STEP``; below → tighten.
        4. Clamp to hard bounds; persist to Redis (TTL 8 days).

        Returns the new state, or None when data is insufficient or the
        update failed (never raises).
        """
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, TRUE)", tenant_id
                )
                rows = await conn.fetch(
                    """
                    SELECT prediction_score, roi, status
                    FROM prediction_outcomes
                    WHERE settlement_timestamp > NOW() - INTERVAL '30 days'
                    ORDER BY settlement_timestamp DESC
                    LIMIT 500
                    """,
                )

            if len(rows) < min_outcomes:
                _log.info(
                    "dynamic_thresholds.insufficient_data",
                    count=len(rows),
                    min_required=min_outcomes,
                )
                return None

            enter_outcomes = [r for r in rows if float(r["prediction_score"]) >= 0.55]
            if not enter_outcomes:
                return None

            precision = sum(1 for r in enter_outcomes if float(r["roi"]) > 0) / len(
                enter_outcomes
            )
            current = await self._get()
            delta = _ADAPT_STEP if precision > target_precision else -_ADAPT_STEP

            new_state = ThresholdState(
                confidence_gate=_clamp(
                    current.confidence_gate - delta, _CONFIDENCE_BOUNDS
                ),
                velocity_slope=_clamp(current.velocity_slope - delta, _VELOCITY_BOUNDS),
                # comply_block: relaxing means a HIGHER block threshold
                # (fewer products blocked), so the sign is inverted.
                comply_block=_clamp(current.comply_block + delta, _COMPLY_BOUNDS),
                updated_at=datetime.now(UTC),
                outcome_count=len(rows),
                precision=round(precision, 4),
            )

            if self._redis is not None:
                await self._redis.set(
                    _REDIS_KEY,
                    json.dumps(
                        {
                            "confidence_gate": new_state.confidence_gate,
                            "velocity_slope": new_state.velocity_slope,
                            "comply_block": new_state.comply_block,
                            "updated_at": new_state.updated_at.isoformat(),
                            "outcome_count": new_state.outcome_count,
                            "precision": new_state.precision,
                        }
                    ),
                    ex=_REDIS_TTL,
                )
            self._cache = new_state
            self._cache_until = datetime.now(UTC) + _CACHE_TTL

            _log.info(
                "dynamic_thresholds.updated",
                precision=round(precision, 4),
                delta=delta,
                confidence_gate=round(new_state.confidence_gate, 3),
                velocity_slope=round(new_state.velocity_slope, 3),
                comply_block=round(new_state.comply_block, 3),
                outcome_count=new_state.outcome_count,
            )
            return new_state

        except Exception as exc:
            _log.error("dynamic_thresholds.update_failed", error=str(exc))
            return None


# Process-wide singleton (lazy redis acquisition via the event-bus holder).
_INSTANCE: DynamicThresholds | None = None


async def get_thresholds() -> DynamicThresholds:
    """Return the process singleton, wiring the shared Redis client lazily."""
    global _INSTANCE  # noqa: PLW0603 - process-level singleton
    if _INSTANCE is None:
        redis = None
        try:
            from aegis.core.event_bus import _HOLDER

            redis = await _HOLDER.get()
        except Exception:
            redis = None
        _INSTANCE = DynamicThresholds(redis=redis)
    return _INSTANCE


def reset_thresholds_singleton() -> None:
    """Test hook: drop the process singleton."""
    global _INSTANCE  # noqa: PLW0603 - test/maintenance hook
    _INSTANCE = None


__all__ = [
    "DynamicThresholds",
    "ThresholdState",
    "get_thresholds",
    "reset_thresholds_singleton",
]
