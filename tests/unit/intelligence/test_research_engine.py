"""Pass 10 — ResearchEngine 5-pass methodology + cross-verification.

Covers the pure synthesis helpers (velocity, cross-verify, confidence,
verdict) and the full research() flow with a mocked harvest so no scrape
infra is required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aegis.intelligence.research_engine import ResearchEngine, ResearchReport


def _signals(n: int = 4) -> list[dict]:
    return [
        {"title": "Wireless earbuds battery breakthrough announced",
         "platform": "reddit", "confidence": 0.8},
        {"title": "New wireless earbuds battery tech goes viral",
         "platform": "hacker_news", "confidence": 0.7},
        {"title": "Earbuds market growth accelerates",
         "platform": "google_news", "confidence": 0.6},
        {"title": "Cheap earbuds flood marketplace",
         "platform": "amazon", "confidence": 0.5},
    ][:n]


def test_cross_verify_finds_multi_platform_themes() -> None:
    """Happy path: 'earbuds' appears across 2+ platforms → cross-verified."""
    eng = ResearchEngine()
    themes = eng._cross_verify(_signals())
    assert "earbuds" in themes


def test_cross_verify_empty() -> None:
    """Edge case: no signals → no themes, no error."""
    assert ResearchEngine()._cross_verify([]) == []


def test_analyze_velocity_no_data() -> None:
    """Failure path: empty signal set reports no_data status."""
    v = ResearchEngine()._analyze_velocity([])
    assert v["status"] == "no_data"
    assert v["total"] == 0


def test_confidence_in_range() -> None:
    """Invariant: composite confidence is always within [0, 1]."""
    eng = ResearchEngine()
    sigs = _signals()
    c = eng._compute_confidence(sigs, eng._cross_verify(sigs))
    assert 0.0 <= c <= 1.0


def test_trend_verdict_thresholds() -> None:
    eng = ResearchEngine()
    assert eng._determine_trend_verdict({"total": 0}) == "stable"
    assert eng._determine_trend_verdict({"total": 150}) == "emerging"
    assert eng._determine_trend_verdict({"total": 10}) == "surface_only"


async def test_research_end_to_end_mocked_harvest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full research() returns a structured ResearchReport (harvest mocked)."""
    eng = ResearchEngine()
    monkeypatch.setattr(eng, "_harvest", AsyncMock(return_value=_signals()))
    report = await eng.research("wireless earbuds", depth="standard")
    assert isinstance(report, ResearchReport)
    assert report.query == "wireless earbuds"
    assert report.signal_count == 4
    assert "earbuds" in report.cross_verified_findings
    assert report.research_depth == "standard"
    assert 0.0 <= report.confidence_score <= 1.0


async def test_research_surface_no_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Edge case: an empty harvest yields a valid report with zero signals."""
    eng = ResearchEngine()
    monkeypatch.setattr(eng, "_harvest", AsyncMock(return_value=[]))
    report = await eng.research("obscure topic", depth="surface")
    assert report.signal_count == 0
    assert report.trend_verdict == "stable"
    assert report.to_dict()["signal_count"] == 0
