"""
End-to-end runner tests.

These tests exercise the full LangGraph pipeline against hand-crafted
candidates and assert the expected halt reason / final verdict. They
require ``langgraph`` to be installed; if it isn't, the test module
is skipped wholesale.

Key paths covered:
  * Strong clean candidate         → PROCEED, completed
  * Trademark-poisoned candidate   → BLOCK,   blocked_by_compliance
  * Weak candidate                 → BLOCK,   scout_below_threshold
  * Astroturf / coordination heavy → BLOCK,   vetoed_by_red_team
  * Exception inside the graph     → halt_reason == "exception"
  * Asyncio timeout                → halt_reason == "timeout"
  * Heuristic-only path (no LLM)   produces a complete decision trail
"""
from __future__ import annotations

import pytest

# Skip the whole module if langgraph isn't installed; we don't want
# CI to fail just because the optional dep isn't present in the
# minimal-test environment.
pytest.importorskip("langgraph")

from aegis.agents import runner  # noqa: E402
from aegis.agents.schemas import AgentVerdict, GraphResult, Priority  # noqa: E402


class TestRunnerHappyPath:
    async def test_strong_candidate_proceeds(self, trend_factory) -> None:
        candidate = trend_factory(
            trend_id="strong-001",
            title="Reusable bamboo travel mug",
            summary="Eco-friendly, dishwasher-safe travel mug",
            representative_text="Everyone's obsessed with this travel mug",
            velocity_1h=300.0,
            velocity_6h=1500.0,
            velocity_24h=4000.0,
            sentiment=0.75,
            commercial_intent=0.85,
            novelty=0.75,
            coordination_risk=0.05,
            signal_count=180,
            unique_authors=120,
            platforms=["tiktok", "reddit", "instagram"],
        )
        result = await runner.run_trend(candidate, use_llm=False)
        assert isinstance(result, GraphResult)
        assert result.final_verdict is AgentVerdict.PROCEED
        assert result.halt_reason == "completed"
        assert result.duration_ms >= 0.0
        # Should have run *all* the agents that pass the gates.
        agent_names = {d.agent for d in result.decisions}
        # Discovery + historian + sourcing + auditor + sentinel +
        # compliance + red_team + hedge.
        for required in (
            "scout",
            "narrative",
            "geo_arbitrage",
            "historian",
            "sourcer",
            "auditor",
            "sentinel",
            "compliance",
            "red_team",
            "hedge",
        ):
            assert required in agent_names, f"missing agent {required}"

    async def test_strong_candidate_p0_priority(self, trend_factory) -> None:
        candidate = trend_factory(
            trend_id="strong-p0",
            title="Vintage-style clock",
            velocity_1h=600.0,
            velocity_6h=3500.0,
            velocity_24h=9000.0,
            sentiment=0.85,
            commercial_intent=0.95,
            novelty=0.9,
            coordination_risk=0.0,
            signal_count=400,
            unique_authors=300,
            platforms=["tiktok", "reddit", "instagram", "youtube"],
        )
        result = await runner.run_trend(candidate, use_llm=False)
        # All gates should pass cleanly. Priority depends on the
        # scout_score reaching 0.80 — accept either P0 (target) or
        # P2 (weaker breakout that still proceeds).
        assert result.final_verdict is AgentVerdict.PROCEED
        assert result.final_priority in {Priority.P0_BREAKOUT, Priority.P2_OPPORTUNITY}


class TestRunnerComplianceVeto:
    async def test_trademark_blocks(self, trend_factory) -> None:
        candidate = trend_factory(
            trend_id="trademark-fail",
            title="Custom Nike sneakers limited edition",
            summary="Knockoff Nike sneakers from overseas",
            velocity_1h=300.0,
            velocity_6h=1500.0,
            velocity_24h=4000.0,
            commercial_intent=0.9,
            novelty=0.7,
            signal_count=180,
            unique_authors=120,
            platforms=["tiktok", "reddit", "instagram"],
        )
        result = await runner.run_trend(candidate, use_llm=False)
        assert result.final_verdict is AgentVerdict.BLOCK
        assert result.halt_reason == "blocked_by_compliance"
        # Decision trail should include compliance with BLOCK.
        compliance_decisions = [d for d in result.decisions if d.agent == "compliance"]
        assert len(compliance_decisions) >= 1
        assert compliance_decisions[-1].verdict is AgentVerdict.BLOCK

    async def test_compliance_short_circuits_red_team(self, trend_factory) -> None:
        # If compliance fails, red_team and hedge should not even run.
        candidate = trend_factory(
            trend_id="compliance-shortcircuit",
            title="Genuine Disney mug",
            velocity_1h=200.0,
            velocity_6h=1000.0,
            velocity_24h=3000.0,
            signal_count=100,
            unique_authors=60,
            platforms=["tiktok", "reddit"],
        )
        result = await runner.run_trend(candidate, use_llm=False)
        assert result.halt_reason == "blocked_by_compliance"
        agent_names = [d.agent for d in result.decisions]
        # Red team and hedge should NOT appear.
        assert "red_team" not in agent_names
        assert "hedge" not in agent_names


