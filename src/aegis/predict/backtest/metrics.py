"""
Backtest metrics — pure stdlib so they work in any environment.

We compute:
  * macro precision / recall / F1 over stage classes
  * Brier score for probability calibration
  * conformal interval coverage (fraction of true velocities inside the
    predicted [p10, p90] band) — checks whether our uncertainty story
    is honest

The schemas we return populate `BacktestResult.metrics`, which the
promotion gate compares to the incumbent's metrics.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from ..schemas import Prediction, TrendStage

# Stage labels we score against. We deliberately do NOT score on
# DORMANT vs SATURATED separately — for the macro F1 used by the gate
# we map both to a "no-action" superclass, because confusing one for
# the other is operationally identical (no alert is fired).
_GATE_CLASSES: tuple[TrendStage, ...] = (
    TrendStage.EMERGING,
    TrendStage.BREAKOUT,
    TrendStage.PEAK,
    TrendStage.DECLINING,
)


def _per_class_prf(
    y_true: Sequence[TrendStage],
    y_pred: Sequence[TrendStage],
    cls: TrendStage,
) -> tuple[float, float, float]:
    """Return (precision, recall, F1) for a single class."""
    tp = sum(1 for t, p in zip(y_true, y_pred, strict=False) if t == cls and p == cls)
    fp = sum(1 for t, p in zip(y_true, y_pred, strict=False) if t != cls and p == cls)
    fn = sum(1 for t, p in zip(y_true, y_pred, strict=False) if t == cls and p != cls)
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
    return prec, rec, f1


def _brier(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    """Brier score on a binary outcome (lower is better)."""
    if not probs:
        return 0.0
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes, strict=False)) / len(probs)


def _coverage(
    p10s: Sequence[float],
    p90s: Sequence[float],
    truths: Sequence[float],
) -> float:
    """Fraction of true values that fall within [p10, p90]."""
    if not p10s:
        return 0.0
    inside = sum(1 for lo, hi, t in zip(p10s, p90s, truths, strict=False) if lo <= t <= hi)
    return inside / len(p10s)


def compute_metrics(
    predictions: Sequence[Prediction],
    truths: Sequence[dict],
) -> dict[str, Any]:
    """Compute the full metrics dict consumed by `BacktestResult`.

    Args:
        predictions: model outputs at horizon `h` for some set of trends.
        truths: parallel list of dicts. Each dict has keys:
            * stage      : TrendStage   — true stage at t + h
            * velocity   : float        — true Δlog-count over horizon h
            * breakout   : 0|1          — did the trend ever cross
                                          breakout threshold within h?

    Returns:
        Flat dict of metric → float, suitable for `BacktestResult`.
    """
    n = len(predictions)
    if n == 0 or n != len(truths):
        return {
            "n": n,
            "macro_precision": 0.0,
            "macro_recall": 0.0,
            "macro_f1": 0.0,
            "brier_breakout": 0.0,
            "coverage_p10_p90": 0.0,
            "mae_velocity": 0.0,
        }

    y_true = [t["stage"] for t in truths]
    y_pred = [p.stage for p in predictions]

    precs, recs, f1s = [], [], []
    for cls in _GATE_CLASSES:
        prec, rec, f1 = _per_class_prf(y_true, y_pred, cls)
        precs.append(prec)
        recs.append(rec)
        f1s.append(f1)

    p_breakout = [p.p_breakout for p in predictions]
    o_breakout = [int(t.get("breakout", 0)) for t in truths]
    brier = _brier(p_breakout, o_breakout)

    p10s = [p.velocity_p10 for p in predictions]
    p90s = [p.velocity_p90 for p in predictions]
    truth_v = [float(t.get("velocity", 0.0)) for t in truths]
    cov = _coverage(p10s, p90s, truth_v)

    mae = sum(abs(p.velocity_log - t) for p, t in zip(predictions, truth_v, strict=False)) / n

    return {
        "n": n,
        "macro_precision": sum(precs) / len(precs),
        "macro_recall": sum(recs) / len(recs),
        "macro_f1": sum(f1s) / len(f1s),
        "brier_breakout": brier,
        "coverage_p10_p90": cov,
        "mae_velocity": mae,
        # per-class for debug/dashboards
        **{f"{cls.value}_f1": f1 for cls, f1 in zip(_GATE_CLASSES, f1s, strict=False)},
    }


def expected_calibration_error(
    probs: Sequence[float],
    outcomes: Sequence[int],
    n_bins: int = 10,
) -> float:
    """ECE — average gap between bucket-mean prob and bucket-mean outcome.

    Reported alongside Brier so we can distinguish "well-calibrated but
    high-variance" from "low-variance but biased".
    """
    if not probs:
        return 0.0
    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, o in zip(probs, outcomes, strict=False):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append((p, o))
    total = len(probs)
    ece = 0.0
    for bucket in bins:
        if not bucket:
            continue
        avg_p = sum(p for p, _ in bucket) / len(bucket)
        avg_o = sum(o for _, o in bucket) / len(bucket)
        ece += (len(bucket) / total) * abs(avg_p - avg_o)
    return ece


def reliability_curve(
    probs: Sequence[float],
    outcomes: Sequence[int],
    n_bins: int = 10,
) -> list[tuple[float, float, int]]:
    """Per-bucket (mean_prob, observed_rate, count) — for plotting."""
    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, o in zip(probs, outcomes, strict=False):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append((p, o))
    out: list[tuple[float, float, int]] = []
    for bucket in bins:
        if not bucket:
            continue
        avg_p = sum(p for p, _ in bucket) / len(bucket)
        avg_o = sum(o for _, o in bucket) / len(bucket)
        out.append((avg_p, avg_o, len(bucket)))
    return out


def safe_log_loss(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    """Bounded log-loss — clamps to [eps, 1-eps] before log."""
    eps = 1e-7
    if not probs:
        return 0.0
    total = 0.0
    for p, o in zip(probs, outcomes, strict=False):
        p_c = max(eps, min(1 - eps, p))
        total += -(o * math.log(p_c) + (1 - o) * math.log(1 - p_c))
    return total / len(probs)
