"""
FastAPI router for Phase 9 Autonomous Self-Evolution.

Endpoints:
    GET  /evolve/health          — liveness probe
    GET  /evolve/status          — aggregated evolve health snapshot
    POST /evolve/retrain         — trigger a manual retraining run
    POST /evolve/outcomes        — record a single trade outcome
    GET  /evolve/outcomes/count  — count recent outcomes
    GET  /evolve/drift/latest    — latest drift snapshot
    POST /evolve/drift/check     — run drift check now (requires feature data)
    GET  /evolve/policy          — current RL pricing policy state
    POST /evolve/policy/update   — apply a single outcome to the RL policy
    GET  /evolve/runs            — recent retrain audit rows
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from aegis.evolve.config import EvolveSettings
from aegis.evolve.schemas import (
    DriftSnapshot,
    EvolveStatus,
    PolicyState,
    RetrainRun,
    TradeOutcome,
)

_log = structlog.get_logger("aegis.evolve.api")
_cfg = EvolveSettings()

router = APIRouter(prefix="/evolve", tags=["evolve"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class RetrainRequest(BaseModel):
    triggered_by: str = Field(default="manual")


class OutcomeRequest(BaseModel):
    execution_plan_id: str
    trend_id: str
    prediction_score: float = Field(ge=0.0, le=1.0)
    prediction_confidence: float = Field(ge=0.0, le=1.0)
    actual_roi_pct: float = Field(default=0.0)
    pnl_usd: float = Field(default=0.0)
    units_sold: int = Field(default=0, ge=0)
    units_returned: int = Field(default=0, ge=0)
    resolution_status: str = Field(default="successful")
    resolution_notes: str = Field(default="")


class PolicyUpdateRequest(BaseModel):
    price: float = Field(gt=0)
    cost: float = Field(gt=0)
    actual_demand: int = Field(ge=0)
    actual_roi_pct: float


class DriftCheckRequest(BaseModel):
    features: list[list[float]] = Field(
        description="Feature matrix rows (each row = EVOLVE_FEATURE_DIM floats)."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_pool() -> Any:
    """Try to return the shared asyncpg pool from aegis.db.pool."""
    try:
        from aegis.db.pool import get_shared_pool
        return get_shared_pool()
    except Exception:
        return None


def _get_pipeline() -> Any:
    from aegis.evolve.retrain import RetrainingPipeline
    return RetrainingPipeline(db_pool=_get_pool(), settings=_cfg)


def _get_recorder() -> Any:
    from aegis.evolve.outcomes import OutcomeRecorder
    pool = _get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Database not available")
    return OutcomeRecorder(pool)


def _get_detector() -> Any:
    from aegis.evolve.drift import DriftDetector
    return DriftDetector(db_pool=_get_pool(), settings=_cfg)


_policy_singleton: Any = None


def _get_policy() -> Any:
    global _policy_singleton  # noqa: PLW0603
    if _policy_singleton is None:
        from aegis.evolve.rl_policy import OnlinePricingPolicy
        _policy_singleton = OnlinePricingPolicy(
            learning_rate=_cfg.rl_learning_rate,
            db_pool=_get_pool(),
            settings=_cfg,
        )
    return _policy_singleton


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "phase": "9", "service": "evolve"}


@router.get("/status", response_model=EvolveStatus)
async def status() -> EvolveStatus:
    """Aggregated Phase 9 health snapshot."""
    pipeline = _get_pipeline()
    detector = _get_detector()
    policy = _get_policy()

    champion_auc = await pipeline.get_champion_auc()
    recent_runs = await pipeline.fetch_recent_runs(limit=1)
    last_snap = await detector.fetch_latest_snapshot()
    pool = _get_pool()
    outcomes_count = 0
    if pool:
        try:
            from aegis.evolve.outcomes import OutcomeRecorder
            outcomes_count = await OutcomeRecorder(pool).count_recent_outcomes(days_back=30)
        except Exception:
            pass

    return EvolveStatus(
        champion_auc=champion_auc,
        last_retrain_at=recent_runs[0].started_at if recent_runs else None,
        last_retrain_status=recent_runs[0].status if recent_runs else None,
        latest_drift_score=last_snap.drift_score if last_snap else None,
        is_drifted=last_snap.is_drifted if last_snap else False,
        policy_update_count=policy._update_count,
        policy_weights=policy.get_weights().tolist(),
        outcomes_last_30d=outcomes_count,
    )


@router.post("/retrain", response_model=RetrainRun)
async def trigger_retrain(req: RetrainRequest) -> RetrainRun:
    """Trigger a manual model retraining run."""
    pipeline = _get_pipeline()
    run = await pipeline.run_weekly_retrain(triggered_by=req.triggered_by)
    # CONN-1: publish the retrain outcome onto the unified event bus (best-effort).
    try:
        from aegis.core.event_bus import STREAM_EVOLVE, publish_event

        await publish_event(STREAM_EVOLVE, {"event": "retrain", **run.model_dump(mode="json")})
    except Exception:  # pragma: no cover - defensive
        pass
    return run


@router.post("/outcomes", status_code=201)
async def record_outcome(req: OutcomeRequest) -> dict[str, Any]:
    """Record a single trade outcome for future retraining."""
    recorder = _get_recorder()
    outcome = TradeOutcome(
        execution_plan_id=req.execution_plan_id,
        trend_id=req.trend_id,
        prediction_score=req.prediction_score,
        prediction_confidence=req.prediction_confidence,
        actual_roi_pct=Decimal(str(req.actual_roi_pct)),
        pnl_usd=Decimal(str(req.pnl_usd)),
        units_sold=req.units_sold,
        units_returned=req.units_returned,
        resolution_status=req.resolution_status,
        resolution_notes=req.resolution_notes,
    )
    ok = await recorder.record_outcome(outcome)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to record outcome")
    return {"outcome_id": outcome.outcome_id, "recorded": True}


@router.get("/outcomes/count")
async def outcomes_count(days_back: int = 30) -> dict[str, int]:
    """Count recent trade outcomes."""
    pool = _get_pool()
    if pool is None:
        return {"count": 0, "days_back": days_back}
    from aegis.evolve.outcomes import OutcomeRecorder
    n = await OutcomeRecorder(pool).count_recent_outcomes(days_back=days_back)
    return {"count": n, "days_back": days_back}


@router.get("/drift/latest", response_model=DriftSnapshot | None)
async def drift_latest() -> DriftSnapshot | None:
    """Return the most recently persisted drift snapshot."""
    detector = _get_detector()
    return await detector.fetch_latest_snapshot()


@router.post("/drift/check", response_model=DriftSnapshot)
async def drift_check(req: DriftCheckRequest) -> DriftSnapshot:
    """Run a drift check on the provided feature matrix and persist the result."""
    import numpy as np
    if not req.features:
        raise HTTPException(status_code=400, detail="features list must not be empty")
    try:
        X = np.array(req.features, dtype=float)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid feature matrix: {exc}") from exc

    detector = _get_detector()
    snap = await detector.run_all_checks(X)
    await detector.persist_snapshot(snap)
    return snap


@router.get("/policy", response_model=PolicyState)
async def policy_state() -> PolicyState:
    """Return current RL pricing policy state."""
    return _get_policy().get_state()


@router.post("/policy/update")
async def policy_update(req: PolicyUpdateRequest) -> dict[str, Any]:
    """Apply a single trade outcome to the RL pricing policy."""
    policy = _get_policy()
    policy.update_from_outcome(
        price=req.price,
        cost=req.cost,
        actual_demand=req.actual_demand,
        actual_roi_pct=req.actual_roi_pct,
    )
    if policy._update_count % _cfg.rl_persist_interval == 0:
        await policy.persist()
    weights = policy.get_weights()
    return {
        "update_count": policy._update_count,
        "weights": weights.tolist(),
    }


@router.get("/runs", response_model=list[RetrainRun])
async def recent_runs(limit: int = 10) -> list[RetrainRun]:
    """List recent retrain audit records."""
    pipeline = _get_pipeline()
    return await pipeline.fetch_recent_runs(limit=min(limit, 50))
