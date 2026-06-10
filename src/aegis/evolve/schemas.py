"""
Phase 9 Pydantic schemas — TradeOutcome, ModelCandidate, DriftSnapshot, PolicyState.

All models are frozen (immutable after construction).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Trade Outcome — ground truth label from a settled trade
# ---------------------------------------------------------------------------

class TradeOutcome(BaseModel, frozen=True):
    """Ground truth label for a single executed trade."""

    outcome_id: str = Field(default_factory=_new_uuid)
    execution_plan_id: str
    trend_id: str
    prediction_score: float = Field(ge=0.0, le=1.0)
    prediction_confidence: float = Field(ge=0.0, le=1.0)
    actual_roi_pct: Decimal = Field(default=Decimal("0"))
    pnl_usd: Decimal = Field(default=Decimal("0"))
    units_sold: int = Field(default=0, ge=0)
    units_returned: int = Field(default=0, ge=0)
    avg_sale_price: Decimal = Field(default=Decimal("0"))
    total_cost: Decimal = Field(default=Decimal("0"))
    shipping_cost: Decimal = Field(default=Decimal("0"))
    platform_fee: Decimal = Field(default=Decimal("0"))
    execution_timestamp: datetime = Field(default_factory=_utcnow)
    fulfillment_timestamp: datetime | None = None
    settlement_timestamp: datetime = Field(default_factory=_utcnow)
    days_to_fulfillment: int = Field(default=0, ge=0)
    resolution_status: str = Field(default="pending")
    resolution_notes: str = Field(default="")
    market_conditions_at_exec: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Model Candidate — one training run result
# ---------------------------------------------------------------------------

class ModelCandidate(BaseModel, frozen=True):
    """Result of a single candidate model training run."""

    candidate_id: str = Field(default_factory=_new_uuid)
    architecture: str
    train_auc: float = Field(default=0.5, ge=0.0, le=1.0)
    val_auc: float = Field(default=0.5, ge=0.0, le=1.0)
    test_auc: float = Field(default=0.5, ge=0.0, le=1.0)
    test_precision: float = Field(default=0.5, ge=0.0, le=1.0)
    test_recall: float = Field(default=0.5, ge=0.0, le=1.0)
    test_f1: float = Field(default=0.5, ge=0.0, le=1.0)
    training_duration_s: float = Field(default=0.0, ge=0.0)
    artifact_path: str = Field(default="")
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Drift Snapshot — point-in-time drift measurement
# ---------------------------------------------------------------------------

class DriftSnapshot(BaseModel, frozen=True):
    """Point-in-time drift measurement captured by DriftDetector."""

    snapshot_id: str = Field(default_factory=_new_uuid)
    drift_score: float = Field(ge=0.0, le=1.0)
    is_drifted: bool
    feature_stats: dict[str, Any] = Field(default_factory=dict)
    precision_now: float | None = None
    precision_prev: float | None = None
    precision_drop: float | None = None
    should_rollback: bool = False
    captured_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Policy State — persisted RL pricing policy weights
# ---------------------------------------------------------------------------

class PolicyState(BaseModel, frozen=True):
    """Snapshot of the RL pricing policy at a point in time."""

    policy_id: str = Field(default="default")
    weights: list[float] = Field(default_factory=lambda: [0.25, 0.35, 0.20, 0.20])
    update_count: int = Field(default=0, ge=0)
    learning_rate: float = Field(default=0.01, gt=0.0)
    last_updated_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Retrain Run — audit record for one weekly retrain
# ---------------------------------------------------------------------------

class RetrainRun(BaseModel, frozen=True):
    """Audit record for a single retraining pipeline execution."""

    run_id: str = Field(default_factory=_new_uuid)
    triggered_by: str = Field(default="scheduler")
    outcomes_count: int = Field(default=0, ge=0)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    champion_before: str | None = None
    champion_after: str | None = None
    improvement_pct: float | None = None
    status: str = Field(default="running")
    error_message: str | None = None
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None


# ---------------------------------------------------------------------------
# Evolve Status — aggregated health view for API/CLI
# ---------------------------------------------------------------------------

class EvolveStatus(BaseModel, frozen=True):
    """Aggregated Phase 9 health snapshot."""

    champion_model_id: str | None = None
    champion_auc: float = 0.5
    last_retrain_at: datetime | None = None
    last_retrain_status: str | None = None
    latest_drift_score: float | None = None
    is_drifted: bool = False
    policy_update_count: int = 0
    policy_weights: list[float] = Field(default_factory=lambda: [0.25, 0.35, 0.20, 0.20])
    outcomes_last_30d: int = 0
    checked_at: datetime = Field(default_factory=_utcnow)
