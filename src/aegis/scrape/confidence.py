"""Scraped data confidence scoring — Phase 5 data quality gate.

Assigns a weighted confidence score [0, 1] to a batch of ProductSignals
across five structural dimensions. When the score drops below a configurable
threshold (default 0.85), the gate logs a structured warning with
per-dimension breakdown and machine-readable remediation hints that tell the
caller *exactly* what went wrong and how to recover.

Confidence formula (weights sum to 1.0)
----------------------------------------
  title_completeness   0.25  — fraction with title length ≥ 10 chars
  platform_diversity   0.20  — unique source platforms / 4  (4+ = 1.0)
  signal_freshness     0.20  — fraction posted within the last 24 hours
  volume_sufficiency   0.20  — min(signal_count / 10, 1.0)  (10+ = 1.0)
  author_diversity     0.15  — penalise when unique_authors / total < 0.10

Self-healing protocol
---------------------
If overall_score < threshold:
  1. A structured WARNING is logged (visible in the dashboard ops console).
  2. ``ConfidenceResult.remediation_hints`` lists actionable recovery steps.
  3. The *caller* (topic.py, runner.py) decides whether to re-route, retry
     with different queries, or proceed at reduced confidence.

This module is pure — no I/O, no side effects, no external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

_log = structlog.get_logger("aegis.scrape.confidence")

# Default threshold: below this, self-healing is triggered.
DEFAULT_CONFIDENCE_THRESHOLD: float = 0.85

# BRAIN-3: dynamic override pushed by async callers (topic.py / scheduler) from
# aegis.core.dynamic_thresholds. score_batch is a sync CPU function run via
# asyncio.to_thread (§12), so the adaptive value is injected rather than awaited
# here. None → fall back to DEFAULT_CONFIDENCE_THRESHOLD.
_DYNAMIC_THRESHOLD: float | None = None


def set_confidence_threshold(value: float | None) -> None:
    """Inject (or clear, with None) the adaptive confidence gate threshold."""
    global _DYNAMIC_THRESHOLD  # noqa: PLW0603 - cross-task injection point
    _DYNAMIC_THRESHOLD = value


def get_confidence_threshold() -> float:
    """Effective confidence gate: dynamic override or the static default."""
    return (
        _DYNAMIC_THRESHOLD
        if _DYNAMIC_THRESHOLD is not None
        else DEFAULT_CONFIDENCE_THRESHOLD
    )

# Sub-dimension score below this emits a remediation hint.
_DIM_WARN_THRESHOLD: float = 0.70

# Weights must sum to exactly 1.0.
_WEIGHTS: dict[str, float] = {
    "title_completeness": 0.25,
    "platform_diversity": 0.20,
    "signal_freshness": 0.20,
    "volume_sufficiency": 0.20,
    "author_diversity": 0.15,
}

# Human-readable remediation steps keyed by dimension name.
_REMEDIATION: dict[str, str] = {
    "title_completeness": (
        "LOW_TITLE_QUALITY: Titles are missing or too short. "
        "Consider falling back to LLM zero-shot title extraction."
    ),
    "platform_diversity": (
        "LOW_PLATFORM_DIVERSITY: Signals concentrated on too few platforms. "
        "Add more source adapters or broaden the query to cross-platform terms."
    ),
    "signal_freshness": (
        "STALE_DATA: Most signals are older than 24 hours. "
        "Increase lookback window, trigger a re-scrape, or check adapter health."
    ),
    "volume_sufficiency": (
        "INSUFFICIENT_VOLUME: Too few signals for reliable analysis. "
        "Raise per-source limit, add adapter sources, or expand the topic query."
    ),
    "author_diversity": (
        "LOW_AUTHOR_DIVERSITY: Author set is suspiciously concentrated (<10% unique). "
        "Suspected astroturf or bot activity — apply coordination_risk boost."
    ),
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfidenceResult:
    """Confidence assessment for a scraped signal batch."""

    overall_score: float
    """Weighted average in [0, 1]. Below *threshold* triggers self-healing."""

    dimensions: dict[str, float]
    """Per-dimension scores, each in [0, 1]."""

    remediation_hints: list[str]
    """Actionable recovery steps for every dimension below _DIM_WARN_THRESHOLD."""

    signal_count: int
    """Number of signals that were scored."""

    passed: bool
    """True when overall_score >= threshold."""

    threshold: float
    """The pass/fail boundary used for this evaluation."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_get(sig: Any, *attrs: str) -> Any:
    for attr in attrs:
        val = sig.get(attr) if isinstance(sig, dict) else getattr(sig, attr, None)
        if val is not None:
            return val
    return None


def _title_len(sig: Any) -> int:
    t = _safe_get(sig, "title") or ""
    return len(str(t).strip())


def _platform_str(sig: Any) -> str:
    p = _safe_get(sig, "platform") or "unknown"
    return p.value if hasattr(p, "value") else str(p)


