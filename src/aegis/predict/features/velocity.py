"""
Velocity computation — multi-horizon log-space derivatives.

For a series of hourly signal counts c_t (length T), we compute:

    v_h(t) = log1p(Σ_{i=t-h+1..t} c_i)  -  log1p(Σ_{i=t-2h+1..t-h} c_i)

This is the "log-momentum" used by SCOUT and the heuristic baseline:
    * Symmetric around zero — easy to threshold.
    * Robust to scale: a 100→200 jump and a 1000→2000 jump are
      represented identically (≈ +0.69).
    * Works on sparse windows: log1p prevents NaN/-Inf when zero.

Three horizons are computed:
    * h=1  → "instantaneous"     (last hour vs prior hour)
    * h=6  → "short-term"        (last 6h vs prior 6h)
    * h=24 → "day-over-day"      (last 24h vs prior 24h)

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VelocityWindow:
    """Per-bucket velocity at three horizons, aligned with the input."""

    v1: list[float]  # length T
    v6: list[float]  # length T
    v24: list[float]  # length T


def _windowed_log1p_sum(series: list[float], *, h: int) -> list[float]:
    """log1p(Σ over last h buckets) at every position. Length T."""
    T = len(series)
    out = [0.0] * T
    running = 0.0
    for i in range(T):
        running += series[i]
        if i >= h:
            running -= series[i - h]
        # `running` may dip slightly negative due to float subtraction
        # of identical values — clamp to 0 to keep log1p well-defined.
        out[i] = math.log1p(max(0.0, running))
    return out


def compute_velocities(
    counts: list[float],
    *,
    horizons: tuple[int, ...] = (1, 6, 24),
) -> VelocityWindow:
    """Compute aligned multi-horizon velocities.

    Inputs
    ------
    counts:    hourly signal counts, length T (oldest first).
    horizons:  must be (1,6,24); other tuples are accepted for
               testing but the returned VelocityWindow always has
               .v1/.v6/.v24 aligned to those positions.

    Returns
    -------
    VelocityWindow with three lists of length T. Position 0 is the
    oldest bucket; values for positions where the lookback would
    underflow are zero.
    """
    if not counts:
        return VelocityWindow(v1=[], v6=[], v24=[])

    h1, h6, h24 = horizons[0], horizons[1], horizons[2]

    s1 = _windowed_log1p_sum(counts, h=h1)
    s6 = _windowed_log1p_sum(counts, h=h6)
    s24 = _windowed_log1p_sum(counts, h=h24)

    T = len(counts)
    v1 = [0.0] * T
    v6 = [0.0] * T
    v24 = [0.0] * T

    # v_h(t) = sum_h(t) - sum_h(t - h). For positions where the
    # offset would be negative, we leave zero — at the start of
    # the window we have no prior period to compare to.
    for t in range(T):
        if t - h1 >= 0:
            v1[t] = s1[t] - s1[t - h1]
        if t - h6 >= 0:
            v6[t] = s6[t] - s6[t - h6]
        if t - h24 >= 0:
            v24[t] = s24[t] - s24[t - h24]

    return VelocityWindow(v1=v1, v6=v6, v24=v24)


__all__ = ["VelocityWindow", "compute_velocities"]
