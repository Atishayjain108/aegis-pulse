"""
aegis.memory.market — Market Memory (Rule 6).

⚠️  ROADMAP / DORMANT — NOT WIRED (verified 2026-06-18, PROJECT OMEGA)
    This module has ZERO production callers: nothing calls ``record_epoch()``
    (no epochs are ever written) and nothing calls ``compare_to_history()``
    (no decision or report reads it). It is exercised only by unit tests. It is
    kept as roadmap scaffolding, NOT a live system — do not present it as a
    working capability. Before relying on it: (1) add a writer that records real
    epochs on a schedule, (2) add a reader that feeds a decision/report, (3)
    remove this banner. See docs/DORMANT_SYSTEMS.md.

Stores compressed per-period market snapshots and answers "how do current
conditions compare against history?". Each epoch is a real summary of a window;
comparison reads past epochs for the same (category, region). Best-effort.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

import structlog

from aegis.memory.schemas import MarketEpoch

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.memory.market")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

# Machine-readable quarantine marker (asserted by the reality-invariant tests).
DORMANT: Final[bool] = True


class MarketMemory:
    """Record market epochs + compare current conditions against history.

    ROADMAP / DORMANT — see module docstring. No production reader or writer.
    """

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id

    async def record_epoch(self, epoch: MarketEpoch) -> bool:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                await conn.execute(
                    """
                    INSERT INTO market_epochs (
                        period_start, period_end, category, region, trend_summary,
                        demand_index, seasonality_tag, recurring_pattern_ids
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                    """,
                    epoch.period_start, epoch.period_end, epoch.category, epoch.region,
                    json.dumps(epoch.trend_summary, default=str), epoch.demand_index,
                    epoch.seasonality_tag, json.dumps(epoch.recurring_pattern_ids),
                )
            return True
        except Exception as exc:
            _log.error("memory.market_record_failed", error=str(exc))
            return False

    async def compare_to_history(
        self, *, category: str, region: str | None, current_demand: float,
    ) -> dict[str, Any]:
        """Compare a current demand reading against the historical mean.

        Returns ``historical_mean``, ``delta`` (current − mean), ``n_epochs`` and
        a coarse ``signal`` label. When there is no history the delta is null.
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                row = await conn.fetchrow(
                    """
                    SELECT AVG(demand_index) AS mean_demand, COUNT(*) AS n
                    FROM market_epochs
                    WHERE category = $1
                      AND ($2::text IS NULL OR region = $2)
                      AND demand_index IS NOT NULL
                    """,
                    category, region,
                )
        except Exception as exc:
            _log.error("memory.market_compare_failed", error=str(exc))
            return {"historical_mean": None, "delta": None, "n_epochs": 0,
                    "signal": "unknown"}

        n = int(row["n"]) if row else 0
        mean = float(row["mean_demand"]) if row and row["mean_demand"] is not None else None
        if mean is None:
            return {"historical_mean": None, "delta": None, "n_epochs": n,
                    "signal": "no_history"}
        delta = current_demand - mean
        signal = "above_trend" if delta > 0 else ("below_trend" if delta < 0 else "at_trend")
        return {
            "historical_mean": round(mean, 4),
            "delta": round(delta, 4),
            "n_epochs": n,
            "signal": signal,
        }
