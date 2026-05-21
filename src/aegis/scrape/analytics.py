"""Signal time-series analytics — OLS velocity regression and PCA noise reduction.

Phase 5 statistical modeling pipeline. Both functions are pure, side-effect-free,
and dependency-minimal: OLS runs in pure stdlib; PCA uses NumPy when available
and silently no-ops when it is not.

Public API
----------
compute_velocity_slope(signals, ...)
    Buckets signals by arrival time and fits OLS linear regression on the
    (bucket_index → count) series. Slope β₁ (signals/hour) tells you whether
    a trend is accelerating (+) or decelerating (−). R² measures reliability.
    Clusters above HIGH_PRIORITY_SLOPE are flagged for the Executive Agent.

pca_denoise_vectors(vectors, ...)
    Projects L2-normalised TF-IDF sparse dicts onto the top-K principal
    components (via NumPy SVD), then reconstructs back to the original
    vocabulary. Noise dimensions orthogonal to the dominant signal directions
    are discarded. Falls back to identity when numpy is absent or corpus < 3.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

_log = structlog.get_logger("aegis.scrape.analytics")

# Slope threshold (signals/hour growth rate) above which a cluster is
# immediately flagged "High Priority" for the Executive Agent layer.
HIGH_PRIORITY_SLOPE: float = 2.0


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VelocityRegression:
    """OLS linear regression result for a signal cluster's arrival time-series."""

    slope: float
    """β₁ — signals per hour. Positive = accelerating, negative = decelerating."""

    intercept: float
    """β₀ — baseline signal rate at the start of the observation window."""

    r_squared: float
    """Coefficient of determination [0, 1]. Below 0.3 = too noisy to trust."""

    bucket_count: int
    """Number of hourly buckets used in the regression."""

    is_high_priority: bool
    """True when slope > HIGH_PRIORITY_SLOPE and r_squared > 0.3."""

    velocity_class: str
    """'accelerating' | 'stable' | 'decelerating'."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _posted_timestamp(sig: Any) -> float | None:
    """Extract a Unix timestamp float from a ProductSignal or plain dict."""
    if hasattr(sig, "posted_at") and sig.posted_at is not None:
        ts = sig.posted_at
        return ts.timestamp() if hasattr(ts, "timestamp") else None
    if hasattr(sig, "provenance"):
        scraped = getattr(sig.provenance, "scraped_at", None)
        if scraped is not None and hasattr(scraped, "timestamp"):
            return scraped.timestamp()
    if isinstance(sig, dict):
        for attr in ("captured_at", "posted_at", "scraped_at"):
            val = sig.get(attr)
            if val is not None and hasattr(val, "timestamp"):
                return val.timestamp()
    return None


def _ols(x: list[float], y: list[float]) -> tuple[float, float, float]:
    """Pure-Python OLS: returns (slope, intercept, r_squared).

    Uses the closed-form solution β = (XᵀX)⁻¹Xᵀy for a single predictor.
    Returns (0.0, mean_y, 0.0) when there is insufficient variance to fit.
    """
    n = len(x)
    if n < 2:  # pragma: no cover
        return 0.0, (y[0] if y else 0.0), 0.0

    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y, strict=False))
    sum_x2 = sum(xi * xi for xi in x)

    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-12:
        return 0.0, sum_y / n, 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    mean_y = sum_y / n
    ss_tot = sum((yi - mean_y) ** 2 for yi in y)
    if ss_tot < 1e-12:
        r_sq = 1.0
    else:
        y_hat = [slope * xi + intercept for xi in x]
        ss_res = sum((yi - yh) ** 2 for yi, yh in zip(y, y_hat, strict=False))
        r_sq = max(0.0, min(1.0, 1.0 - ss_res / ss_tot))

    return slope, intercept, r_sq


# ---------------------------------------------------------------------------
# Public: OLS velocity regression
# ---------------------------------------------------------------------------


def compute_velocity_slope(
    signals: list[Any],
    *,
    lookback_hours: int = 72,
    bucket_hours: float = 1.0,
) -> VelocityRegression:
    """Fit OLS on the hourly arrival rate of *signals*.

    Divides [now − lookback_hours, now] into `bucket_hours`-wide windows,
    counts arrivals per window, then regresses bucket_index → count.

    Signals with no parseable timestamp are silently skipped; they contribute
    to the source set but not to the time-series fit. An empty time-series
    returns a zero-slope, zero-R² result — never raises.

    Args:
        signals:       List of ProductSignal objects or plain dicts.
        lookback_hours: How far back to include signals (default: 72 h).
        bucket_hours:   Width of each time bucket in hours (default: 1 h).

    Returns:
        A frozen VelocityRegression with slope, R², high-priority flag.
    """
    now_ts = datetime.now(UTC).timestamp()
    cutoff = now_ts - lookback_hours * 3600.0
    n_buckets = max(2, int(math.ceil(lookback_hours / bucket_hours)))
    bucket_width_s = bucket_hours * 3600.0

    counts: list[int] = [0] * n_buckets

    for sig in signals:
        ts = _posted_timestamp(sig)
        if ts is None or ts < cutoff:
            continue
        age_s = now_ts - ts
        idx = int((lookback_hours * 3600.0 - age_s) / bucket_width_s)
        idx = max(0, min(n_buckets - 1, idx))
        counts[idx] += 1

    x = [float(i) for i in range(n_buckets)]
    y = [float(c) for c in counts]

    slope, intercept, r_sq = _ols(x, y)

    is_hp = slope > HIGH_PRIORITY_SLOPE and r_sq > 0.3

    if slope > 0.5:
        vel_class = "accelerating"
    elif slope < -0.5:
        vel_class = "decelerating"
    else:
        vel_class = "stable"

    _log.debug(
        "analytics.velocity_regression",
        slope=round(slope, 3),
        r_squared=round(r_sq, 3),
        is_high_priority=is_hp,
        velocity_class=vel_class,
        n_signals=len(signals),
        n_buckets=n_buckets,
    )

    return VelocityRegression(
        slope=round(slope, 4),
        intercept=round(intercept, 4),
        r_squared=round(r_sq, 4),
        bucket_count=n_buckets,
        is_high_priority=is_hp,
        velocity_class=vel_class,
    )


# ---------------------------------------------------------------------------
# Public: PCA noise reduction
# ---------------------------------------------------------------------------


def pca_denoise_vectors(
    vectors: list[dict[str, float]],
    *,
    n_components: int | None = None,
    variance_retained: float = 0.90,
) -> list[dict[str, float]]:
    """Remove noise dimensions from TF-IDF vectors via truncated PCA.

    Projects *vectors* onto the principal components that jointly explain
    *variance_retained* of total variance, then reconstructs back into the
    original feature space. Noise components — orthogonal to the dominant
    signal directions — are discarded, producing cleaner cluster boundaries.

    Falls back to returning *vectors* unchanged when:
    - numpy is not installed
    - fewer than 3 vectors are supplied (PCA is undefined)
    - all vectors are empty (no vocabulary)

    Args:
        vectors:          L2-normalised TF-IDF sparse dicts from ``_build_tfidf``.
        n_components:     Fixed number of PCA components to keep. When None,
                          the function uses *variance_retained* to choose.
        variance_retained: Target cumulative explained-variance ratio (0, 1].
                           Ignored when *n_components* is set explicitly.

    Returns:
        Denoised sparse dicts, same length as *vectors*, re-L2-normalised.
    """
    if len(vectors) < 3:
        return vectors

    try:
        import numpy as np
    except ImportError:
        _log.debug("analytics.pca_skipped", reason="numpy_not_available")
        return vectors

    # Build union vocabulary
    vocab: list[str] = sorted({term for v in vectors for term in v})
    if not vocab:
        return vectors

    V = len(vocab)
    term_idx = {t: i for i, t in enumerate(vocab)}
    N = len(vectors)

    # Dense matrix [N × V]
    mat = np.zeros((N, V), dtype=np.float64)
    for row, vec in enumerate(vectors):
        for term, weight in vec.items():
            col = term_idx.get(term)
            if col is not None:
                mat[row, col] = weight

    # Centre each column so PCA captures variance, not mean offset
    col_mean = mat.mean(axis=0)
    mat_c = mat - col_mean

    try:
        U, s, Vt = np.linalg.svd(mat_c, full_matrices=False)
    except np.linalg.LinAlgError:
        _log.warning("analytics.pca_svd_failed")
        return vectors

    total_var = float(np.sum(s**2))
    if total_var < 1e-12:
        return vectors

    # Choose k
    if n_components is not None:
        k = max(1, min(int(n_components), len(s)))
    else:
        cumvar = np.cumsum(s**2) / total_var
        k = int(min(np.searchsorted(cumvar, variance_retained) + 1, len(s)))

    # Reconstruct from top-k components and re-add column means
    mat_denoised = (U[:, :k] @ np.diag(s[:k]) @ Vt[:k, :]) + col_mean

    # Re-normalise each row to L2 = 1 (consistent with _build_tfidf output)
    norms = np.linalg.norm(mat_denoised, axis=1, keepdims=True)
    norms = np.where(norms < 1e-12, 1.0, norms)
    mat_denoised /= norms

    # Convert back to sparse dicts, dropping near-zero entries
    result: list[dict[str, float]] = []
    for row in range(N):
        vec_out: dict[str, float] = {}
        for col, term in enumerate(vocab):
            val = float(mat_denoised[row, col])
            if abs(val) > 1e-6:
                vec_out[term] = val
        result.append(vec_out)

    _log.debug(
        "analytics.pca_complete",
        n_vectors=N,
        vocab_size=V,
        components_kept=k,
        variance_target=variance_retained,
    )
    return result


__all__ = [
    "HIGH_PRIORITY_SLOPE",
    "VelocityRegression",
    "compute_velocity_slope",
    "pca_denoise_vectors",
]
