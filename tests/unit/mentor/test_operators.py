"""Tests for the M2 operator fleet (Research / Explore / Knowledge)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from aegis.mentor.operators import (
    ExploreOp,
    KnowledgeOp,
    OperatorContext,
    OperatorResult,
    ResearchOp,
)
from aegis.mentor.schemas import Intent, UserProfile
from aegis.mentor.sector import SectorRouter


def _profile(**kw) -> UserProfile:
    base = {"sector": "d2c_india", "sector_raw": "wireless earbuds", "intent": Intent.INCOME}
    base.update(kw)
    return UserProfile(**base)


# --- Fake research engine -------------------------------------------------


@dataclass
class _FakeReport:
    query: str
    topic_type: str = "ecommerce"
    executive_summary: str = "Solid demand for the query."
    cross_verified_findings: list[str] = field(default_factory=lambda: ["theme A", "theme B"])
    unverified_claims: list[str] = field(default_factory=lambda: ["single-source X"])
    recommended_actions: list[str] = field(default_factory=lambda: ["source it", "list it"])
    sources_consulted: list[str] = field(default_factory=lambda: ["amazon_in", "google_news"])
    confidence_score: float = 0.72
    trend_verdict: str = "emerging"
    signal_count: int = 40
    opportunities: list[str] = field(default_factory=lambda: ["arbitrage gap"])
    risks: list[str] = field(default_factory=list)
    research_depth: str = "standard"


class _FakeEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def research(self, query: str, *, depth: str = "standard"):
        self.calls.append((query, depth))
        return _FakeReport(query=query, research_depth=depth)


class _BoomEngine:
    async def research(self, query: str, *, depth: str = "standard"):
        raise RuntimeError("harvest down")


class TestResearchOp:
    @pytest.mark.asyncio
    async def test_research_grounded_result(self) -> None:
        engine = _FakeEngine()
        op = ResearchOp(engine=engine)
        ctx = OperatorContext(depth="deep")
        res = await op.run(_profile(), "wireless earbuds", ctx)
        assert isinstance(res, OperatorResult)
        assert res.operator == "research"
        assert "theme A" in res.findings
        assert any(f.startswith("(unverified)") for f in res.findings)
        assert res.sources == ["amazon_in", "google_news"]
        assert res.confidence == 0.72
        assert res.data["trend_verdict"] == "emerging"
        assert engine.calls == [("wireless earbuds", "deep")]

    @pytest.mark.asyncio
    async def test_research_failure_is_graceful(self) -> None:
        op = ResearchOp(engine=_BoomEngine())
        res = await op.run(_profile(), "x", OperatorContext())
        assert res.confidence == 0.0
        assert not res.has_signal

    @pytest.mark.asyncio
    async def test_research_empty_query(self) -> None:
        op = ResearchOp(engine=_FakeEngine())
        res = await op.run(_profile(sector_raw="", sector=""), "", OperatorContext())
        assert res.confidence == 0.0


# --- Fake sentinel --------------------------------------------------------


@dataclass
class _FakeScan:
    radar_signals: int = 80
    breakouts: int = 2
    reports: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"label": "portable neck fans", "verdict": "emerging",
             "sources_consulted": ["gdelt", "google_trends_global"]},
        ]
    )


class _FakeSentinel:
    async def scan(self):
        return _FakeScan()


class _EmptySentinel:
    async def scan(self):
        return _FakeScan(radar_signals=0, breakouts=0, reports=[])


class TestExploreOp:
    @pytest.mark.asyncio
    async def test_explore_surfaces_niches(self) -> None:
        op = ExploreOp(sentinel=_FakeSentinel())
        res = await op.run(_profile(), "discover new markets", OperatorContext())
        assert res.operator == "explore"
        assert any("portable neck fans" in f for f in res.findings)
        assert "gdelt" in res.sources
        assert res.confidence > 0
        assert res.has_signal

    @pytest.mark.asyncio
    async def test_explore_empty_scan(self) -> None:
        op = ExploreOp(sentinel=_EmptySentinel())
        res = await op.run(_profile(), "discover", OperatorContext())
        assert res.findings == []
        assert res.confidence == 0.0


class TestKnowledgeOp:
    @pytest.mark.asyncio
    async def test_knowledge_from_deep_pack(self) -> None:
        router = SectorRouter()
        ctx = OperatorContext(sector_pack=router.route("d2c_india"))
        res = await KnowledgeOp().run(_profile(), "what are the rules", ctx)
        assert res.operator == "knowledge"
        assert any("Regulation:" in f for f in res.findings)
        assert any("Common failure:" in f for f in res.findings)
        assert "sector_pack:d2c_india" in res.sources
        assert res.confidence > 0.5
        assert res.data["deep_sector"] is True

    @pytest.mark.asyncio
    async def test_knowledge_recalls_from_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeOppMem:
            def __init__(self, pool, *a, **k) -> None:
                pass

            async def patterns(self, *, days_back=90, limit=5):
                return [{"pattern": "low-CAC reselling wins"}]

        monkeypatch.setattr("aegis.memory.opportunity.OpportunityMemory", _FakeOppMem)
        router = SectorRouter()
        ctx = OperatorContext(pool=object(), sector_pack=router.route("d2c_india"))
        res = await KnowledgeOp().run(_profile(), "ground reality", ctx)
        assert any("low-CAC reselling wins" in f for f in res.findings)
        assert "aegis.memory:opportunity" in res.sources

    @pytest.mark.asyncio
    async def test_knowledge_baseline_pack_is_general(self) -> None:
        router = SectorRouter()
        ctx = OperatorContext(sector_pack=router.route("undecided"))
        res = await KnowledgeOp().run(
            _profile(sector="undecided"), "explain", ctx
        )
        # Baseline pack exposes no fabricated sector facts → general, low confidence.
        assert res.data["deep_sector"] is False
        assert res.confidence < 0.55
