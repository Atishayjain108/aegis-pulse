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
    EMA_LONG_PERIOD,
    EMA_SHORT_PERIOD,
    HEURISTIC_BREAKOUT_MIN_AUTHORS,
    HEURISTIC_BREAKOUT_MIN_SIGNALS,
    HEURISTIC_BREAKOUT_VELOCITY_24H,
    HEURISTIC_CONFIDENCE_CEILING,
    HEURISTIC_DECLINING_VELOCITY_24H,
    HEURISTIC_PEAK_DECEL_THRESHOLD,
    MOMENTUM_BEARISH_THRESHOLD,
    MOMENTUM_BULLISH_THRESHOLD,
    OLS_HORIZON_CAP_HOURS,
    OLS_STRONG_R2,
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


def _ema(series: list[float], *, period: int) -> float:
    """Exponential moving average (final value) — pure stdlib, O(N).

    Uses the standard 2/(period+1) smoothing factor so that the result
    matches widely-accepted TA convention (e.g. pandas ewm(span=period)).
    """
    if not series:
        return 0.0
    alpha = 2.0 / (period + 1)
    val = series[0]
    for v in series[1:]:
        val = alpha * v + (1.0 - alpha) * val
    return val


def _ema_series(series: list[float], *, period: int) -> list[float]:
    """Full EMA series (same length as input), earliest value first."""
    if not series:
        return []
    alpha = 2.0 / (period + 1)
    out = [series[0]]
    for v in series[1:]:
        out.append(alpha * v + (1.0 - alpha) * out[-1])
    return out


def _ols_slope_r2(series: list[float]) -> tuple[float, float]:
    """OLS slope and R² for y = slope*t + intercept, t in 0..n-1.

    Returns (0.0, 0.0) when the series is too short or degenerate.
    Pure stdlib — no numpy.
    """
    n = len(series)
    if n < 3:
        return 0.0, 0.0
    # Closed-form OLS using integer sums (exact for int t).
    t_sum = n * (n - 1) / 2
    t2_sum = n * (n - 1) * (2 * n - 1) / 6
    y_sum = sum(series)
    ty_sum = sum(float(t) * y for t, y in enumerate(series))
    denom = n * t2_sum - t_sum * t_sum
    if abs(denom) < 1e-10:
        return 0.0, 0.0
    slope = (n * ty_sum - t_sum * y_sum) / denom
    intercept = (y_sum - slope * t_sum) / n
    y_mean = y_sum / n
    ss_tot = sum((y - y_mean) ** 2 for y in series)
    if ss_tot < 1e-12:
        # All values identical — perfect fit by convention.
        return slope, 1.0
    ss_res = sum((y - (slope * t + intercept)) ** 2 for t, y in enumerate(series))
    r2 = max(0.0, 1.0 - ss_res / ss_tot)
    return slope, r2


def _summarise_window(window: FeatureWindow) -> dict[str, Any]:
    """Extract a small dict of scalar summaries from the window.

    Phase 3.1 additions (all pure stdlib, no numpy):
      ema_short / ema_long  — exponential moving averages of signal_count.
      momentum              — (ema_short / ema_long) - 1; positive = bullish.
      ols_slope / ols_r2    — OLS trend line over the last 24 h of counts.
      ma_cross_signal       — +1 bullish cross, -1 bearish, 0 no change.
    """
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

    # ------------------------------------------------------------------ #
    # Phase 3.1 — EMA / momentum / OLS trend analysis
    # ------------------------------------------------------------------ #
    counts_all = col(rows, "signal_count")

    # EMA of signal counts: short (3 h) and long (12 h).
    ema_s = _ema(counts_all, period=EMA_SHORT_PERIOD)
    ema_l = _ema(counts_all, period=EMA_LONG_PERIOD)
    # Momentum = fractional excess of short EMA over long EMA.
    momentum = (ema_s / (ema_l + 1e-9)) - 1.0

    # OLS slope over the last 24 buckets of signal counts.
    ols_window = counts_all[-24:] if len(counts_all) >= 24 else counts_all
    ols_slope, ols_r2 = _ols_slope_r2(ols_window)

    # MA cross detection: compare penultimate vs. final EMA relationship.
    # Short EMA series over all rows; check if the cross happened in the
    # last two buckets.
    ma_cross_signal = 0.0
    if len(counts_all) >= 2:
        ema_short_series = _ema_series(counts_all, period=EMA_SHORT_PERIOD)
        ema_long_series = _ema_series(counts_all, period=EMA_LONG_PERIOD)
        prev_diff = ema_short_series[-2] - ema_long_series[-2]
        curr_diff = ema_short_series[-1] - ema_long_series[-1]
        if prev_diff < 0 and curr_diff >= 0:
            ma_cross_signal = 1.0   # golden cross (bullish)
        elif prev_diff >= 0 and curr_diff < 0:
            ma_cross_signal = -1.0  # death cross (bearish)

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
        # Phase 3.1 additions
        "ema_short": ema_s,
        "ema_long": ema_l,
        "momentum": momentum,
        "ols_slope": ols_slope,
        "ols_r2": ols_r2,
        "ma_cross_signal": ma_cross_signal,
    }


