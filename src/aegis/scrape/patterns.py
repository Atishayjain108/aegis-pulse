"""Signal pattern detection — cluster scraped signals into emerging themes.

Uses TF-IDF weighted unigrams + bigrams with cosine similarity to group
signals into thematic clusters, then ranks those clusters by size and
recency weight. Requires zero external ML dependencies — pure stdlib.

Usage::

    from aegis.scrape.patterns import detect_patterns, PatternCluster

    clusters = detect_patterns(signals, min_cluster_size=2)
    for c in clusters:
        print(c.label, c.signal_count, f"velocity={c.velocity_score:.2f}")
        for title in c.top_titles:
            print("  -", title)
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aegis.scrape.analytics import compute_velocity_slope, pca_denoise_vectors

# Stopwords — stripped before TF-IDF so they don't dilute topic signals
_STOPWORDS = frozenset(
    "a an the is are was were be been being have has had do does did will would "
    "could should may might shall can of in on at by for with about against between "
    "into through during before after above below to from up down out off over under "
    "again further then once here there when where why how all both each few more "
    "most other some such no nor not only same so than too very just also now new "
    "says said report says according sources amid amid amid amid amid amid amid".split()
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PatternCluster:
    """A group of signals sharing a common theme."""

    label: str
    """Human-readable label: top 4 TF-IDF terms joined with spaces."""

    signal_count: int
    """Number of signals in this cluster."""

    platforms: list[str]
    """Unique platform names contributing to this cluster."""

    top_titles: list[str]
    """Up to 5 representative signal titles."""

    representative_terms: list[str]
    """Top 8 terms by cluster TF-IDF weight (for display / labelling)."""

    velocity_score: float
    """Relative cluster size score in [0, 1] — larger clusters score higher."""

    recency_weight: float
    """Fraction of signals posted in the last 6 hours, in [0, 1]."""

    # ── Phase 5: statistical velocity fields ──────────────────────────────
    velocity_slope: float = 0.0
    """OLS regression slope (signals/hour). Positive = accelerating."""

    r_squared: float = 0.0
    """Goodness-of-fit for the slope estimate. Below 0.3 = too noisy to trust."""

    is_high_priority: bool = False
    """True when slope > HIGH_PRIORITY_SLOPE and R² > 0.3. Flagged for Executive Agent."""

    member_indices: list[int] = field(default_factory=list, repr=False)
    """Indices into the original signal list (for downstream processing)."""


# ---------------------------------------------------------------------------
# Tokenization and TF-IDF
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens + bigrams, stopwords removed, length ≥ 2."""
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower())
             if w not in _STOPWORDS and len(w) >= 2]
    bigrams = [f"{words[i]}_{words[i + 1]}" for i in range(len(words) - 1)]
    return words + bigrams


def _build_tfidf(token_lists: list[list[str]]) -> list[dict[str, float]]:
    """Compute L2-normalised TF-IDF vectors for each document."""
    n = len(token_lists)
    if n == 0:
        return []

    # Document frequency (number of docs that contain each term)
    df: Counter[str] = Counter()
    for tokens in token_lists:
        for t in set(tokens):
            df[t] += 1

    vectors: list[dict[str, float]] = []
    for tokens in token_lists:
        if not tokens:
            vectors.append({})
            continue
        tf = Counter(tokens)
        vec: dict[str, float] = {}
        for term, count in tf.items():
            tf_val = count / len(tokens)
            # Smooth IDF avoids zero for terms appearing in all docs
            idf_val = math.log((n + 1) / (df[term] + 1)) + 1.0
            vec[term] = tf_val * idf_val

        # L2 normalise
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0.0:
            vec = {k: v / norm for k, v in vec.items()}
        vectors.append(vec)

    return vectors


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    # Iterate smaller dict for efficiency
    if len(a) > len(b):
        a, b = b, a
    return sum(a_val * b.get(term, 0.0) for term, a_val in a.items())


# ---------------------------------------------------------------------------
# Greedy clustering
# ---------------------------------------------------------------------------


def _greedy_cluster(
    vectors: list[dict[str, float]],
    threshold: float,
) -> list[list[int]]:
    """Assign each signal to the first cluster whose pivot exceeds *threshold*.

    Returns a list of clusters, each cluster being a list of signal indices.
    The first element of each cluster is its pivot (representative signal).
    """
    clusters: list[list[int]] = []
    pivot_vectors: list[dict[str, float]] = []

    for i, vec in enumerate(vectors):
        best = -1
        best_sim = threshold - 1e-6  # must strictly exceed threshold
        for j, pv in enumerate(pivot_vectors):
            sim = _cosine(vec, pv)
            if sim > best_sim:
                best_sim = sim
                best = j

        if best >= 0:
            clusters[best].append(i)
        else:
            clusters.append([i])
            pivot_vectors.append(vec)

    return clusters


# ---------------------------------------------------------------------------
# Signal field accessors (works on ProductSignal objects and plain dicts)
# ---------------------------------------------------------------------------


def _title(sig: Any) -> str:
    if hasattr(sig, "title"):
        return (sig.title or "").strip()
    return str(sig.get("title") or "").strip()


def _platform(sig: Any) -> str:
    if hasattr(sig, "platform"):
        p = sig.platform
        return p.value if hasattr(p, "value") else str(p)
    return str(sig.get("platform") or "unknown")


