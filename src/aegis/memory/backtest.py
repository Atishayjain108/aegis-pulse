"""
aegis.memory.backtest — validate the Reality layer out-of-sample (Rule 12).

The headline Phase C success metric: do opportunities the verifier rates higher
actually realise more often? This reads SETTLED opportunities (realized/failed)
that carry a stored ``evidence_score``, buckets them, and reports the realized
rate per bucket plus the high−low separation. A positive separation is the
falsifiable evidence that the Reality gate adds signal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.memory.backtest")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


def _bucket(score: float) -> str:
    if score >= 0.66:
        return "high"
    if score >= 0.33:
        return "mid"
    return "low"


class RealityBacktester:
    """Measure whether a higher evidence_score predicts a higher realized rate."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id

    async def run(self) -> dict[str, Any]:
        """Return realized-rate-by-bucket + high−low separation. Best-effort."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                rows = await conn.fetch(
                    """
                    SELECT evidence_score, outcome
                    FROM opportunities
                    WHERE outcome IN ('realized', 'failed')
                      AND evidence_score IS NOT NULL
                    """
                )
        except Exception as exc:
            _log.error("memory.backtest_fetch_failed", error=str(exc))
            return {"n": 0, "buckets": {}, "separation": None, "status": "error"}

        buckets: dict[str, list[int]] = {"low": [], "mid": [], "high": []}
        for r in rows:
            y = 1 if r["outcome"] == "realized" else 0
            buckets[_bucket(float(r["evidence_score"]))].append(y)

        n = sum(len(v) for v in buckets.values())
        if n == 0:
            return {"n": 0, "buckets": {}, "separation": None, "status": "no_data"}

        rates = {
            b: (round(sum(v) / len(v), 4) if v else None)
            for b, v in buckets.items()
        }
        counts = {b: len(v) for b, v in buckets.items()}
        sep = (
            round(rates["high"] - rates["low"], 4)
            if rates["high"] is not None and rates["low"] is not None
            else None
        )
        return {
            "n": n,
            "buckets": rates,
            "counts": counts,
            "separation": sep,   # >0 ⇒ higher evidence predicts higher realization
            "status": "ok",
        }
