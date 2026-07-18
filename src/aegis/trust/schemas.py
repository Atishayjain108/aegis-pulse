"""Phase B trust/calibration Pydantic schemas (all frozen)."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ReliabilityBin(BaseModel):
    """One bin of a reliability diagram."""

    model_config = ConfigDict(frozen=True)

    lower: float
    upper: float
    count: int
    mean_predicted: float  # average p in this bin
    observed_freq: float   # fraction of y==1 in this bin
    gap: float             # |mean_predicted - observed_freq|


class CalibrationReport(BaseModel):
    """Result of calibrating a stream of (p, y) pairs."""

    model_config = ConfigDict(frozen=True)

    entity_kind: str = "model"          # model | source | agent | global
    entity_id: str = "global"
    n: int = 0
    status: str = "ok"                  # ok | insufficient_variance | insufficient_data
    ece: float | None = None
    mce: float | None = None
    brier: float | None = None
    brier_skill_score: float | None = None  # vs base-rate predictor
    base_rate: float | None = None
    bins: list[ReliabilityBin] = Field(default_factory=list)
    computed_at: datetime = Field(default_factory=_utcnow)
    notes: str = ""


class TrustScore(BaseModel):
    """Calibrated reliability of one entity, in [0, 1]."""

    model_config = ConfigDict(frozen=True)

    entity_kind: str                    # model | source | agent
    entity_id: str
    trust: float = Field(ge=0.0, le=1.0)
    n_outcomes: int = 0
    ece: float | None = None
    brier_skill_score: float | None = None
    base_rate: float | None = None
    computed_at: datetime = Field(default_factory=_utcnow)
    notes: str = ""