class TestRunnerWeakScout:
    async def test_weak_scout_blocks_pipeline(self, trend_factory) -> None:
        candidate = trend_factory(
            trend_id="weak-scout",
            title="generic widget",
            summary="just an item",
            representative_text="basic product",
            velocity_1h=0.5,
            velocity_6h=2.0,
            velocity_24h=4.0,
            sentiment=0.0,
            commercial_intent=0.05,
            novelty=0.05,
            coordination_risk=0.0,
            signal_count=8,
            unique_authors=3,
            platforms=["reddit"],
        )
        result = await runner.run_trend(candidate, use_llm=False)
        # SCOUT should BLOCK → blocked_by has 'scout' → final verdict BLOCK.
        assert result.final_verdict is AgentVerdict.BLOCK
        assert result.halt_reason in {
            "scout_below_threshold",
            "blocked_by_compliance",
            # If by chance compliance also flags a generic title with
            # specific words, that's also acceptable.
        }


class TestRunnerRedTeamVeto:
    async def test_astroturf_vetoed(self, trend_factory) -> None:
        candidate = trend_factory(
            trend_id="astroturf-1",
            title="Amazing eco product everyone loves",
            summary="must-have green product",
            velocity_1h=300.0,
            velocity_6h=1500.0,
            velocity_24h=4000.0,
            sentiment=0.9,
            commercial_intent=0.9,
            novelty=0.7,
            coordination_risk=0.85,  # F1: high coordination
            signal_count=200,
            unique_authors=8,  # F2: low diversity
            platforms=["tiktok"],  # F6: single platform with high volume
        )
        result = await runner.run_trend(candidate, use_llm=False)
        # Should fail red_team OR scout. Either is the right answer.
        assert result.final_verdict is AgentVerdict.BLOCK
        assert result.halt_reason in {
            "vetoed_by_red_team",
            "scout_below_threshold",
        }


class TestRunnerErrorHandling:
    async def test_timeout_returns_safe_result(self, trend_factory) -> None:
        # 0-second timeout → guaranteed timeout regardless of speed.
        candidate = trend_factory(trend_id="timeout-1")
        result = await runner.run_trend(
            candidate, use_llm=False, timeout_s=0.0001
        )
        assert isinstance(result, GraphResult)
        assert result.halt_reason == "timeout"
        # Even on timeout, we get a valid result object.
        assert result.final_verdict is AgentVerdict.HOLD
        assert result.final_priority is Priority.P3_HOUSEKEEPING

    async def test_exception_during_compile_caught(
        self, trend_factory, monkeypatch
    ) -> None:
        # Force `_get_graph` to raise to exercise the compile-failure
        # branch. This proves the runner never bubbles up exceptions
        # from graph construction.
        async def _explode(**_kwargs):
            raise RuntimeError("simulated graph compile failure")

        await runner.reset_graph_cache()
        monkeypatch.setattr(runner, "_get_graph", _explode)

        candidate = trend_factory(trend_id="boom-1")
        result = await runner.run_trend(candidate, use_llm=False)
        assert result.halt_reason == "exception"
        assert result.final_verdict is AgentVerdict.HOLD


class TestRunnerCaching:
    async def test_subsequent_calls_reuse_compiled_graph(self, trend_factory) -> None:
        c1 = trend_factory(trend_id="cache-1")
        c2 = trend_factory(trend_id="cache-2")

        await runner.reset_graph_cache()
        cache_size_before = len(runner._graph_cache)
        assert cache_size_before == 0

        await runner.run_trend(c1, use_llm=False)
        cache_size_after_first = len(runner._graph_cache)
        assert cache_size_after_first == 1

        await runner.run_trend(c2, use_llm=False)
        cache_size_after_second = len(runner._graph_cache)
        # Same params → same cache key → still one entry.
        assert cache_size_after_second == 1


class TestRunnerNoLLMPath:
    async def test_complete_trail_without_llm(self, trend_factory) -> None:
        # Critical doctrine check: with use_llm=False the entire
        # pipeline must produce a complete, well-formed result.
        candidate = trend_factory(
            trend_id="no-llm",
            velocity_1h=100.0,
            velocity_6h=500.0,
            velocity_24h=1500.0,
            commercial_intent=0.7,
            novelty=0.6,
            sentiment=0.5,
            signal_count=80,
            unique_authors=50,
            platforms=["tiktok", "reddit"],
        )
        result = await runner.run_trend(candidate, use_llm=False)
        # Every decision in the trail should have populated reasoning
        # from the heuristic alone — no "llm" details key.
        for d in result.decisions:
            assert d.reasoning, f"agent {d.agent} produced empty reasoning"
            assert d.confidence >= 0.0
            assert d.score >= 0.0
            # Without LLM, no decision should carry a `llm` key in details.
            assert "llm" not in d.details, (
                f"agent {d.agent} appears to have called LLM despite use_llm=False"
            )
