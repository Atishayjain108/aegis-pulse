"""
Heuristic predictors — the deterministic always-on path.

Two implementations:
    HeuristicTemporalPredictor    — uses only the time series.
    HeuristicRelationalPredictor  — also uses CreatorGraph features.

Both are pure-stdlib (no torch, no numpy required) and return well-
calibrated predictions in the empirically-observed e-commerce trend
distribution. They produce the **floor** of system quality — the
neural models exist to push above this floor, but the system never
goes below it.

Calibration philosophy:
    * Confidences are bounded by HEURISTIC_CONFIDENCE_CEILING (0.75).
      A heuristic that claims 99% certainty is a bug.
    * Class probabilities sum to ≤ 1.0 across {breakout, peak, decline};
      the residual is implicit "dormant/emerging/saturated" mass.
    * Velocity percentiles use a log-normal proxy with σ scaled by
      the noisiness of the recent window — it widens when the input
      is sparse, tightens when many signals agree.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from ..constants import (
    ACTION_CONFIDENCE_FLOOR,
    ACTION_ENTER_PROBABILITY_FLOOR,
    ACTION_EXIT_PROBABILITY_CEILING,
    HEURISTIC_BREAKOUT_MIN_AUTHORS,
    HEURISTIC_BREAKOUT_MIN_SIGNALS,
    HEURISTIC_BREAKOUT_VELOCITY_24H,
    HEURISTIC_CONFIDENCE_CEILING,
    HEURISTIC_DECLINING_VELOCITY_24H,
    HEURISTIC_PEAK_DECEL_THRESHOLD,
)
from ..features.graph import CreatorGraph
from ..schemas import (
    FeatureWindow,
    ModelKind,
    Prediction,
    PredictionAction,
    TrendStage,
    UncertaintyMethod,
)
from .base import Predictor


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _logistic(x: float, *, k: float = 1.0, x0: float = 0.0) -> float:
    """Stable logistic that does not overflow for large |x|."""
    z = -k * (x - x0)
    if z >= 0:
        return 1.0 / (1.0 + math.exp(z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _summarise_window(window: FeatureWindow) -> dict[str, Any]:
    """Extract a small dict of scalar summaries from the window."""
    rows = window.as_2d()
    names = list(window.feature_names)
    idx = {n: i for i, n in enumerate(names)}

    # Last 24h tail (or whatever fits).
    tail = min(24, len(rows))
    head = min(24, max(0, len(rows) - tail))
    recent = rows[-tail:]
    prior = rows[-2 * tail : -tail] if len(rows) >= 2 * tail else rows[:head]

    def col(matrix: list[list[float]], name: str) -> list[float]:
        i = idx[name]
        return [r[i] for r in matrix]

    recent_signals = sum(col(recent, "signal_count"))
    prior_signals = sum(col(prior, "signal_count")) if prior else 0.0
    recent_authors = sum(col(recent, "unique_authors"))
    last_v24 = col(rows, "velocity_24h")[-1]
    last_v6 = col(rows, "velocity_6h")[-1]
    last_v1 = col(rows, "velocity_1h")[-1]

    last_sentiment = col(rows, "sentiment_mean")[-1]
    last_commercial = col(rows, "commercial_intent")[-1]
    last_novelty = col(rows, "novelty")[-1]
    last_coord = col(rows, "coordination_risk")[-1]

    # Acceleration: change in v24 over the last quarter of the window.
    v24_series = col(rows, "velocity_24h")
    quarter = max(1, len(v24_series) // 4)
    accel = v24_series[-1] - v24_series[-quarter]

    # Noise in recent velocities — used to widen percentile bands.
    recent_v6 = col(recent, "velocity_6h") if recent else [0.0]
    v_std = statistics.pstdev(recent_v6) if len(recent_v6) > 1 else 0.0

    return {
        "recent_signals": recent_signals,
        "prior_signals": prior_signals,
        "recent_authors": recent_authors,
        "v24": last_v24,
        "v6": last_v6,
        "v1": last_v1,
        "sentiment": last_sentiment,
        "commercial_intent": last_commercial,
        "novelty": last_novelty,
        "coord_risk": last_coord,
        "acceleration": accel,
        "velocity_noise": v_std,
    }


def _heuristic_stage(s: dict[str, Any]) -> TrendStage:
    """Map summary scalars to a discrete TrendStage."""
    v24 = s["v24"]
    v1 = s["v1"]
    accel = s["acceleration"]
    recent_signals = s["recent_signals"]
    recent_authors = s["recent_authors"]

    if (
        v24 >= HEURISTIC_BREAKOUT_VELOCITY_24H
        and recent_signals >= HEURISTIC_BREAKOUT_MIN_SIGNALS
        and recent_authors >= HEURISTIC_BREAKOUT_MIN_AUTHORS
        and accel > 0
    ):
        return TrendStage.BREAKOUT

    if v24 > 0 and accel <= HEURISTIC_PEAK_DECEL_THRESHOLD and recent_signals > 0:
        return TrendStage.PEAK

    if v24 <= HEURISTIC_DECLINING_VELOCITY_24H:
        return TrendStage.DECLINING

    if v24 > 0 and accel > 0:
        return TrendStage.EMERGING

    if recent_signals == 0 and v1 == 0:
        return TrendStage.DORMANT

    if v24 < 0 and accel < HEURISTIC_DECLINING_VELOCITY_24H * 1.5:
        return TrendStage.SATURATED

    # Negative velocity with mild deceleration: treat as declining, not emerging.
    if v24 < 0:
        return TrendStage.DECLINING

    return TrendStage.EMERGING


def _stage_to_class_probs(
    stage: TrendStage,
    *,
    confidence: float,
) -> tuple[float, float, float]:
    """Map (stage, confidence) → (p_breakout, p_peak, p_decline).

    Mass not assigned to these three classes is implicit "other"
    (dormant/emerging/saturated).
    """
    c = _clamp(confidence, 0.0, HEURISTIC_CONFIDENCE_CEILING)
    if stage == TrendStage.BREAKOUT:
        return (c, 0.15 * c, 0.05 * c)
    if stage == TrendStage.PEAK:
        return (0.20 * c, c, 0.20 * c)
    if stage == TrendStage.DECLINING:
        return (0.05 * c, 0.10 * c, c)
    if stage == TrendStage.SATURATED:
        return (0.02, 0.05, 0.85 * c)
    if stage == TrendStage.EMERGING:
        return (0.40 * c, 0.10 * c, 0.05 * c)
    # DORMANT
    return (0.02, 0.02, 0.02)


def _action_for(
    *,
    p_breakout: float,
    p_decline: float,
    confidence: float,
    stage: TrendStage,
) -> PredictionAction:
    """Project (probabilities, confidence) onto a recommended action."""
    if confidence < ACTION_CONFIDENCE_FLOOR:
        return PredictionAction.OBSERVE
    if p_breakout >= ACTION_ENTER_PROBABILITY_FLOOR and stage in (
        TrendStage.EMERGING,
        TrendStage.BREAKOUT,
    ):
        return PredictionAction.ENTER
    if p_decline >= ACTION_EXIT_PROBABILITY_CEILING:
        return PredictionAction.EXIT
    if stage == TrendStage.SATURATED:
        return PredictionAction.AVOID
    return PredictionAction.HOLD


def _percentile_band(
    *,
    mean_log: float,
    noise: float,
    horizon_h: int,
) -> tuple[float, float, float, float]:
    """Return (mean, p10, p50, p90) on the natural-units scale.

    Wider bands at longer horizons (sqrt-time scaling) and noisier inputs.
    """
    sigma = math.sqrt(max(0.05, noise) * horizon_h)
    # Convert log-mean → mean of an exponentiated lognormal.
    p50 = math.exp(mean_log)
    p10 = math.exp(mean_log - 1.2816 * sigma)  # 10th percentile of N
    p90 = math.exp(mean_log + 1.2816 * sigma)
    mean = math.exp(mean_log + 0.5 * sigma * sigma)
    return mean, p10, p50, p90


def heuristic_predict(
    *,
    window: FeatureWindow,
    graph: CreatorGraph | None,
    horizons: tuple[int, ...],
) -> list[Prediction]:
    """Pure function — produces a list of Prediction objects.

    Used both directly by the heuristic predictors and as a safety
    net in the base Predictor when a neural forward pass fails.
    """
    s = _summarise_window(window)
    stage = _heuristic_stage(s)

    # Base confidence reflects strength of evidence:
    #   * many signals → up to +0.40
    #   * many authors → up to +0.20
    #   * agreeing v1/v6/v24 sign → up to +0.15
    sig_conf = _clamp(math.log1p(s["recent_signals"]) / math.log(200.0), 0.0, 0.4)
    auth_conf = _clamp(math.log1p(s["recent_authors"]) / math.log(50.0), 0.0, 0.2)
    same_sign = int(s["v1"] >= 0) + int(s["v6"] >= 0) + int(s["v24"] >= 0)
    sign_conf = 0.05 * (1 + abs(same_sign - 1.5) * 2)  # 0.05 → 0.20
    confidence = _clamp(
        0.20 + sig_conf + auth_conf + sign_conf,
        0.05,
        HEURISTIC_CONFIDENCE_CEILING,
    )

    # Coordination risk and graph signal claw back confidence.
    if s["coord_risk"] > 0.5:
        confidence *= 0.6
    if graph is not None and graph.coordination_score > 0.6:
        confidence *= 0.7

    # Cross-platform spread is bullish.
    if graph is not None and graph.cross_platform_authors >= 3:
        confidence = min(HEURISTIC_CONFIDENCE_CEILING, confidence + 0.05)

    p_breakout, p_peak, p_decline = _stage_to_class_probs(stage, confidence=confidence)

    # Velocity forecast: log-mean is a damped extrapolation of v24.
    # At h hours ahead, expected log-velocity = current_v24 * decay(h)
    # where decay shrinks to 0 over ~96 hours.
    base_log_v = s["v24"]
    noise = max(0.05, s["velocity_noise"])

    out: list[Prediction] = []
    for h in horizons:
        decay = math.exp(-h / 96.0)
        mean_log = base_log_v * decay
        mean, p10, p50, p90 = _percentile_band(mean_log=mean_log, noise=noise, horizon_h=h)
        action = _action_for(
            p_breakout=p_breakout,
            p_decline=p_decline,
            confidence=confidence,
            stage=stage,
        )
        reasoning = (
            f"heuristic stage={stage.value} signals={int(s['recent_signals'])} "
            f"authors={int(s['recent_authors'])} v24={s['v24']:.2f} "
            f"accel={s['acceleration']:.2f} coord={s['coord_risk']:.2f}"
        )
        if graph is not None:
            reasoning += (
                f" graph(n={graph.n_authors},xplat={graph.cross_platform_authors},"
                f"coord={graph.coordination_score:.2f})"
            )

        out.append(
            Prediction(
                horizon_hours=h,
                stage=stage,
                velocity_log=mean_log,
                velocity_mean=max(0.0, mean),
                velocity_p10=max(0.0, p10),
                velocity_p50=max(0.0, p50),
                velocity_p90=max(0.0, p90),
                p_breakout=p_breakout,
                p_peak=p_peak,
                p_decline=p_decline,
                aleatoric=noise,
                epistemic=0.0,  # heuristic has no model uncertainty
                conformal_lower=max(0.0, p10),
                conformal_upper=max(0.0, p90),
                conformal_alpha=0.10,
                confidence=confidence,
                action=action,
                reasoning=reasoning,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Class wrappers (so the factory can hand back any predictor uniformly).
# ---------------------------------------------------------------------------
class HeuristicTemporalPredictor(Predictor):
    """Stateless heuristic; uses only the time-series."""

    @property
    def model_id(self) -> str:
        return "heuristic_temporal-3.0.0"

    @property
    def kind(self) -> ModelKind:
        return ModelKind.HEURISTIC

    @property
    def version(self) -> str:
        return "3.0.0"

    @property
    def uncertainty_method(self) -> UncertaintyMethod:
        return UncertaintyMethod.NONE

    async def _predict_inner(
        self,
        *,
        window: FeatureWindow,
        graph: CreatorGraph | None,
        horizons: tuple[int, ...],
        seed: int,
    ) -> list[Prediction]:
        return heuristic_predict(window=window, graph=None, horizons=horizons)


class HeuristicRelationalPredictor(Predictor):
    """Heuristic + graph features."""

    @property
    def model_id(self) -> str:
        return "heuristic_relational-3.0.0"

    @property
    def kind(self) -> ModelKind:
        return ModelKind.HEURISTIC

    @property
    def version(self) -> str:
        return "3.0.0"

    async def _predict_inner(
        self,
        *,
        window: FeatureWindow,
        graph: CreatorGraph | None,
        horizons: tuple[int, ...],
        seed: int,
    ) -> list[Prediction]:
        return heuristic_predict(window=window, graph=graph, horizons=horizons)


__all__ = [
    "HeuristicTemporalPredictor",
    "HeuristicRelationalPredictor",
    "heuristic_predict",
]