def _heuristic_stage(s: dict[str, Any]) -> TrendStage:
    """Map summary scalars to a discrete TrendStage.

    Phase 3.1: momentum and OLS signals are used as secondary confirming
    evidence.  The primary velocity/acceleration rules are unchanged —
    momentum only acts as a tiebreaker or an early-warning signal.
    """
    v24 = s["v24"]
    v1 = s["v1"]
    accel = s["acceleration"]
    recent_signals = s["recent_signals"]
    recent_authors = s["recent_authors"]
    momentum = s.get("momentum", 0.0)
    ols_slope = s.get("ols_slope", 0.0)
    ols_r2 = s.get("ols_r2", 0.0)

    # Primary breakout: classic velocity + count + author threshold.
    if (
        v24 >= HEURISTIC_BREAKOUT_VELOCITY_24H
        and recent_signals >= HEURISTIC_BREAKOUT_MIN_SIGNALS
        and recent_authors >= HEURISTIC_BREAKOUT_MIN_AUTHORS
        and accel > 0
    ):
        return TrendStage.BREAKOUT

    # Momentum-assisted breakout: slightly below velocity threshold but
    # short EMA has crossed above long EMA (golden cross) with a positive
    # OLS slope — early breakout signal before volume fully arrives.
    if (
        v24 >= HEURISTIC_BREAKOUT_VELOCITY_24H * 0.75
        and momentum > MOMENTUM_BULLISH_THRESHOLD
        and ols_slope > 0
        and ols_r2 >= OLS_STRONG_R2
        and recent_signals >= HEURISTIC_BREAKOUT_MIN_SIGNALS * 0.6
        and accel > 0
    ):
        return TrendStage.BREAKOUT

    if v24 > 0 and accel <= HEURISTIC_PEAK_DECEL_THRESHOLD and recent_signals > 0:
        return TrendStage.PEAK

    # Momentum-confirmed decline: bearish EMA cross + negative OLS slope
    # provides higher confidence than velocity alone.
    if v24 <= HEURISTIC_DECLINING_VELOCITY_24H:
        return TrendStage.DECLINING

    if (
        momentum < MOMENTUM_BEARISH_THRESHOLD
        and ols_slope < 0
        and ols_r2 >= OLS_STRONG_R2
        and v24 < 0
    ):
        return TrendStage.DECLINING

    # OLS-confirmed emergence: positive trend line even when v24 is low.
    if v24 > 0 and accel > 0:
        return TrendStage.EMERGING

    if ols_slope > 0 and ols_r2 >= OLS_STRONG_R2 and v24 > 0:
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
    # BUGFIX (OMEGA Phase C): the PEAK tuple summed to 1.40·c and the BREAKOUT
    # tuple to 1.20·c. With the 0.75 ceiling, PEAK reached 1.05 > 1.0 and the
    # Prediction validator raised "class probabilities exceed 1.0" — so every
    # confident PEAK (and borderline BREAKOUT) prediction was silently DROPPED,
    # biasing the surviving sample. The three classes are mutually exclusive, so
    # their mass must satisfy p_breakout + p_peak + p_decline ≤ 1; the remainder
    # is the implicit DORMANT/EMERGING/SATURATED "other" mass. Each tuple below
    # now sums to ≤ 1·c ≤ ceiling, so the validator can never fire.
    if stage == TrendStage.BREAKOUT:
        return (0.80 * c, 0.15 * c, 0.05 * c)
    if stage == TrendStage.PEAK:
        return (0.15 * c, 0.70 * c, 0.15 * c)
    if stage == TrendStage.DECLINING:
        return (0.05 * c, 0.10 * c, 0.85 * c)
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