def _posted_ts(sig: Any) -> float | None:
    if hasattr(sig, "posted_at") and sig.posted_at is not None:
        ts = sig.posted_at
        return ts.timestamp() if hasattr(ts, "timestamp") else None
    if hasattr(sig, "provenance"):
        sc = getattr(sig.provenance, "scraped_at", None)
        if sc is not None and hasattr(sc, "timestamp"):
            return sc.timestamp()
    if isinstance(sig, dict):
        for attr in ("captured_at", "posted_at", "scraped_at"):
            val = sig.get(attr)
            if val is not None and hasattr(val, "timestamp"):
                return val.timestamp()
    return None


def _author_diversity_score(signals: list[Any]) -> float:
    """Return a diversity score in [0, 1] based on the author-to-signal ratio.

    If no author metadata is available, returns 1.0 (benefit of the doubt).
    Signals below 0.10 unique-author ratio are heavily penalised — this is
    the astroturf threshold used by the SCOUT agent as well.
    """
    authors: list[str] = []
    for sig in signals:
        a = _safe_get(sig, "author", "author_id")
        if a is not None:
            authors.append(str(a))

    if not authors:
        return 1.0  # no metadata → cannot penalise

    ratio = len(set(authors)) / len(authors)
    if ratio >= 0.10:
        return 1.0
    # Scale 0..0.10 → 0..1 so a ratio of 0 → score 0, 0.10 → score 1.0
    return round(ratio / 0.10, 4)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_batch(
    signals: list[Any],
    *,
    threshold: float | None = None,
) -> ConfidenceResult:
    """Compute a confidence score for a scraped signal batch.

    Pure function — no external I/O. The caller decides whether to re-route,
    retry, or continue at reduced confidence.

    Args:
        signals:   List of ProductSignal objects or plain dicts.
        threshold: Pass/fail boundary. Default 0.85.

    Returns:
        ConfidenceResult with overall score, per-dimension breakdown, and
        remediation hints for any dimension below 0.70.
    """
    if threshold is None:
        threshold = get_confidence_threshold()
    n = len(signals)

    if n == 0:
        full_hints = list(_REMEDIATION.values())
        result = ConfidenceResult(
            overall_score=0.0,
            dimensions=dict.fromkeys(_WEIGHTS, 0.0),
            remediation_hints=full_hints,
            signal_count=0,
            passed=False,
            threshold=threshold,
        )
        _log.warning(
            "confidence.gate_failed",
            overall_score=0.0,
            threshold=threshold,
            signal_count=0,
            action="advisory_empty_batch",
            remediation_hints=full_hints,
            note="Batch is empty — all remediation hints apply",
        )
        return result

    now_ts = datetime.now(UTC).timestamp()
    cutoff_24h = now_ts - 24 * 3600.0

    # 1. Title completeness (fraction with title ≥ 10 chars)
    dim_title = sum(1 for s in signals if _title_len(s) >= 10) / n

    # 2. Platform diversity (4+ distinct platforms = 1.0)
    platforms = {_platform_str(s) for s in signals}
    dim_platform = min(1.0, len(platforms) / 4.0)

    # 3. Signal freshness (fraction posted in last 24 h; no-timestamp = fresh)
    fresh = sum(
        1
        for s in signals
        if (ts := _posted_ts(s)) is not None and ts >= cutoff_24h
    )
    no_ts = sum(1 for s in signals if _posted_ts(s) is None)
    dim_freshness = (fresh + no_ts) / n

    # 4. Volume sufficiency (10+ signals = 1.0)
    dim_volume = min(1.0, n / 10.0)

    # 5. Author diversity (penalise astroturf-level concentration)
    dim_authors = _author_diversity_score(signals)

    dimensions: dict[str, float] = {
        "title_completeness": round(dim_title, 4),
        "platform_diversity": round(dim_platform, 4),
        "signal_freshness": round(dim_freshness, 4),
        "volume_sufficiency": round(dim_volume, 4),
        "author_diversity": round(dim_authors, 4),
    }

    overall = round(
        min(1.0, max(0.0, sum(_WEIGHTS[k] * dimensions[k] for k in _WEIGHTS))),
        4,
    )

    hints = [
        _REMEDIATION[dim]
        for dim, score in dimensions.items()
        if score < _DIM_WARN_THRESHOLD
    ]

    passed = overall >= threshold

    result = ConfidenceResult(
        overall_score=overall,
        dimensions=dimensions,
        remediation_hints=hints,
        signal_count=n,
        passed=passed,
        threshold=threshold,
    )

    if not passed:
        stale_only = hints == ["STALE_DATA: Most signals are older than 24 hours. "
                               "Increase lookback window, trigger a re-scrape, or check adapter health."]
        _log.warning(
            "confidence.gate_failed",
            overall_score=overall,
            threshold=threshold,
            dimensions=dimensions,
            remediation_hints=hints,
            signal_count=n,
            action="advisory_stale_data" if stale_only else "advisory_gate_failed",
        )
    else:
        _log.debug(
            "confidence.gate_passed",
            overall_score=overall,
            threshold=threshold,
            signal_count=n,
        )

    return result


__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "ConfidenceResult",
    "get_confidence_threshold",
    "score_batch",
    "set_confidence_threshold",
]
