"""
Signal normalization layer — makes signals from different platforms comparable.

Problem: Reddit upvotes (0–100k), GitHub stars (0–500k), product ratings (1–5),
         stock % change (-10%–+10%) are all stored as `score` but are
         statistically incomparable without normalization.

Solution:
- Z-score normalization within platform batches
- Percentile ranking across the combined swarm result
- Weighted confidence scaling by platform tier

Tier values match aegis.schemas.enums.SourceTier exactly.
"""
from __future__ import annotations

import bisect
import statistics
from typing import Any

# Tier weights keyed by actual SourceTier enum values.
TIER_WEIGHTS: dict[str, float] = {
    "T1_intent": 1.0,
    "T2_commerce": 0.85,
    "T3_search": 0.70,
    "T4_cultural": 0.60,
    "T5_alternative": 0.50,
}


def z_score_batch(signals: list[dict[str, Any]], score_field: str = "score") -> list[dict[str, Any]]:
    """Normalize scores within a platform batch using z-score."""
    scores = [float(s.get(score_field) or 0) for s in signals]
    if len(scores) < 2:
        return signals
    try:
        mean = statistics.mean(scores)
        stdev = statistics.stdev(scores)
    except statistics.StatisticsError:
        return signals
    if stdev == 0:
        return signals
    result = []
    for sig, raw_score in zip(signals, scores, strict=False):
        sig = dict(sig)
        sig["score_normalized"] = round((raw_score - mean) / stdev, 4)
        result.append(sig)
    return result


def percentile_rank_batch(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add percentile_rank (0–100) to each signal across the full combined batch."""
    scores = [float(s.get("score_normalized") or s.get("score") or 0) for s in signals]
    sorted_scores = sorted(scores)
    n = len(sorted_scores)
    result = []
    for sig, score in zip(signals, scores, strict=False):
        sig = dict(sig)
        rank = bisect.bisect_left(sorted_scores, score)
        sig["percentile_rank"] = round((rank / max(n - 1, 1)) * 100, 1)
        result.append(sig)
    return result


def apply_tier_weight(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scale normalized score by platform tier quality weight."""
    result = []
    for sig in signals:
        sig = dict(sig)
        tier = sig.get("tier", "T3_search")
        weight = TIER_WEIGHTS.get(tier, 0.7)
        base = sig.get("score_normalized") or sig.get("score") or 0
        sig["weighted_score"] = round(float(base) * weight, 4)
        result.append(sig)
    return result


def normalize_swarm_batch(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Full normalization pipeline for a combined swarm result."""
    by_platform: dict[str, list[dict[str, Any]]] = {}
    for sig in signals:
        p = sig.get("platform", "unknown")
        by_platform.setdefault(p, []).append(sig)

    normalized: list[dict[str, Any]] = []
    for platform_signals in by_platform.values():
        normalized.extend(z_score_batch(platform_signals))

    return apply_tier_weight(percentile_rank_batch(normalized))


__all__ = [
    "TIER_WEIGHTS",
    "apply_tier_weight",
    "normalize_swarm_batch",
    "percentile_rank_batch",
    "z_score_batch",
]
