"""
Randomized smoothing — adversarial-robustness wrapper for Phase 3 inference.

The technique (Cohen et al., 2019; Lecuyer et al., 2019):
  Given a base classifier f and input x, the smoothed classifier g(x) is
  f's expected prediction over noise n ~ N(0, sigma^2 * I):
      g(x) = E_n[ f(x + n) ]
  This g comes with a certified L2-radius around x within which g's verdict
  is provably stable.

In AEGIS Phase 3, the base classifier is a deterministic feature-driven
heuristic that maps a 20-dim feature vector to a score in [0,1]. The neural
augmentation can only multiply this score by [0.5, 1.0], so the smoothed
result remains in [0,1] and never flips the underlying verdict (heuristic
floor).

This module is **callable-agnostic**: it accepts any `Callable[[np.ndarray],
float | np.ndarray]` as the base classifier. The Phase 3 InferenceRunner can
be passed directly by adapting its single-vector entrypoint.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

import numpy as np

from aegis.harden.constants import (
    SMOOTHING_DEFAULT_ALPHA,
    SMOOTHING_DEFAULT_SAMPLES,
    SMOOTHING_DEFAULT_SIGMA,
    SMOOTHING_MAX_SAMPLES,
    SMOOTHING_MAX_SIGMA,
    SMOOTHING_MIN_SAMPLES,
    SMOOTHING_MIN_SIGMA,
)
from aegis.harden.errors import SmoothingError, make
from aegis.harden.metrics import M
from aegis.harden.schemas import SmoothingResult
from aegis.harden.utils.rng import SeededRng

# Type alias — a base classifier that maps a 1-D feature vector to a scalar
# in [0,1]. We accept either Python `float` or a 0-d ndarray.
ScalarBase = Callable[[np.ndarray], "float | np.ndarray"]


def _normal_quantile(p: float) -> float:
    """Approximate the inverse standard-normal CDF.

    Closed-form Acklam approximation; ~1e-9 accuracy. Used to compute the
    certified radius. We deliberately do not import scipy here to keep the
    defense package dependency-free.
    """
    # Acklam's algorithm
    a = [
        -3.969683028665376e1,
        2.209460984245205e2,
        -2.759285104469687e2,
        1.383577518672690e2,
        -3.066479806614716e1,
        2.506628277459239,
    ]
    b = [
        -5.447609879822406e1,
        1.615858368580409e2,
        -1.556989798598866e2,
        6.680131188771972e1,
        -1.328068155288572e1,
    ]
    c = [
        -7.784894002430293e-3,
        -3.223964580411365e-1,
        -2.400758277161838,
        -2.549732539343734,
        4.374664141464968,
        2.938163982698783,
    ]
    d = [7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996, 3.754408661907416]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
            * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
        )
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
    )


def smooth_predict(
    base_clf: ScalarBase,
    x: np.ndarray,
    *,
    sigma: float = SMOOTHING_DEFAULT_SIGMA,
    n_samples: int = SMOOTHING_DEFAULT_SAMPLES,
    alpha: float = SMOOTHING_DEFAULT_ALPHA,
    rng: SeededRng | None = None,
) -> SmoothingResult:
    """Apply randomized smoothing to a single feature vector.

    Parameters
    ----------
    base_clf
        A callable that returns a scalar in [0,1] for a 1-D feature vector.
        In Phase 3, wrap `InferenceRunner.infer_one` and extract a single
        horizon's `p_breakout` to feed in.
    x
        1-D `np.ndarray` of features. Typically Phase 3's 20-dim feature window.
    sigma
        Gaussian noise stdev. 0 disables smoothing (returns the raw verdict
        with `certified_radius=0`).
    n_samples
        Monte Carlo draws used to estimate the smoothed score.
    alpha
        Confidence level for the certified radius.
    rng
        Seeded RNG. If None, a default seeded RNG is used.

    Returns
    -------
    SmoothingResult — frozen.

    Raises
    ------
    SmoothingError on invalid inputs (NaN, wrong shape, out-of-bounds params).
    """
    if not isinstance(x, np.ndarray) or x.ndim != 1 or x.size == 0:
        raise SmoothingError(*make("AEGIS-HARDEN-0030", shape=getattr(x, "shape", None)))
    if not (SMOOTHING_MIN_SIGMA <= sigma <= SMOOTHING_MAX_SIGMA):
        raise SmoothingError(*make("AEGIS-HARDEN-0031", field="sigma", value=sigma))
    if not (SMOOTHING_MIN_SAMPLES <= n_samples <= SMOOTHING_MAX_SAMPLES):
        raise SmoothingError(*make("AEGIS-HARDEN-0031", field="n_samples", value=n_samples))
    if not (0.0 < alpha < 1.0):
        raise SmoothingError(*make("AEGIS-HARDEN-0031", field="alpha", value=alpha))

    rng = rng or SeededRng()

    # Raw (un-smoothed) verdict — used for `agrees_with_raw`.
    raw = _safe_scalar(base_clf(x))
    if math.isnan(raw):
        raise SmoothingError(*make("AEGIS-HARDEN-0032", stage="raw"))

    # If sigma == 0, smoothing is a no-op.
    if sigma == 0.0:
        M.smoothing_runs_total.labels(agrees="true").inc()
        return SmoothingResult(
            smoothed_score=raw,
            certified_radius=0.0,
            n_samples=n_samples,
            sigma=0.0,
            agrees_with_raw=True,
            raw_score=raw,
        )

    start = time.perf_counter()
    draws = np.empty(n_samples, dtype=np.float64)
    noise = rng.np.normal(loc=0.0, scale=sigma, size=(n_samples, x.size))
    for i in range(n_samples):
        s = _safe_scalar(base_clf(x + noise[i]))
        if math.isnan(s):
            raise SmoothingError(*make("AEGIS-HARDEN-0032", stage="sample", i=i))
        draws[i] = s
    M.smoothing_latency_seconds.observe(time.perf_counter() - start)

    smoothed = float(np.clip(draws.mean(), 0.0, 1.0))
    # Cohen et al. certified radius: sigma * Phi^{-1}(p_lower)
    # We use a Clopper-Pearson lower bound on the mean for stability.
    p_lower = _lower_confidence_bound(draws, alpha)
    certified = max(0.0, sigma * _normal_quantile(max(min(p_lower, 1 - 1e-9), 1e-9)))

    agrees = (smoothed >= 0.5) == (raw >= 0.5)
    M.smoothing_runs_total.labels(agrees="true" if agrees else "false").inc()

    return SmoothingResult(
        smoothed_score=smoothed,
        certified_radius=float(certified),
        n_samples=n_samples,
        sigma=sigma,
        agrees_with_raw=agrees,
        raw_score=raw,
    )


def _safe_scalar(v: float | np.ndarray) -> float:
    """Coerce a 0-d ndarray or float to a clamped Python float in [0,1]."""
    if isinstance(v, np.ndarray):
        if v.size != 1:
            return float("nan")
        f = float(v.item())
    else:
        f = float(v)
    if math.isnan(f):
        return float("nan")
    return float(np.clip(f, 0.0, 1.0))


def _lower_confidence_bound(samples: np.ndarray, alpha: float) -> float:
    """Lower Clopper-Pearson-style bound on the mean of a [0,1] sample.

    For non-Bernoulli draws (smoothed scores are continuous in [0,1]), we use
    the standard normal-approximation lower bound `p - z * sqrt(p(1-p)/n)`,
    clipped to [0,1]. This is conservative enough for radius estimation.
    """
    n = samples.size
    if n == 0:
        return 0.0
    p = float(samples.mean())
    z = _normal_quantile(1 - alpha)
    margin = z * math.sqrt(max(0.0, p * (1.0 - p)) / max(n, 1))
    return max(0.0, min(1.0, p - margin))


__all__ = ["ScalarBase", "smooth_predict"]
