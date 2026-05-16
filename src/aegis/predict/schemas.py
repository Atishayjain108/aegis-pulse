"""
Pydantic v2 schemas for the Predictive Apex layer.

Defines:
    FeatureWindow   — frozen, validated input tensor with metadata
    Prediction      — single-horizon prediction with uncertainty
    PredictionBundle — multi-horizon bundle, the canonical output
    PredictionRecord — DB-bound row (immutable, signed, audit-grade)
    BacktestResult  — walk-forward evaluation output
    ModelManifest   — registry entry: weights, schema, metrics, hash

All datetimes are timezone-aware UTC. All floats are bounded.
All ids are UUID4 strings. All payloads are JSON-serialisable.

These schemas are the **contract** between the predict layer and the
rest of AEGIS Pulse — they MUST stay backwards-compatible across
minor versions. Bumping any field's semantics requires a major bump
on `FEATURE_SCHEMA_VERSION` and a fresh model registry entry.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from . import FEATURE_DIM, FEATURE_NAMES, FEATURE_SCHEMA_VERSION

# ---------------------------------------------------------------------------
# Type aliases — bounded primitives keep the surface area sane.
# ---------------------------------------------------------------------------
ShortStr = Annotated[str, Field(min_length=1, max_length=128)]
LongStr = Annotated[str, Field(max_length=4096)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
NonNegFloat = Annotated[float, Field(ge=0.0)]
PositiveInt = Annotated[int, Field(ge=1)]


def _ensure_utc(v: datetime) -> datetime:
    """Coerce naive datetimes to UTC; reject non-UTC tz."""
    if v.tzinfo is None:
        return v.replace(tzinfo=UTC)
    return v.astimezone(UTC)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class TrendStage(StrEnum):
    """Lifecycle stages — coarsened into 5 buckets for label stability."""

    DORMANT = "dormant"  # noise floor; no measurable velocity
    EMERGING = "emerging"  # acceleration detected; pre-virality
    BREAKOUT = "breakout"  # active virality; velocity > σ_breakout
    PEAK = "peak"  # plateau; velocity ≈ 0
    DECLINING = "declining"  # negative second derivative
    SATURATED = "saturated"  # post-peak, audience exhausted


class PredictionAction(StrEnum):
    """High-level recommended action attached to each prediction."""

    ENTER = "enter"
    HOLD = "hold"
    EXIT = "exit"
    AVOID = "avoid"
    OBSERVE = "observe"  # default for low-confidence


class ModelKind(StrEnum):
    HEURISTIC = "heuristic"
    TEMPORAL = "temporal"
    RELATIONAL = "relational"
    FUSION = "fusion"
    CAUSAL = "causal"
    RL_POLICY = "rl_policy"


class UncertaintyMethod(StrEnum):
    NONE = "none"
    MC_DROPOUT = "mc_dropout"
    DEEP_ENSEMBLE = "deep_ensemble"
    CONFORMAL = "conformal"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
class FeatureWindow(BaseModel):
    """Sliding-window numeric input for the temporal model.

    Shape contract:
        - `values` is a row-major flattened list of length
          window_size * feature_dim.
        - `feature_names` MUST equal aegis.predict.FEATURE_NAMES.
        - `window_size` is the number of timesteps (default 168 = 7d).

    We pass a flat list rather than nested arrays because pydantic
    validation is O(n) per nested dimension, and this struct often
    crosses an HTTP boundary; flat encoding is ~3× faster to round-trip.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = FEATURE_SCHEMA_VERSION
    trend_id: ShortStr
    correlation_id: ShortStr = Field(default_factory=lambda: str(uuid.uuid4()))

    window_size: PositiveInt
    feature_dim: PositiveInt = FEATURE_DIM
    feature_names: tuple[str, ...] = FEATURE_NAMES
    values: list[float]

    captured_at: datetime
    tenant_id: ShortStr = "default"

    @field_validator("captured_at")
    @classmethod
    def _utc_only(cls, v: datetime) -> datetime:
        return _ensure_utc(v)

    @field_validator("feature_names")
    @classmethod
    def _names_match_schema(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(v) != FEATURE_NAMES:
            raise ValueError(
                f"feature_names mismatch: got {v!r}, expected FEATURE_NAMES "
                f"of length {FEATURE_DIM}. Bump FEATURE_SCHEMA_VERSION if "
                f"intentional."
            )
        return v

    @model_validator(mode="after")
    def _values_shape(self) -> FeatureWindow:
        expected = self.window_size * self.feature_dim
        if len(self.values) != expected:
            raise ValueError(
                f"values length {len(self.values)} != " f"window_size * feature_dim ({expected})"
            )
        # Reject NaN / Inf — no silent garbage propagation.
        for x in self.values:
            if math.isnan(x) or math.isinf(x):
                raise ValueError("values contain NaN or Inf")
        return self

    def as_2d(self) -> list[list[float]]:
        """Return a list-of-lists view shaped (window_size, feature_dim)."""
        w, d = self.window_size, self.feature_dim
        return [self.values[i * d : (i + 1) * d] for i in range(w)]


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
class Prediction(BaseModel):
    """Single-horizon prediction with calibrated uncertainty."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon_hours: PositiveInt
    stage: TrendStage
    velocity_log: float = Field(..., description="ln(predicted velocity at horizon)")
    velocity_mean: NonNegFloat
    velocity_p10: NonNegFloat
    velocity_p50: NonNegFloat
    velocity_p90: NonNegFloat

    # Probability the trend is in BREAKOUT or PEAK at horizon end.
    p_breakout: Probability
    p_peak: Probability
    p_decline: Probability

    # Aleatoric (data) vs epistemic (model) uncertainty.
    aleatoric: NonNegFloat = 0.0
    epistemic: NonNegFloat = 0.0

    # Distribution-free conformal interval (only meaningful when
    # method=CONFORMAL). [lower, upper] for velocity_mean.
    conformal_lower: float = 0.0
    conformal_upper: float = 0.0
    conformal_alpha: Probability = 0.1  # 90% interval default

    confidence: Probability  # composite score [0,1]
    action: PredictionAction
    reasoning: LongStr = ""

    # ---------------------------------------------------------------
    # Optional model-output fields (populated by neural predictors).
    #
    # We keep `velocity_log` / `velocity_mean` (positive scale) as the
    # canonical fields the persistence layer reads, AND additionally
    # expose:
    #
    #   * velocity_mean_log  — log-domain mean (signed)
    #   * velocity_log_sigma — log-domain stdev (≥0)
    #   * metadata           — small per-prediction k/v dict for audit
    #   * model_kind         — convenience override; defaults to the
    #                          bundle-level model_kind in audit views.
    #
    # These exist because the neural heads natively produce log-domain
    # outputs and per-prediction metadata (e.g. routing weights from
    # TimesNet's FFT branches). Without them, a model would have to
    # round-trip through inverse exp() to write into velocity_mean —
    # losing precision and making the audit trail harder to read.
    # ---------------------------------------------------------------
    velocity_mean_log: float = 0.0
    velocity_log_sigma: NonNegFloat = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    model_kind: ModelKind = ModelKind.HEURISTIC

    @model_validator(mode="after")
    def _percentile_order(self) -> Prediction:
        if not (self.velocity_p10 <= self.velocity_p50 <= self.velocity_p90):
            raise ValueError("percentiles violate monotonicity")
        if self.conformal_lower > self.conformal_upper:
            raise ValueError("conformal interval inverted")
        # Probabilities sum to ≤ 1 across the three classes (others are
        # implicit DORMANT/EMERGING/SATURATED).
        if self.p_breakout + self.p_peak + self.p_decline > 1.0001:
            raise ValueError("class probabilities exceed 1.0")
        return self


class PredictionBundle(BaseModel):
    """Multi-horizon bundle — the canonical output of `predict()`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = FEATURE_SCHEMA_VERSION
    trend_id: ShortStr
    correlation_id: ShortStr
    tenant_id: ShortStr = "default"

    predictions: list[Prediction]

    # Provenance — required for audit + reproducibility.
    model_id: ShortStr  # registry key (sha256-prefixed)
    model_kind: ModelKind
    model_version: ShortStr
    uncertainty_method: UncertaintyMethod
    seed: int = 0
    is_heuristic_only: bool = True  # True when no neural model ran

    started_at: datetime
    finished_at: datetime
    duration_ms: NonNegFloat

    # The full feature snapshot used — kept for replay.
    feature_window_hash: ShortStr

    # Optional model name (registry key without sha prefix). Populated
    # by neural predictors that want to record which architecture
    # produced the bundle separately from the registry's full model_id.
    model_name: ShortStr = "heuristic"

    @field_validator("started_at", "finished_at")
    @classmethod
    def _utc_only(cls, v: datetime) -> datetime:
        return _ensure_utc(v)

    @model_validator(mode="after")
    def _at_least_one(self) -> PredictionBundle:
        if not self.predictions:
            raise ValueError("PredictionBundle must have ≥1 prediction")
        # Horizons must be unique per bundle.
        horizons = [p.horizon_hours for p in self.predictions]
        if len(set(horizons)) != len(horizons):
            raise ValueError("duplicate horizons in bundle")
        return self

    def by_horizon(self, hours: int) -> Prediction | None:
        """Return the prediction for `hours` ahead, or None."""
        for p in self.predictions:
            if p.horizon_hours == hours:
                return p
        return None


# ---------------------------------------------------------------------------
# Persistent record — what lands in `predictions` table.
# ---------------------------------------------------------------------------
class PredictionRecord(BaseModel):
    """Immutable DB record. Signed for tamper-evidence (Phase 20)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prediction_id: ShortStr = Field(default_factory=lambda: str(uuid.uuid4()))
    bundle: PredictionBundle
    created_at: datetime
    signature: ShortStr = ""  # Ed25519 sig hex; empty until signed

    @field_validator("created_at")
    @classmethod
    def _utc_only(cls, v: datetime) -> datetime:
        return _ensure_utc(v)


# ---------------------------------------------------------------------------
# Backtest output
# ---------------------------------------------------------------------------
class BacktestResult(BaseModel):
    """Walk-forward backtest result over a single fold."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: ShortStr
    fold_index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime

    n_train: int
    n_test: int

    # Classification (stage)
    accuracy: Probability
    macro_f1: Probability
    breakout_precision: Probability
    breakout_recall: Probability

    # Regression (velocity)
    mae_log_velocity: NonNegFloat
    pinball_p10: NonNegFloat
    pinball_p90: NonNegFloat

    # Calibration / uncertainty
    ece: NonNegFloat  # expected calibration error
    coverage_90: Probability  # fraction inside 90% interval
    sharpness: NonNegFloat  # mean width of 90% interval

    # PnL proxy (set by execution simulator)
    expected_pnl: float = 0.0
    max_drawdown: float = 0.0

    notes: LongStr = ""

    @field_validator("train_start", "train_end", "test_start", "test_end")
    @classmethod
    def _utc_only(cls, v: datetime) -> datetime:
        return _ensure_utc(v)

    @model_validator(mode="after")
    def _no_lookahead(self) -> BacktestResult:
        if self.train_end > self.test_start:
            raise ValueError("train_end must precede test_start (lookahead leak)")
        return self


# ---------------------------------------------------------------------------
# Registry entry
# ---------------------------------------------------------------------------
class ModelManifest(BaseModel):
    """Registry entry — describes one trained, versioned, hashed model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: ShortStr  # "patchtst-3.0.0-sha:abc123"
    name: ShortStr  # "patchtst"
    kind: ModelKind
    version: ShortStr  # semver
    schema_version: str = FEATURE_SCHEMA_VERSION

    weights_uri: ShortStr  # s3://aegis-models/...
    onnx_uri: ShortStr | None = None
    sha256: ShortStr  # weights file content hash

    trained_at: datetime
    train_window_start: datetime
    train_window_end: datetime
    n_train_samples: int

    # Aggregate metrics over walk-forward backtest.
    backtest_summary: dict[str, float] = Field(default_factory=dict)

    # Stage in the registry. Promotion rules live in registry/store.py.
    stage: Literal["staging", "production", "shadow", "archived"] = "staging"

    notes: LongStr = ""

    @field_validator("trained_at", "train_window_start", "train_window_end")
    @classmethod
    def _utc_only(cls, v: datetime) -> datetime:
        return _ensure_utc(v)


__all__ = [
    "TrendStage",
    "PredictionAction",
    "ModelKind",
    "UncertaintyMethod",
    "FeatureWindow",
    "Prediction",
    "PredictionBundle",
    "PredictionRecord",
    "BacktestResult",
    "ModelManifest",
]
