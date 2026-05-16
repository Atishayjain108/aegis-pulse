"""
Tool: velocity_classify.

Pure-Python, no I/O. Bins multi-window velocity into a class label
and a calibrated [0,1] "breakout score". Used by SCOUT and SENTINEL
as a fast, deterministic baseline that does not depend on any LLM.

The thresholds were calibrated by replaying the Phase-1 backtest
corpus and choosing the point where precision flattens at the
target operating point. They live as named constants here so they
are easy to tune.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

from .base import ToolResult, tool_call

# Velocity thresholds (signals/hour, log-scaled). Tuned from
# Phase-1 backtest. DO NOT change without re-running backtests.
V_THRESHOLD_LOW = 5.0
V_THRESHOLD_MID = 20.0
V_THRESHOLD_HIGH = 100.0
V_THRESHOLD_BREAKOUT = 500.0


def _logistic(x: float, *, midpoint: float, slope: float = 1.0) -> float:
    import math

    z = slope * (x - midpoint)
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


@tool_call("velocity_classify")
async def classify(
    *,
    velocity_1h: float,
    velocity_6h: float,
    velocity_24h: float,
) -> ToolResult:
    """Classify multi-window velocity. Returns a dict with class,
    breakout_score, and acceleration."""
    # Acceleration: 1h vs 6h normalized rate. >0 means accelerating.
    rate_1h = max(0.0, velocity_1h)
    rate_6h_per_h = max(0.0, velocity_6h) / 6.0
    rate_24h_per_h = max(0.0, velocity_24h) / 24.0

    # Avoid divide-by-zero with a tiny epsilon; this also tames the
    # extreme-acceleration case when 6h velocity is nearly nothing.
    eps = 1e-3
    accel = (rate_1h - rate_6h_per_h) / max(rate_6h_per_h, eps)
    accel = max(-1.0, min(5.0, accel))  # clamp pathological values

    # Headline class is the highest threshold the 1h rate crosses,
    # but only if 24h trend agrees (sustained, not a single spike).
    sustained = rate_24h_per_h >= V_THRESHOLD_LOW * 0.5
    if rate_1h >= V_THRESHOLD_BREAKOUT and sustained:
        klass = "breakout"
    elif rate_1h >= V_THRESHOLD_HIGH and sustained:
        klass = "hot"
    elif rate_1h >= V_THRESHOLD_MID:
        klass = "warm"
    elif rate_1h >= V_THRESHOLD_LOW:
        klass = "rising"
    else:
        klass = "flat"

    # Breakout score blends magnitude (logistic centered at HIGH) and
    # accel (sigmoid centered at 0). 60/40 weight — magnitude wins.
    mag_score = _logistic(rate_1h, midpoint=V_THRESHOLD_HIGH, slope=0.02)
    accel_score = _logistic(accel, midpoint=0.5, slope=2.0)
    breakout_score = 0.6 * mag_score + 0.4 * accel_score
    breakout_score = max(0.0, min(1.0, breakout_score))

    return ToolResult.success(
        {
            "class": klass,
            "breakout_score": round(breakout_score, 4),
            "acceleration": round(accel, 4),
            "rate_1h": round(rate_1h, 4),
            "rate_6h_per_h": round(rate_6h_per_h, 4),
            "rate_24h_per_h": round(rate_24h_per_h, 4),
            "sustained": sustained,
        }
    )
