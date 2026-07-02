"""
aegis.execution_intel.forecast — Failure Prediction (Rule 8).

Predict per-mode failure, STORE the prediction, then MEASURE it against the
settled outcome and learn. The predictor reuses the deterministic
``ExecutionSimulator`` (failure prob = 1 − survival), so the forecast inherits
the simulator's learned failure base rates — the "learn continuously" loop is
closed by feeding settled outcomes back into ``failure_base_rates``.

No look-ahead: ``record_forecast`` stores the prediction at recommendation time;
``measure_accuracy`` only ever scores forecasts whose plan has *already* settled.
``abstained`` forecasts are excluded from scoring (we don't score a non-claim).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from aegis.execution_intel.schemas import FailureForecast, ForecastAccuracy
from aegis.execution_intel.simulator import ExecutionSimulator

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.execution_intel.forecast")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


class FailureForecaster:
    """Forecast, store, and score plan-failure predictions (Rule 8)."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id
        self._simulator = ExecutionSimulator(db_pool, tenant_id=tenant_id)

    async def _set_tenant(self, conn: Any) -> None:
        await conn.execute(
            "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
        )

    async def forecast(
        self,
        plan_id: str,
        *,
        supplier_name: str | None = None,
        region: str = "GLOBAL",
        category: str = "general",
        compliance_risk: float | None = None,
    ) -> FailureForecast:
        """Predict per-mode failure probabilities (1 − simulated survival)."""
        surv = await self._simulator.simulate(
            plan_id,
            supplier_name=supplier_name,
            region=region,
            category=category,
            compliance_risk=compliance_risk,
        )
        if surv.abstained or not surv.mode_survival:
            return FailureForecast(plan_id=plan_id, abstained=True)
        probs = {
            mode: round(1.0 - s, 4) for mode, s in surv.mode_survival.items()
        }
        return FailureForecast(plan_id=plan_id, probabilities=probs, abstained=False)

    @staticmethod
    def _p_any_failure(probabilities: dict[str, float]) -> float:
        """P(at least one mode fails) assuming mode independence."""
        survive_all = 1.0
        for p in probabilities.values():
            survive_all *= (1.0 - p)
        return round(1.0 - survive_all, 4)

    async def record_forecast(self, forecast: FailureForecast) -> bool:
        """Persist a forecast for later scoring (idempotent on plan_id)."""
        if self._pool is None:
            return False
        import json

        p_any = (
            None if forecast.abstained
            else self._p_any_failure(forecast.probabilities)
        )
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                await conn.execute(
                    """
                    INSERT INTO execution_forecasts (
                        plan_id, probabilities, p_any_failure, abstained
                    ) VALUES ($1,$2,$3,$4)
                    ON CONFLICT (tenant_id, plan_id) DO UPDATE SET
                        probabilities = EXCLUDED.probabilities,
                        p_any_failure = EXCLUDED.p_any_failure,
                        abstained = EXCLUDED.abstained,
                        created_at = NOW()
                    """,
                    forecast.plan_id,
                    json.dumps(forecast.probabilities),
                    p_any,
                    forecast.abstained,
                )
            return True
        except Exception as exc:
            _log.error("execution_intel.record_forecast_failed", error=str(exc))
            return False

    async def measure_accuracy(self) -> ForecastAccuracy:
        """Score stored non-abstained forecasts vs settled outcomes (Rule 8).

        Joins ``execution_forecasts`` to ``execution_records`` on plan_id where
        the record has settled. Observed failure = outcome in (failed,cancelled).
        Brier = mean (p_any − observed)²; brier_skill vs the base-rate predictor.
        """
        if self._pool is None:
            return ForecastAccuracy()
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                rows = await conn.fetch(
                    """
                    SELECT f.p_any_failure AS p,
                           (r.outcome IN ('failed','cancelled')) AS failed
                    FROM execution_forecasts f
                    JOIN execution_records r ON r.plan_id = f.plan_id
                    WHERE f.abstained = FALSE
                      AND f.p_any_failure IS NOT NULL
                      AND r.outcome != 'pending'
                    """
                )
        except Exception as exc:
            _log.error("execution_intel.measure_accuracy_failed", error=str(exc))
            return ForecastAccuracy()

        n = len(rows)
        if n == 0:
            return ForecastAccuracy(n=0)
        observed = [1.0 if r["failed"] else 0.0 for r in rows]
        preds = [float(r["p"]) for r in rows]
        base_rate = sum(observed) / n
        brier = sum((p - o) ** 2 for p, o in zip(preds, observed, strict=True)) / n
        brier_base = sum((base_rate - o) ** 2 for o in observed) / n
        skill = None if brier_base == 0 else 1.0 - (brier / brier_base)
        return ForecastAccuracy(
            n=n,
            base_rate=round(base_rate, 4),
            brier=round(brier, 4),
            brier_skill=round(skill, 4) if skill is not None else None,
        )