# ---------------------------------------------------------------------------
# STAGE 1.6 — action-space reachability (startup assertion)
# ---------------------------------------------------------------------------

# Max class-probability scale factors from _stage_to_class_probs, per axis.
# Keep in sync with the tuples above; the assertion below exists precisely
# because a tuple change (OMEGA B1) once made EXIT unreachable and nothing
# noticed until an audit did the arithmetic by hand.
_MAX_P_BREAKOUT_SCALE = 0.80  # BREAKOUT tuple
_MAX_P_DECLINE_SCALE = 0.85   # DECLINING / SATURATED tuples


def action_reachability() -> list[dict[str, object]]:
    """Per-action reachability given current thresholds and the confidence
    ceiling. Pure arithmetic — no I/O, no model load."""
    c = HEURISTIC_CONFIDENCE_CEILING
    max_p_breakout = _MAX_P_BREAKOUT_SCALE * c
    max_p_decline = _MAX_P_DECLINE_SCALE * c
    return [
        {
            "action": "ENTER",
            "threshold": f"p_breakout >= {ACTION_ENTER_PROBABILITY_FLOOR}"
            " (stage EMERGING|BREAKOUT)",
            "max_attainable": round(max_p_breakout, 4),
            "reachable": max_p_breakout >= ACTION_ENTER_PROBABILITY_FLOOR,
        },
        {
            "action": "EXIT",
            "threshold": f"p_decline >= {ACTION_EXIT_PROBABILITY_CEILING}",
            "max_attainable": round(max_p_decline, 4),
            "reachable": max_p_decline >= ACTION_EXIT_PROBABILITY_CEILING,
        },
        {
            "action": "OBSERVE",
            "threshold": f"confidence < {ACTION_CONFIDENCE_FLOOR}",
            "max_attainable": 1.0,
            "reachable": ACTION_CONFIDENCE_FLOOR > 0.0,
        },
        {
            "action": "AVOID",
            "threshold": "stage == SATURATED (conf >= floor, p_decline < ceiling)",
            "max_attainable": 1.0,
            "reachable": True,
        },
        {
            "action": "HOLD",
            "threshold": "default",
            "max_attainable": 1.0,
            "reachable": True,
        },
    ]


