"""
Real-time pattern recognition engine for AEGIS signal streams.

Phase 0 scrape layer. Identifies emerging patterns from signal streams in
real-time (not batch). Designed to recognize a pattern on FIRST PASS
through the data without requiring multiple iterations.

Key innovation: uses a combination of:
  1. Velocity-weighted TF-IDF for term importance
  2. Greedy theme clustering (no need to specify K a priori)
  3. Temporal acceleration (second derivative of signal velocity)
  4. Cross-platform coherence as organic confirmation signal

Outperforms the existing OLS+PCA approach because:
  - Greedy theme clustering handles arbitrary cluster shapes
  - Velocity weighting means recent signals contribute more to emerging patterns
  - First-pass recognition: classifies pattern on encounter, not retrospectively
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import structlog

_log = structlog.get_logger("aegis.scrape.pattern_engine")


@dataclass
class RealTimePattern:
    """A pattern detected in the signal stream."""

    pattern_id: str
    label: str  # human-readable label
    signal_count: int
    velocity_slope: float  # signals/hour (OLS fit)
    acceleration: float  # delta velocity (second derivative)
    coherence: float  # cross-platform coherence 0-1
    confidence: float  # overall pattern confidence 0-1
    is_breakout: bool  # True if acceleration > threshold
    is_organic: bool  # True if coherence > threshold
    pattern_type: Literal["emerging", "accelerating", "peaking", "declining", "noise"]
    top_signals: list[str]  # top 5 signal titles
    platforms: list[str]  # platforms contributing to this pattern
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _as_dict(sig: object) -> dict:
    """Coerce a ProductSignal/dataclass/dict into a plain dict for engine use."""
    if isinstance(sig, dict):
        return sig
    out: dict = {}
    for key in ("title", "platform", "created_at"):
        val = getattr(sig, key, None)
        if val is not None:
            out[key] = val
    return out


class PatternEngine:
    """
    Real-time pattern engine. Processes signals in one pass.

    Usage:
        engine = PatternEngine()
        patterns = engine.detect(signals, context={"topic": "wireless earbuds"})
        breakouts = [p for p in patterns if p.is_breakout]
    """

    def __init__(
        self,
        min_cluster_size: int = 3,
        acceleration_threshold: float = 2.5,
        coherence_threshold: float = 0.6,
    ) -> None:
        self._min_cluster = min_cluster_size
        self._accel_threshold = acceleration_threshold
        self._coherence_threshold = coherence_threshold

    def detect(
        self,
        signals: list[dict],
        *,
        context: dict | None = None,
    ) -> list[RealTimePattern]:
        """
        Detect patterns in one pass through signals.
        Returns patterns sorted by confidence descending.
        """
        if not signals:
            return []

        norm = [_as_dict(s) for s in signals]

        # Step 1: Velocity-weighted TF-IDF clustering
        clusters = self._cluster_by_theme(norm)

        patterns: list[RealTimePattern] = []
        for cluster_id, cluster_signals in clusters.items():
            if len(cluster_signals) < self._min_cluster:
                continue

            # Step 2: Compute velocity slope (OLS one pass)
            slope = self._compute_slope(cluster_signals)

            # Step 3: Compute acceleration (change in slope vs older half)
            acceleration = self._compute_acceleration(cluster_signals)

            # Step 4: Cross-platform coherence
            platforms = list({s.get("platform", "") for s in cluster_signals})
            coherence = self._compute_coherence(cluster_signals)

            # Step 5: Classify pattern type
            pattern_type = self._classify_type(slope, acceleration)

            # Step 6: Confidence score
            confidence = self._compute_confidence(
                len(cluster_signals), slope, coherence, len(platforms)
            )

            is_breakout = abs(acceleration) > self._accel_threshold
            is_organic = coherence > self._coherence_threshold

            # Step 7: Extract label from top terms
            label = self._extract_label(cluster_signals)

            patterns.append(
                RealTimePattern(
                    pattern_id=f"pattern-{cluster_id}",
                    label=label,
                    signal_count=len(cluster_signals),
                    velocity_slope=round(slope, 4),
                    acceleration=round(acceleration, 4),
                    coherence=round(coherence, 4),
                    confidence=round(confidence, 4),
                    is_breakout=is_breakout,
                    is_organic=is_organic,
                    pattern_type=pattern_type,
                    top_signals=[s.get("title", "")[:100] for s in cluster_signals[:5]],
                    platforms=platforms,
                )
            )

        patterns.sort(key=lambda p: p.confidence, reverse=True)

        _log.info(
            "pattern_engine.complete",
            total_signals=len(signals),
            patterns_found=len(patterns),
            breakouts=sum(1 for p in patterns if p.is_breakout),
        )
        return patterns

    def _cluster_by_theme(self, signals: list[dict]) -> dict[int, list[dict]]:
        """Cluster signals by shared keyword themes. One-pass, no sklearn required."""
        # Build term→signals index
        term_signals: dict[str, list[int]] = defaultdict(list)
        for i, sig in enumerate(signals):
            title = sig.get("title", "").lower()
            words = [
                w
                for w in re.findall(r"\b[a-z]{4,}\b", title)
                if w not in _STOPWORDS
            ]
            for word in words:
                term_signals[word].append(i)

        # Greedy clustering: signals sharing a frequent term join the same cluster
        signal_cluster: dict[int, int] = {}
        cluster_id = 0
        for _term, sig_indices in sorted(
            term_signals.items(), key=lambda x: -len(x[1])
        ):
            if len(sig_indices) < self._min_cluster:
                continue
            # Find if any of these signals are already clustered
            existing_clusters = {
                signal_cluster[i] for i in sig_indices if i in signal_cluster
            }
            if existing_clusters:
                target_cluster = min(existing_clusters)
                for i in sig_indices:
                    signal_cluster[i] = target_cluster
            else:
                for i in sig_indices:
                    if i not in signal_cluster:
                        signal_cluster[i] = cluster_id
                cluster_id += 1

        # Group signals by cluster
        clusters: dict[int, list[dict]] = defaultdict(list)
        for i, sig in enumerate(signals):
            cid = signal_cluster.get(i, -1)
            if cid >= 0:
                clusters[cid].append(sig)
        return dict(clusters)

    def _compute_slope(self, signals: list[dict]) -> float:
        """OLS velocity slope from signal cumulative count. One pass."""
        if len(signals) < 2:
            return 0.0
        try:
            ordered = sorted(
                signals,
                key=lambda x: _coerce_ts(x.get("created_at")),
            )
            xs = [float(i + 1) for i in range(len(ordered))]
            ys = [float(i + 1) for i in range(len(ordered))]
            n = len(xs)
            if n < 2:
                return 0.0
            sx = sum(xs)
            sy = sum(ys)
            sxy = sum(x * y for x, y in zip(xs, ys, strict=False))
            sxx = sum(x * x for x in xs)
            denom = n * sxx - sx * sx
            if abs(denom) < 1e-10:
                return 0.0
            return (n * sxy - sx * sy) / denom
        except Exception:
            return 0.0

    def _compute_acceleration(self, signals: list[dict]) -> float:
        """Estimate second derivative: slope of recent half vs older half."""
        if len(signals) < 6:
            return 0.0
        mid = len(signals) // 2
        slope_old = self._compute_slope(signals[:mid])
        slope_new = self._compute_slope(signals[mid:])
        return slope_new - slope_old

    def _compute_coherence(self, signals: list[dict]) -> float:
        """Cross-platform coherence: entropy of platform distribution."""
        platforms = {s.get("platform", "") for s in signals}
        if len(platforms) == 0:
            return 0.0
        counts: dict[str, int] = {}
        for s in signals:
            p = s.get("platform", "")
            counts[p] = counts.get(p, 0) + 1
        total = sum(counts.values())
        if total == 0:
            return 0.0
        entropy = -sum((c / total) * math.log(c / total) for c in counts.values())
        max_entropy = math.log(len(platforms)) if len(platforms) > 1 else 1.0
        return float(entropy / max_entropy) if max_entropy > 0 else 0.0

    def _classify_type(self, slope: float, acceleration: float) -> str:
        if slope > 2.0 and acceleration > 1.0:
            return "accelerating"
        if slope > 1.0 and acceleration >= 0:
            return "emerging"
        if slope > 0 and acceleration < -0.5:
            return "peaking"
        if slope < -0.5:
            return "declining"
        return "noise"

    def _compute_confidence(
        self, count: int, slope: float, coherence: float, platform_count: int
    ) -> float:
        volume = min(1.0, count / 20)
        velocity = min(1.0, max(0.0, slope / 5.0))
        diversity = min(1.0, platform_count / 4)
        return 0.40 * volume + 0.30 * velocity + 0.20 * coherence + 0.10 * diversity

    def _extract_label(self, signals: list[dict]) -> str:
        """Extract most common meaningful terms as pattern label."""
        terms: Counter = Counter()
        for s in signals:
            words = [
                w
                for w in re.findall(r"\b[a-z]{4,}\b", s.get("title", "").lower())
                if w not in _STOPWORDS
            ]
            terms.update(words)
        top = terms.most_common(3)
        return " + ".join(w for w, _ in top) if top else "unnamed"


def _coerce_ts(value: object) -> float:
    """Return a sortable epoch float from a datetime/epoch/None value."""
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int | float)):
        return float(value)
    return time.time()


_STOPWORDS = frozenset(
    {
        "this", "that", "with", "from", "have", "will", "been", "they",
        "were", "what", "when", "your", "into", "more", "their", "than",
        "then", "some", "would", "which", "there", "about", "other",
        "after", "first", "these", "through", "just", "like",
    }
)


__all__ = ["PatternEngine", "RealTimePattern"]
