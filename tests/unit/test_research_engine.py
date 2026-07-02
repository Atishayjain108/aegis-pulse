"""
PASS6-6B: unit tests for the multi-pass ResearchEngine.

The harvest pass (``scrape_topic``) is stubbed so tests run with no network
and no DB — they exercise the synthesis logic (cross-verify, confidence,
verdict, findings) and the depth-tier behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from aegis.intelligence.research_engine import (
    ResearchEngine,
    ResearchFinding,
    ResearchReport,
)


@dataclass
class _FakeHarvest:
    signals: list


def _signals(*titles_platforms: tuple[str, str]) -> list[dict]:
    return [
        {"title": t, "platform": p, "confidence": 0.7, "url": None}
        for t, p in titles_platforms
    ]


@pytest.fixture
def stub_scrape(monkeypatch):
    """Patch scrape_topic to return a fixed signal set; returns a setter."""

    holder: dict = {"signals": []}

    async def _fake_scrape_topic(query, **kwargs):
        return _FakeHarvest(signals=holder["signals"])

    import aegis.scrape.topic as topic_mod

    monkeypatch.setattr(topic_mod, "scrape_topic", _fake_scrape_topic)

    def _set(signals: list) -> None:
        holder["signals"] = signals

    return _set


class TestResearchSynthesis:
    async def test_empty_signals_low_confidence(self, stub_scrape) -> None:
        stub_scrape([])
        engine = ResearchEngine()
        report = await engine.research("nothing here", depth="surface")
        assert isinstance(report, ResearchReport)
        assert report.signal_count == 0
        assert report.confidence_score == 0.0
        assert report.trend_verdict == "stable"
        assert report.recommended_actions  # always at least one action

    async def test_cross_verified_themes_detected(self, stub_scrape) -> None:
        # "wireless earbuds" appears on 2 platforms → cross-verified.
        stub_scrape(
            _signals(
                ("Best wireless earbuds 2026", "reddit"),
                ("New wireless earbuds launch", "hacker_news"),
                ("Random unrelated headline", "techcrunch"),
            )
        )
        engine = ResearchEngine()
        report = await engine.research("earbuds", depth="standard")
        # "wireless" and "earbuds" both appear on >=2 platforms and are >5 chars
        assert any("earbuds" in t for t in report.cross_verified_findings)
        assert report.signal_count == 3
        assert len(report.sources_consulted) == 3

    async def test_findings_marked_verified(self, stub_scrape) -> None:
        stub_scrape(
            _signals(
                ("wireless earbuds review", "reddit"),
                ("wireless earbuds deal", "amazon"),
            )
        )
        engine = ResearchEngine()
        report = await engine.research("earbuds", depth="standard")
        verified = [f for f in report.key_findings if f.verified_by]
        assert verified  # at least one finding cross-confirmed

    async def test_surface_depth_skips_risk_synthesis(self, stub_scrape) -> None:
        stub_scrape(_signals(("widget one", "reddit"), ("widget two", "hacker_news")))
        engine = ResearchEngine()
        report = await engine.research("widget", depth="surface")
        assert report.risks == []
        assert report.opportunities == []
        assert report.research_depth == "surface"

    async def test_deep_depth_runs_opportunities(self, stub_scrape) -> None:
        # >50 signals across 3+ platforms triggers both opportunity heuristics.
        many = []
        for i in range(60):
            plat = ["reddit", "hacker_news", "techcrunch"][i % 3]
            many.append({"title": f"trend item {i}", "platform": plat, "confidence": 0.8})
        stub_scrape(many)
        engine = ResearchEngine()
        report = await engine.research("trend", depth="deep")
        assert report.trend_verdict in ("emerging", "stable")
        assert any("market interest" in o for o in report.opportunities)
        assert any("Multi-platform" in o for o in report.opportunities)

    async def test_report_to_dict_is_json_safe(self, stub_scrape) -> None:
        import json

        stub_scrape(_signals(("alpha beta gamma", "reddit"), ("alpha beta delta", "hn")))
        engine = ResearchEngine()
        report = await engine.research("alpha", depth="standard")
        payload = report.to_dict()
        # Round-trips through JSON cleanly.
        encoded = json.dumps(payload)
        assert "key_findings" in json.loads(encoded)
        assert isinstance(payload["key_findings"], list)


class TestSignalNormalization:
    async def test_accepts_productsignal_like_objects(self, stub_scrape) -> None:
        class _PlatformEnum:
            value = "reddit"

        class _Sig:
            platform = _PlatformEnum()
            title = "objectified signal"
            url = None
            source_confidence = 0.66

        stub_scrape([_Sig(), _Sig()])
        engine = ResearchEngine()
        report = await engine.research("obj", depth="surface")
        assert report.signal_count == 2
        assert report.sources_consulted == ["reddit"]


class TestResearchFinding:
    def test_finding_to_dict(self) -> None:
        f = ResearchFinding(claim="x", source="reddit", confidence=0.5, verified_by=["x"])
        d = f.to_dict()
        assert d["claim"] == "x"
        assert d["verified_by"] == ["x"]
        assert "timestamp" in d
