"""
Fusion ensemble — combines temporal + relational predictions.

For each (trend, horizon) we receive two bundles:
    * temporal bundle  (from PatchTST/Autoformer)
    * relational bundle (from HGT/GAT-fallback)

The fusion is a weighted average in the **probability simplex** for
the class distribution, plus a precision-weighted blend for the
log-velocity mean and σ. Weights are learned offline by minimising
a hold-out NLL via SGD; in the absence of trained weights we use
calibrated defaults (0.7 temporal / 0.3 relational, picked because
the temporal model is the stronger learner empirically and the
graph signal is mostly a *modifier* on confidence).

The fusion module also holds the post-hoc calibrators:
    * Platt scaling — a logistic regression on the breakout class.
    * Isotonic regression — a monotone re-ranker on calibration bins.

Both calibrators are tiny (≤ 1KB each) and live next to the fusion
weights in the registry.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..constants import HEURISTIC_CONFIDENCE_CEILING
from ..schemas import (
    Prediction,
    PredictionBundle,
    TrendStage,
)
from .heuristic import _action_for


@dataclass(frozen=True, slots=True)
class FusionWeights:
    """Linear blend weights. Always non-negative and sum to 1."""

    temporal: float = 0.70
    relational: float = 0.30

    def normalised(self) -> FusionWeights:
        s = self.temporal + self.relational
        if s <= 0:
            return FusionWeights(0.5, 0.5)
        return FusionWeights(self.temporal / s, self.relational / s)


@dataclass(frozen=True, slots=True)
class PlattCalibrator:
    """y = sigmoid(a * x + b) applied to a single class score."""

    a: float = 1.0
    b: float = 0.0

    def __call__(self, p: float) -> float:
        # Map a probability through Platt: apply to logit, then squash.
        eps = 1e-6
        p = max(eps, min(1.0 - eps, p))
        logit = math.log(p / (1.0 - p))
        z = self.a * logit + self.b
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        ez = math.exp(z)
        return ez / (1.0 + ez)


@dataclass(frozen=True, slots=True)
class IsotonicCalibrator:
    """Stepwise monotone non-decreasing function from [0,1] to [0,1].

    Stored as (xs, ys) with len(xs) == len(ys) and both sorted
    non-decreasing. `xs[0]` should be 0.0, `xs[-1]` should be 1.0.
    Applied via piecewise-linear interpolation between knots — this
    matches the behaviour of sklearn's IsotonicRegression on test
    inputs and avoids an external dependency.
    """

    xs: tuple[float, ...] = (0.0, 1.0)
    ys: tuple[float, ...] = (0.0, 1.0)

    def __call__(self, p: float) -> float:
        xs, ys = self.xs, self.ys
        if p <= xs[0]:
            return ys[0]
        if p >= xs[-1]:
            return ys[-1]
        # Binary search for the right segment.
        lo, hi = 0, len(xs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if xs[mid] <= p:
                lo = mid
            else:
                hi = mid
        x0, x1 = xs[lo], xs[hi]
        y0, y1 = ys[lo], ys[hi]
        if x1 == x0:
            return y0
        t = (p - x0) / (x1 - x0)
        return y0 + t * (y1 - y0)


def _blend_probs(
    p_t: tuple[float, float, float],
    p_r: tuple[float, float, float],
    *,
    weights: FusionWeights,
) -> tuple[float, float, float]:
    w = weights.normalised()
    return (
        w.temporal * p_t[0] + w.relational * p_r[0],
        w.temporal * p_t[1] + w.relational * p_r[1],
        w.temporal * p_t[2] + w.relational * p_r[2],
    )


def _blend_velocity(
    mu_t: float,
    sig_t: float,
    mu_r: float,
    sig_r: float,
    *,
    weights: FusionWeights,
) -> tuple[float, float]:
    """Precision-weighted mean (a.k.a. inverse-variance combo)."""
    w = weights.normalised()
    var_t = max(1e-4, sig_t * sig_t)
    var_r = max(1e-4, sig_r * sig_r)
    pt = w.temporal / var_t
    pr = w.relational / var_r
    if pt + pr <= 0:
        return mu_t, sig_t
    mu = (pt * mu_t + pr * mu_r) / (pt + pr)
    var = 1.0 / (pt + pr)
    return mu, math.sqrt(var)


def fuse(
    *,
    temporal: PredictionBundle,
    relational: PredictionBundle | None,
    weights: FusionWeights | None = None,
    platt_breakout: PlattCalibrator | None = None,
    isotonic_breakout: IsotonicCalibrator | None = None,
) -> list[Prediction]:
    """Fuse two PredictionBundles into a single list[Prediction].

    Horizons present in `temporal` define the output. Missing horizons
    in `relational` are treated as relational-confidence-zero (so
    the output equals the temporal prediction for that horizon).
    """
    weights = (weights or FusionWeights()).normalised()
    out: list[Prediction] = []
    for tp in temporal.predictions:
        rp = relational.by_horizon(tp.horizon_hours) if relational else None
        if rp is None:
            # No relational data → keep temporal but cap confidence
            # so we don't over-claim certainty.
            capped = min(tp.confidence, HEURISTIC_CONFIDENCE_CEILING)
            out.append(tp.model_copy(update={"confidence": capped}))
            continue

        p_b, p_p, p_d = _blend_probs(
            (tp.p_breakout, tp.p_peak, tp.p_decline),
            (rp.p_breakout, rp.p_peak, rp.p_decline),
            weights=weights,
        )
        # Apply post-hoc calibration to breakout probability.
        if platt_breakout is not None:
            p_b = platt_breakout(p_b)
        if isotonic_breakout is not None:
            p_b = isotonic_breakout(p_b)
        # Renormalise the trio to sum to ≤ 1.
        s = p_b + p_p + p_d
        if s > 1.0:
            p_b, p_p, p_d = p_b / s, p_p / s, p_d / s

        mu, sigma = _blend_velocity(
            tp.velocity_log,
            tp.aleatoric or 0.3,
            rp.velocity_log,
            rp.aleatoric or 0.3,
            weights=weights,
        )

        # Pick stage by argmax of expanded class probabilities (use
        # the temporal model's stage as the prior when probabilities
        # are too close to call).
        if max(p_b, p_p, p_d) >= 0.45:
            if p_b >= p_p and p_b >= p_d:
                stage = TrendStage.BREAKOUT
            elif p_p >= p_d:
                stage = TrendStage.PEAK
            else:
                stage = TrendStage.DECLINING
        else:
            stage = tp.stage

        # Fused confidence: weighted average, with a small bonus when
        # the two models agree (cosine of probability vectors > 0.9).
        agree = _cosine(
            (tp.p_breakout, tp.p_peak, tp.p_decline),
            (rp.p_breakout, rp.p_peak, rp.p_decline),
        )
        agree_bonus = 0.05 if agree >= 0.9 else 0.0
        confidence = max(
            0.0,
            min(
                0.98,
                weights.temporal * tp.confidence + weights.relational * rp.confidence + agree_bonus,
            ),
        )

        action = _action_for(p_breakout=p_b, p_decline=p_d, confidence=confidence, stage=stage)

        out.append(
            Prediction(
                horizon_hours=tp.horizon_hours,
                stage=stage,
                velocity_log=mu,
                velocity_mean=max(0.0, math.exp(mu + 0.5 * sigma * sigma)),
                velocity_p10=max(0.0, math.exp(mu - 1.2816 * sigma)),
                velocity_p50=max(0.0, math.exp(mu)),
                velocity_p90=max(0.0, math.exp(mu + 1.2816 * sigma)),
                p_breakout=p_b,
                p_peak=p_p,
                p_decline=p_d,
                aleatoric=sigma,
                epistemic=max(tp.epistemic, rp.epistemic),
                conformal_lower=max(0.0, math.exp(mu - 1.2816 * sigma)),
                conformal_upper=max(0.0, math.exp(mu + 1.2816 * sigma)),
                conformal_alpha=0.10,
                confidence=confidence,
                action=action,
                reasoning=(
                    f"fusion w_t={weights.temporal:.2f} w_r={weights.relational:.2f} "
                    f"agree={agree:.2f} stage={stage.value}"
                ),
            )
        )
    return out


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    num = sum(x * y for x, y in zip(a, b, strict=False))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


__all__ = [
    "FusionWeights",
    "IsotonicCalibrator",
    "PlattCalibrator",
    "fuse",
]
