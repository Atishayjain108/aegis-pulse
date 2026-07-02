"""Tests for the MentorAgent orchestrator (M2)."""

from __future__ import annotations

import pytest

from aegis.mentor.config import MentorSettings
from aegis.mentor.mentor_agent import MentorAgent
from aegis.mentor.operators.base import OperatorContext, OperatorResult
from aegis.mentor.schemas import AutonomyPreference, Intent, UserProfile


def _profile(**kw) -> UserProfile:
    base = {"sector": "d2c_india", "sector_raw": "wireless earbuds", "intent": Intent.INCOME}
    base.update(kw)
    return UserProfile(**base)


class _StubOp:
    """Records that it ran and returns a fixed grounded result."""

    def __init__(self, name: str, *, confidence: float = 0.6, findings=None) -> None:
        self.name = name
        self._confidence = confidence
        self._findings = findings if findings is not None else [f"{name} finding"]
        self.ran = False
        self.depth_seen: str | None = None

    async def run(self, profile, request, context: OperatorContext) -> OperatorResult:
        self.ran = True
        self.depth_seen = context.depth
        return OperatorResult(
            operator=self.name,
            findings=list(self._findings),
            actions=[f"{self.name} step 1", f"{self.name} step 2"],
            sources=[f"src_{self.name}"],
            reasoning=f"{self.name} reasoning",
            confidence=self._confidence,
        )


class _SilentOp:
    """Returns nothing grounded — exercises grounded-or-silent."""

    name = "research"

    async def run(self, profile, request, context) -> OperatorResult:
        return OperatorResult(operator=self.name, confidence=0.0)


def _agent(ops: dict, *, llm: bool = False) -> MentorAgent:
    return MentorAgent(
        settings=MentorSettings(llm_enrichment_enabled=llm),
        operators=ops,
    )


class TestSynthesis:
    @pytest.mark.asyncio
    async def test_aggregates_grounded_results(self) -> None:
        ops = {"research": _StubOp("research", confidence=0.8),
               "knowledge": _StubOp("knowledge", confidence=0.4)}
        counsel = await _agent(ops).advise(_profile(), "research the market and explain the basics")
        assert "research finding" in counsel.claims
        assert "knowledge finding" in counsel.claims
        assert counsel.confidence == 0.6  # mean of 0.8 and 0.4
        assert "src_research" in counsel.sources
        assert counsel.next_steps
        assert counsel.altitude == AutonomyPreference.COACH_ME

    @pytest.mark.asyncio
    async def test_grounded_or_silent_when_nothing_found(self) -> None:
        counsel = await _agent({"research": _SilentOp()}).advise(_profile(), "research x")
        assert counsel.claims == []
        assert counsel.confidence == 0.0
        assert "couldn't ground" in counsel.summary.lower()


class TestAltitudeRouting:
    @pytest.mark.asyncio
    async def test_guide_me_runs_full_fleet_and_deep(self) -> None:
        ops = {"research": _StubOp("research"), "knowledge": _StubOp("knowledge"),
               "explore": _StubOp("explore")}
        agent = _agent(ops)
        await agent.advise(_profile(autonomy_preference=AutonomyPreference.GUIDE_ME), "help me")
        assert all(op.ran for op in ops.values())  # full fleet
        assert ops["research"].depth_seen == "deep"

    @pytest.mark.asyncio
    async def test_answer_me_stays_lean(self) -> None:
        ops = {"research": _StubOp("research"), "knowledge": _StubOp("knowledge"),
               "explore": _StubOp("explore")}
        agent = _agent(ops)
        await agent.advise(
            _profile(autonomy_preference=AutonomyPreference.ANSWER_ME),
            "analyze this market",  # no explore/knowledge keywords
        )
        assert ops["research"].ran
        assert not ops["explore"].ran  # brief-me drops exploration
        assert ops["research"].depth_seen == "deep"

    @pytest.mark.asyncio
    async def test_explore_keyword_routes_to_explore(self) -> None:
        ops = {"research": _StubOp("research"), "explore": _StubOp("explore")}
        await _agent(ops).advise(_profile(), "discover new niches for me")
        assert ops["explore"].ran

    @pytest.mark.asyncio
    async def test_answer_me_caps_steps_at_three(self) -> None:
        # Three stub ops × 2 steps each = 6 candidate steps; answer_me caps at 3.
        ops = {"research": _StubOp("research")}
        counsel = await _agent(ops).advise(
            _profile(autonomy_preference=AutonomyPreference.ANSWER_ME), "market analysis"
        )
        assert len(counsel.next_steps) <= 3


class TestLLMEnrichment:
    @pytest.mark.asyncio
    async def test_llm_rewrites_only_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_complete(agent, messages, **kwargs):
            return "Sharp grounded verdict."

        monkeypatch.setattr(
            "aegis.llm.bridge.agents_bridge.complete_for_agent", fake_complete
        )
        ops = {"research": _StubOp("research", confidence=0.7)}
        counsel = await _agent(ops, llm=True).advise(_profile(), "market analysis")
        assert counsel.summary == "Sharp grounded verdict."
        # Claims/confidence untouched by the LLM.
        assert counsel.claims == ["research finding"]
        assert counsel.confidence == 0.7

    @pytest.mark.asyncio
    async def test_llm_failure_keeps_deterministic_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def boom(agent, messages, **kwargs):
            raise RuntimeError("down")

        monkeypatch.setattr(
            "aegis.llm.bridge.agents_bridge.complete_for_agent", boom
        )
        ops = {"research": _StubOp("research")}
        counsel = await _agent(ops, llm=True).advise(_profile(), "market analysis")
        assert "grounded" in counsel.summary.lower()

    @pytest.mark.asyncio
    async def test_llm_not_called_when_silent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called = {"n": 0}

        async def fake_complete(agent, messages, **kwargs):
            called["n"] += 1
            return "should not appear"

        monkeypatch.setattr(
            "aegis.llm.bridge.agents_bridge.complete_for_agent", fake_complete
        )
        counsel = await _agent({"research": _SilentOp()}, llm=True).advise(_profile(), "research x")
        assert called["n"] == 0  # no claims → no LLM call
        assert "couldn't ground" in counsel.summary.lower()