def assert_action_space_reachable() -> None:
    """Raise if any probability-gated action can never fire under the current
    constants. Called once at InferenceRunner startup."""
    dead = [r for r in action_reachability() if not r["reachable"]]
    if dead:
        raise RuntimeError(
            "unreachable prediction action(s) under current thresholds: "
            + ", ".join(
                f"{r['action']} (needs {r['threshold']}, max {r['max_attainable']})"
                for r in dead
            )
        )


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
    # max(0, ·) guards the log1p domain: a corrupted/adversarial window can
    # carry negative counts, and the heuristic must NEVER raise (doctrine).
    sig_conf = _clamp(math.log1p(max(0.0, s["recent_signals"])) / math.log(200.0), 0.0, 0.4)
    auth_conf = _clamp(math.log1p(max(0.0, s["recent_authors"])) / math.log(50.0), 0.0, 0.2)
    # BUGFIX (OMEGA Phase C): the old `0.05 * (1 + abs(same_sign - 1.5) * 2)`
    # awarded the SAME maximum (+0.20) to a unanimously DECLINING trend
    # (same_sign=0) as to a unanimously rising one (same_sign=3) — inflating
    # confidence on trends heading down — and its 0.20 ceiling let evidence
    # saturate near the cap. Reframe as a bounded *consistency* term: the
    # fraction of the three velocity horizons that agree on a sign, scaled into
    # [0, 0.10]. Direction is conveyed by `stage`, not by this term.
    same_sign = int(s["v1"] >= 0) + int(s["v6"] >= 0) + int(s["v24"] >= 0)
    agree_frac = max(same_sign, 3 - same_sign) / 3.0  # 0.33 (split) … 1.0 (unanimous)
    sign_conf = _clamp(0.10 * (agree_frac - 1.0 / 3.0) / (2.0 / 3.0), 0.0, 0.10)

    # Phase 3.1 — momentum / MA-cross / OLS confidence adjustments.
    # Each bonus is small and individually capped to prevent compounding
    # from pushing past HEURISTIC_CONFIDENCE_CEILING on its own.
    momentum = s.get("momentum", 0.0)
    ols_r2 = s.get("ols_r2", 0.0)
    ols_slope = s.get("ols_slope", 0.0)
    ma_cross = s.get("ma_cross_signal", 0.0)

    # Bullish momentum bonus (stage must agree).
    momentum_bonus = 0.0
    if momentum > MOMENTUM_BULLISH_THRESHOLD and stage in (
        TrendStage.BREAKOUT, TrendStage.EMERGING
    ):
        momentum_bonus = _clamp(momentum * 0.10, 0.0, 0.05)
    elif momentum < MOMENTUM_BEARISH_THRESHOLD and stage == TrendStage.DECLINING:
        momentum_bonus = _clamp(abs(momentum) * 0.08, 0.0, 0.04)

    # MA cross bonus: freshly crossed golden cross = early confirmation.
    ma_cross_bonus = 0.04 if ma_cross == 1.0 and stage in (
        TrendStage.BREAKOUT, TrendStage.EMERGING
    ) else 0.0

    # OLS trend confirmation: a reliable positive trend line adds evidence.
    ols_bonus = 0.0
    if ols_r2 >= OLS_STRONG_R2:
        if ols_slope > 0 and stage in (TrendStage.BREAKOUT, TrendStage.EMERGING):
            ols_bonus = ols_r2 * 0.05   # up to +0.05 at R²=1
        elif ols_slope < 0 and stage == TrendStage.DECLINING:
            ols_bonus = ols_r2 * 0.04   # declining trend confirmed

    confidence = _clamp(
        0.20 + sig_conf + auth_conf + sign_conf + momentum_bonus + ma_cross_bonus + ols_bonus,
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

    # Velocity forecast: log-mean is a damped extrapolation of v24,
    # now blended with an OLS trend component at short horizons.
    # At h hours ahead: mean_log = v24 * decay(h) + ols_blend(h)
    # where decay shrinks to 0 over ~96 h and ols_blend decays over ~48 h.
    base_log_v = s["v24"]
    noise = max(0.05, s["velocity_noise"])

    out: list[Prediction] = []
    for h in horizons:
        decay = math.exp(-h / 96.0)

        # Phase 3.1: OLS trend blend — only within the extrapolation window
        # and only when the trend is reliable (R² ≥ OLS_STRONG_R2).
        ols_blend = 0.0
        if ols_r2 >= OLS_STRONG_R2 and h <= OLS_HORIZON_CAP_HOURS:
            # sign(slope) × R² × small_scale, decaying with horizon.
            ols_blend = (
                math.copysign(ols_r2 * 0.15, ols_slope)
                * math.exp(-h / 48.0)
            )

        mean_log = base_log_v * decay + ols_blend

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
            f"accel={s['acceleration']:.2f} coord={s['coord_risk']:.2f} "
            f"momentum={momentum:.2f} ols_r2={ols_r2:.2f} ols_slope={ols_slope:.3f}"
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
    "HeuristicRelationalPredictor",
    "HeuristicTemporalPredictor",
    "heuristic_predict",
]