def _posted_at(sig: Any) -> datetime | None:
    if hasattr(sig, "posted_at") and sig.posted_at is not None:
        return sig.posted_at
    if hasattr(sig, "provenance"):
        return getattr(sig.provenance, "scraped_at", None)
    ts = sig.get("captured_at") or sig.get("posted_at") or sig.get("scraped_at")
    if ts is not None and hasattr(ts, "tzinfo"):
        return ts
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_patterns(
    signals: list[Any],
    *,
    min_cluster_size: int = 2,
    similarity_threshold: float = 0.28,
    max_clusters: int = 12,
) -> list[PatternCluster]:
    """Cluster *signals* into thematic groups and rank by velocity.

    Args:
        signals: List of ``ProductSignal`` objects or dicts with a ``title`` key.
        min_cluster_size: Clusters smaller than this are discarded.
        similarity_threshold: Cosine similarity in [0, 1] above which two
            signals are considered thematically related. Lower values produce
            larger, broader clusters; higher values produce tight, specific ones.
            0.28 is a good default for news headlines.
        max_clusters: Return at most this many clusters (ranked by size).

    Returns:
        List of ``PatternCluster`` sorted by ``signal_count`` descending.
    """
    if not signals:
        return []

    titles = [_title(s) for s in signals]
    token_lists = [_tokenize(t) for t in titles]

    # Drop signals with empty titles — they contribute nothing to clustering
    valid_indices = [i for i, tl in enumerate(token_lists) if tl]
    if not valid_indices:
        return []

    valid_tokens = [token_lists[i] for i in valid_indices]
    raw_vectors = _build_tfidf(valid_tokens)

    # Phase 5: PCA denoising removes noise dimensions from the TF-IDF space
    # before clustering, producing tighter, more semantically coherent clusters.
    # Falls back to raw vectors when numpy is absent or corpus is too small.
    vectors = pca_denoise_vectors(raw_vectors)

    raw_clusters = _greedy_cluster(vectors, similarity_threshold)

    now = datetime.now(UTC)
    six_hours_ago = now.timestamp() - 6 * 3600

    result: list[PatternCluster] = []
    for cluster_member_offsets in raw_clusters:
        if len(cluster_member_offsets) < min_cluster_size:
            continue

        # Map back to original signal indices
        orig_indices = [valid_indices[off] for off in cluster_member_offsets]
        cluster_signals = [signals[i] for i in orig_indices]

        # Aggregate cluster TF-IDF: sum vectors of all members (unigrams only for labels)
        agg: dict[str, float] = {}
        for off in cluster_member_offsets:
            for term, weight in vectors[off].items():
                if "_" not in term:
                    agg[term] = agg.get(term, 0.0) + weight

        top_terms = sorted(agg, key=agg.__getitem__, reverse=True)[:8]
        label = " ".join(top_terms[:4]).title() if top_terms else "Unknown"

        # Platform diversity
        platforms = sorted({_platform(s) for s in cluster_signals})

        # Top titles (longest, up to 5)
        top_titles = sorted(
            (_title(s) for s in cluster_signals if _title(s)),
            key=len,
            reverse=True,
        )[:5]

        # Recency weight: fraction of signals posted in last 6 hours
        recent_count = 0
        for s in cluster_signals:
            ts = _posted_at(s)
            if ts is not None:
                ts_naive = ts.timestamp() if hasattr(ts, "timestamp") else 0.0
                if ts_naive >= six_hours_ago:
                    recent_count += 1
        recency = recent_count / len(cluster_signals) if cluster_signals else 0.0

        # Phase 5: OLS velocity regression for this cluster's arrival time-series.
        vel_reg = compute_velocity_slope(cluster_signals)

        result.append(
            PatternCluster(
                label=label,
                signal_count=len(cluster_signals),
                platforms=platforms,
                top_titles=top_titles,
                representative_terms=top_terms,
                velocity_score=0.0,  # filled in below
                recency_weight=round(recency, 3),
                velocity_slope=vel_reg.slope,
                r_squared=vel_reg.r_squared,
                is_high_priority=vel_reg.is_high_priority,
                member_indices=orig_indices,
            )
        )

    if not result:
        return []

    # Rank by composite velocity score:
    #   base   = signal_count / max_count  (relative size)
    #   recency_boost = up to 30% for fully-recent clusters
    #   slope_boost   = up to 20% for positive OLS slope (Phase 5)
    #                   only applied when R² > 0.3 (reliable fit)
    # High-priority clusters (is_high_priority=True) always sort first.
    max_count = max(c.signal_count for c in result)
    max_slope = max((c.velocity_slope for c in result if c.r_squared > 0.3), default=1.0)
    max_slope = max(max_slope, 1e-6)  # avoid division by zero

    for c in result:
        raw_vel = c.signal_count / max_count
        recency_boost = 1.0 + 0.30 * c.recency_weight
        slope_boost = (
            1.0 + 0.20 * min(1.0, c.velocity_slope / max_slope)
            if c.r_squared > 0.3 and c.velocity_slope > 0
            else 1.0
        )
        c.velocity_score = round(min(1.0, raw_vel * recency_boost * slope_boost), 3)

    # is_high_priority clusters float to the top regardless of size
    result.sort(key=lambda c: (c.is_high_priority, c.velocity_score), reverse=True)
    return result[:max_clusters]


__all__ = ["PatternCluster", "detect_patterns"]
