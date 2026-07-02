"""PASS11 — tests for the real-time PatternEngine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aegis.scrape.pattern_engine import PatternEngine, RealTimePattern


def _make_signals(n: int, *, theme: str, platforms: list[str]) -> list[dict]:
    now = datetime.now(UTC)
    sigs: list[dict] = []
    for i in range(n):
        sigs.append(
            {
                "title": f"{theme} review breakdown analysis {i}",
                "platform": platforms[i % len(platforms)],
                "created_at": now - timedelta(hours=n - i),
            }
        )
    return sigs


def test_empty_signals_returns_empty() -> None:
    assert PatternEngine().detect([]) == []


def test_detects_themed_cluster() -> None:
    sigs = _make_signals(10, theme="wireless earbuds", platforms=["reddit", "hn"])
    patterns = PatternEngine(min_cluster_size=2).detect(sigs)
    assert patterns, "expected at least one pattern"
    assert isinstance(patterns[0], RealTimePattern)
    # label should be drawn from the shared theme terms
    assert any(term in patterns[0].label for term in ("wireless", "earbuds", "review"))


def test_min_cluster_size_filters_small_clusters() -> None:
    sigs = _make_signals(2, theme="single", platforms=["reddit"])
    # min_cluster_size 5 should discard the 2-signal cluster
    assert PatternEngine(min_cluster_size=5).detect(sigs) == []


def test_patterns_sorted_by_confidence_desc() -> None:
    sigs = _make_signals(12, theme="solar panel", platforms=["reddit", "hn", "devto"])
    patterns = PatternEngine(min_cluster_size=2).detect(sigs)
    confidences = [p.confidence for p in patterns]
    assert confidences == sorted(confidences, reverse=True)


def test_confidence_and_coherence_bounded() -> None:
    sigs = _make_signals(8, theme="electric scooter", platforms=["reddit", "hn"])
    for p in PatternEngine(min_cluster_size=2).detect(sigs):
        assert 0.0 <= p.confidence <= 1.0
        assert 0.0 <= p.coherence <= 1.0


def test_coerces_object_signals() -> None:
    class _Sig:
        def __init__(self, title: str, platform: str) -> None:
            self.title = title
            self.platform = platform
            self.created_at = datetime.now(UTC)

    objs = [_Sig(f"crypto wallet ledger news {i}", "reddit") for i in range(6)]
    patterns = PatternEngine(min_cluster_size=2).detect(objs)  # type: ignore[arg-type]
    assert patterns
    assert patterns[0].signal_count >= 2


@pytest.mark.parametrize(
    ("slope", "accel", "expected"),
    [
        (3.0, 2.0, "accelerating"),
        (1.5, 0.0, "emerging"),
        (0.5, -1.0, "peaking"),
        (-1.0, 0.0, "declining"),
        (0.0, 0.0, "noise"),
    ],
)
def test_classify_type(slope: float, accel: float, expected: str) -> None:
    assert PatternEngine()._classify_type(slope, accel) == expected
