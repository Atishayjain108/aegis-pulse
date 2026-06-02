"""Tests for all 10 agents — heuristic-only path (no LLM)."""

from __future__ import annotations

import pytest

from aegis.agents.nodes import (
    AuditorAgent,
    ComplianceAgent,
    GeoArbitrageAgent,
    HedgeAgent,
    HistorianAgent,
    NarrativeAgent,
    RedTeamAgent,
    ScoutAgent,
    SentinelAgent,
    SourcerAgent,
)
from aegis.agents.schemas import AgentDecision, AgentVerdict
from aegis.agents.state import initial_state


class TestScoutAgent:
    async def test_strong_breakout_proceeds(self, trend_factory) -> None:
        agent = ScoutAgent(use_llm=False)
        candidate = trend_factory(
            velocity_1h=300.0,
            velocity_6h=1500.0,
            velocity_24h=4000.0,
            commercial_intent=0.9,
            novelty=0.8,
            sentiment=0.7,
            signal_count=200,
            unique_authors=120,
            platforms=["reddit", "tiktok", "instagram"],
            coordination_risk=0.0,
        )
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.PROCEED
        assert d.score >= 0.70
        assert partial["scout_score"] >= 0.70

    async def test_low_velocity_blocks(self, trend_factory) -> None:
        agent = ScoutAgent(use_llm=False)
        candidate = trend_factory(
            velocity_1h=0.5,
            velocity_6h=2.0,
            velocity_24h=4.0,
            commercial_intent=0.1,
            novelty=0.1,
            sentiment=0.0,
            signal_count=5,
            unique_authors=2,
            platforms=["reddit"],
        )
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.BLOCK
        assert "blocked_by" in partial
        assert "scout" in partial["blocked_by"]

    async def test_astroturf_penalty_applies(self, trend_factory) -> None:
        agent = ScoutAgent(use_llm=False)
        candidate = trend_factory(
            velocity_1h=50.0,
            velocity_6h=200.0,
            velocity_24h=400.0,
            signal_count=100,
            unique_authors=5,  # 5% diversity → astroturf
            commercial_intent=0.7,
            novelty=0.7,
        )
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.details["astroturf_penalty"] == 0.20

    async def test_coordination_penalty_applies(self, trend_factory) -> None:
        agent = ScoutAgent(use_llm=False)
        candidate = trend_factory(coordination_risk=0.9)
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.details["coordination_penalty"] > 0.0


class TestSourcerAgent:
    async def test_easy_category_proceeds(self, trend_factory) -> None:
        agent = SourcerAgent(use_llm=False)
        candidate = trend_factory(title="Funny enamel pin", summary="Cute enamel pins for jackets")
        state = initial_state(candidate)
        state["scout_score"] = 0.8  # type: ignore[typeddict-item]
        partial = await agent(state)
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.PROCEED
        supplier = partial.get("sourcer_supplier")
        assert supplier is not None
        assert supplier["category"] == "novelty"
        assert supplier["feasibility"] == "easy"
        assert supplier["unit_cost"] > 0
        assert supplier["synthetic"] is True

    async def test_blocked_category(self, trend_factory) -> None:
        agent = SourcerAgent(use_llm=False)
        candidate = trend_factory(title="CBD gummies for sleep", summary="full-spectrum cannabis")
        state = initial_state(candidate)
        state["scout_score"] = 0.8  # type: ignore[typeddict-item]
        partial = await agent(state)
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.BLOCK
        assert partial["sourcer_supplier"] is None

    async def test_synthesizer_deterministic(self, trend_factory) -> None:
        agent = SourcerAgent(use_llm=False)
        candidate = trend_factory(title="Custom enamel pin")
        state = initial_state(candidate)
        state["scout_score"] = 0.8  # type: ignore[typeddict-item]
        partial1 = await agent(state)
        partial2 = await agent(state)
        s1 = partial1["sourcer_supplier"]
        s2 = partial2["sourcer_supplier"]
        # Same trend_id → same synthesized supplier.
        assert s1["name"] == s2["name"]
        assert s1["unit_cost"] == s2["unit_cost"]


class TestAuditorAgent:
    async def test_no_supplier_holds(self, trend_factory) -> None:
        agent = AuditorAgent(use_llm=False)
        candidate = trend_factory()
        state = initial_state(candidate)
        # No sourcer_supplier in state.
        partial = await agent(state)
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.HOLD
        assert d.details["missing_dependency"] == "sourcer_supplier"
        assert partial["auditor_margin_p10"] == 0.0

    async def test_with_supplier_runs_simulation(self, trend_factory) -> None:
        agent = AuditorAgent(use_llm=False)
        candidate = trend_factory()
        state = initial_state(candidate)
        state["sourcer_supplier"] = {  # type: ignore[typeddict-item]
            "name": "AEGIS-Synth-AAAA",
            "category": "novelty",
            "feasibility": "easy",
            "moq": 50,
            "lead_time_days": 7,
            "unit_cost": 2.0,
            "synthetic": True,
        }
        partial = await agent(state)
        d = partial["decisions"][0]
        # With $2 cost and ~$8 default sell-price, margins should be
        # comfortably positive → PROCEED.
        assert d.verdict in {AgentVerdict.PROCEED, AgentVerdict.HOLD}
        assert "p10" in d.details
        assert partial["auditor_margin_p50"] != 0.0

    async def test_high_cost_blocks(self, trend_factory) -> None:
        agent = AuditorAgent(use_llm=False)
        candidate = trend_factory()
        state = initial_state(candidate)
        # Sell-price is clamped to $199.99 max. Set unit cost above
        # the clamp so margin is forced negative regardless of fees.
        state["sourcer_supplier"] = {  # type: ignore[typeddict-item]
            "unit_cost": 250.0,
            "category": "general",
            "feasibility": "hard",
            "synthetic": True,
        }
        partial = await agent(state)
        d = partial["decisions"][0]
        # 250×4 = 1000 → clamped to $199.99; cost $250 vs sell $200.
        # Mean margin guaranteed negative → BLOCK.
        assert d.verdict is AgentVerdict.BLOCK


class TestSentinelAgent:
    async def test_decay_recommends_exit(self, trend_factory) -> None:
        agent = SentinelAgent(use_llm=False)
        # 6h velocity high but 1h has dropped → decay
        # signal_count high → maturity.
        candidate = trend_factory(
            velocity_1h=2.0,
            velocity_6h=200.0,  # rate_6h_per_h ≈ 33 → much higher than 1h
            velocity_24h=300.0,
            signal_count=800,
            novelty=0.1,
            coordination_risk=0.4,
        )
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.PROCEED
        assert partial["sentinel_recommended_exit"] is True
        assert partial["sentinel_saturation"] >= 0.7

    async def test_fresh_trend_no_exit(self, trend_factory) -> None:
        agent = SentinelAgent(use_llm=False)
        candidate = trend_factory(
            velocity_1h=50.0,  # accelerating
            velocity_6h=120.0,
            velocity_24h=180.0,
            signal_count=20,
            novelty=0.9,
            coordination_risk=0.0,
        )
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.BLOCK  # "do not exit yet"
        assert partial["sentinel_recommended_exit"] is False

    async def test_block_does_not_add_to_blocked_by(self, trend_factory) -> None:
        agent = SentinelAgent(use_llm=False)
        candidate = trend_factory(velocity_1h=50.0, velocity_6h=120.0, velocity_24h=180.0)
        partial = await agent(initial_state(candidate))
        # SENTINEL.BLOCK ≠ pipeline halt — never adds to blocked_by.
        assert "blocked_by" not in partial


class TestComplianceAgent:
    async def test_clean_proceeds(self, trend_factory) -> None:
        agent = ComplianceAgent(use_llm=False)
        candidate = trend_factory(title="Bamboo travel mug", summary="Reusable bamboo travel mug")
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.PROCEED
        assert partial["compliance_passed"] is True

    async def test_trademark_flags_for_review(self, trend_factory) -> None:
        # Phase 8 (multi-jurisdiction engine): trademark match alone → FLAG→HOLD for
        # human review. Only regulatory violations (FDA/FTC disease/cure claims) → BLOCK.
        agent = ComplianceAgent(use_llm=False)
        candidate = trend_factory(title="Custom Disney mug")
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.HOLD
        assert partial["compliance_passed"] is False
        # Phase 8 stores trademark names in details["trademarks"]
        assert any(
            "disney" in str(t).lower()
            for t in d.details.get("trademarks", [])
        ), f"expected disney in trademarks, got: {d.details}"

    async def test_ftc_income_claim_holds(self, trend_factory) -> None:
        # FTC "guaranteed income" rule fires at FLAG severity → HOLD in Phase 2.
        agent = ComplianceAgent(use_llm=False)
        candidate = trend_factory(title="Guaranteed income from home business opportunity")
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.HOLD
        assert partial["compliance_passed"] is False  # hold ≠ pass

    async def test_fda_disease_claim_blocks(self, trend_factory) -> None:
        # FDA supplement disease-claim rule fires at BLOCK severity → BLOCK in Phase 2.
        agent = ComplianceAgent(use_llm=False)
        candidate = trend_factory(
            title="Dietary supplement cures cancer and prevents diabetes",
            summary="vitamin supplement",
        )
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.BLOCK
        assert partial["compliance_passed"] is False


class TestGeoArbitrageAgent:
    async def test_discovery_no_commerce_proceeds(self, trend_factory) -> None:
        agent = GeoArbitrageAgent(use_llm=False)
        candidate = trend_factory(
            platforms=["tiktok", "reddit", "instagram"],
        )
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.PROCEED
        assert partial["geo_arbitrage_score"] >= 0.66

    async def test_commerce_present_dampens(self, trend_factory) -> None:
        agent = GeoArbitrageAgent(use_llm=False)
        candidate = trend_factory(
            platforms=["tiktok", "reddit", "amazon"],
        )
        partial = await agent(initial_state(candidate))
        # With commerce on the list, arbitrage falls.
        assert partial["geo_arbitrage_score"] < 0.66

    async def test_no_arbitrage_when_only_commerce(self, trend_factory) -> None:
        agent = GeoArbitrageAgent(use_llm=False)
        candidate = trend_factory(platforms=["amazon", "etsy"])
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.BLOCK
        assert "blocked_by" not in partial  # advisory only

    async def test_unclassified_platforms(self, trend_factory) -> None:
        agent = GeoArbitrageAgent(use_llm=False)
        candidate = trend_factory(platforms=["weird_platform"])
        partial = await agent(initial_state(candidate))
        # No discovery / no commerce → score 0 → BLOCK (advisory).
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.BLOCK


class TestNarrativeAgent:
    async def test_strong_narrative_proceeds(self, trend_factory) -> None:
        agent = NarrativeAgent(use_llm=False)
        candidate = trend_factory(
            title="Life-changing kitchen gadget everyone's obsessed with",
            sentiment=0.85,
            novelty=0.8,
            unique_authors=25,
            signal_count=30,
            platforms=["tiktok", "reddit", "instagram"],
        )
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.PROCEED
        assert partial["narrative_score"] >= 0.65

    async def test_weak_narrative_blocks(self, trend_factory) -> None:
        agent = NarrativeAgent(use_llm=False)
        candidate = trend_factory(
            title="basic widget",
            summary="just a thing",
            representative_text="some product",
            sentiment=0.0,
            novelty=0.1,
            unique_authors=2,
            signal_count=20,
            platforms=["reddit"],
        )
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.BLOCK
        assert "blocked_by" not in partial  # advisory


class TestRedTeamAgent:
    async def test_clean_passes(self, trend_factory) -> None:
        agent = RedTeamAgent(use_llm=False)
        candidate = trend_factory(
            velocity_24h=200.0,  # solidly above sustained threshold
            coordination_risk=0.05,
            unique_authors=20,
            signal_count=30,
            novelty=0.6,
            platforms=["reddit", "tiktok"],
        )
        state = initial_state(candidate)
        state["scout_score"] = 0.85  # type: ignore[typeddict-item]
        state["compliance_passed"] = True  # type: ignore[typeddict-item]
        partial = await agent(state)
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.PROCEED
        assert partial["red_team_passed"] is True
        assert partial["red_team_falsifiers"] == []

    async def test_two_falsifiers_blocks(self, trend_factory) -> None:
        agent = RedTeamAgent(use_llm=False)
        candidate = trend_factory(
            coordination_risk=0.7,  # F1
            unique_authors=2,  # F2 — astroturf
            signal_count=100,
            velocity_24h=1.0,  # F3 — no sustain
        )
        state = initial_state(candidate)
        state["scout_score"] = 0.8  # type: ignore[typeddict-item]
        partial = await agent(state)
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.BLOCK
        assert partial["red_team_passed"] is False
        assert len(partial["red_team_falsifiers"]) >= 2

    async def test_compliance_failure_is_falsifier(self, trend_factory) -> None:
        agent = RedTeamAgent(use_llm=False)
        candidate = trend_factory()
        state = initial_state(candidate)
        state["compliance_passed"] = False  # type: ignore[typeddict-item]
        state["compliance_flags"] = ["tm:nike"]  # type: ignore[typeddict-item]
        state["scout_score"] = 0.8  # type: ignore[typeddict-item]
        partial = await agent(state)
        # 2 compliance-related falsifiers (F8 + F8b) → BLOCK.
        assert partial["red_team_passed"] is False


class TestHedgeAgent:
    async def test_no_shared_memory_proceeds(self, trend_factory) -> None:
        agent = HedgeAgent(shared_memory=None, use_llm=False)
        candidate = trend_factory()
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.PROCEED
        assert partial["hedge_passed"] is True

    async def test_no_shared_memory_low_confidence(self, trend_factory) -> None:
        agent = HedgeAgent(shared_memory=None, use_llm=False)
        candidate = trend_factory()
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.confidence < 0.3  # no-data flag → 0.2

    async def test_shared_memory_empty_pool_proceeds(self, trend_factory) -> None:
        from unittest.mock import AsyncMock

        sm = AsyncMock()
        sm.active_trends = AsyncMock(return_value=[])
        agent = HedgeAgent(shared_memory=sm, use_llm=False)
        candidate = trend_factory()
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.PROCEED
        assert d.details["active_pool_size"] == 0
        assert d.details["shared_memory_used"] is True

    async def test_within_concentration_cap_proceeds(self, trend_factory) -> None:
        from unittest.mock import AsyncMock

        sm = AsyncMock()
        tenant_id = "default"
        trend_id = "trend-1"
        # 2 same-category out of 10 → prospective=(3/11)≈0.27 < 0.40 cap
        same_cat = [(tenant_id, f"other-{i}") for i in range(2)]
        diff_cat = [(tenant_id, f"diff-{i}") for i in range(8)]
        sm.active_trends = AsyncMock(return_value=same_cat + diff_cat)

        async def get_all(ten, tid):
            if "other" in tid:
                return {"category": "fitness"}
            return {"category": "tech"}

        sm.get_all = get_all
        agent = HedgeAgent(shared_memory=sm, use_llm=False)
        candidate = trend_factory()
        state = initial_state(candidate)
        state["trend_id"] = trend_id
        state["tenant_id"] = tenant_id
        state["sourcer_supplier"] = {"category": "fitness"}
        partial = await agent(state)
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.PROCEED

    async def test_hold_on_moderate_concentration(self, trend_factory) -> None:
        from unittest.mock import AsyncMock

        sm = AsyncMock()
        tenant_id = "default"
        trend_id = "trend-x"
        # 4 same-category out of 8 → prospective = 5/9 ≈ 0.56; excess = 0.16 (≥0.05 HOLD)
        same_cat = [(tenant_id, f"same-{i}") for i in range(4)]
        diff_cat = [(tenant_id, f"diff-{i}") for i in range(4)]
        sm.active_trends = AsyncMock(return_value=same_cat + diff_cat)

        async def get_all(ten, tid):
            if "same" in tid:
                return {"category": "fitness"}
            return {"category": "tech"}

        sm.get_all = get_all
        agent = HedgeAgent(shared_memory=sm, use_llm=False)
        candidate = trend_factory()
        state = initial_state(candidate)
        state["trend_id"] = trend_id
        state["tenant_id"] = tenant_id
        state["sourcer_supplier"] = {"category": "fitness"}
        partial = await agent(state)
        d = partial["decisions"][0]
        assert d.verdict in (AgentVerdict.HOLD, AgentVerdict.BLOCK)
        assert d.details["hedge_passed"] is False

    async def test_block_on_severe_concentration(self, trend_factory) -> None:
        from unittest.mock import AsyncMock

        sm = AsyncMock()
        tenant_id = "default"
        trend_id = "trend-y"
        # 8 same-category out of 9 → prospective = 9/10 = 0.9; excess ≥ 0.20 → BLOCK
        same_cat = [(tenant_id, f"same-{i}") for i in range(8)]
        other_cat = [(tenant_id, "diff-0")]
        sm.active_trends = AsyncMock(return_value=same_cat + other_cat)

        async def get_all(ten, tid):
            if "same" in tid:
                return {"category": "fitness"}
            return {"category": "tech"}

        sm.get_all = get_all
        agent = HedgeAgent(shared_memory=sm, use_llm=False)
        candidate = trend_factory()
        state = initial_state(candidate)
        state["trend_id"] = trend_id
        state["tenant_id"] = tenant_id
        state["sourcer_supplier"] = {"category": "fitness"}
        partial = await agent(state)
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.BLOCK
        assert partial["hedge_passed"] is False

    async def test_augment_with_llm_noop_for_proceed(self, trend_factory) -> None:
        agent = HedgeAgent(shared_memory=None, use_llm=False)
        candidate = trend_factory()
        heuristic = AgentDecision(
            agent="hedge",
            trend_id=candidate.trend_id,
            correlation_id=candidate.correlation_id,
            verdict=AgentVerdict.PROCEED,
            score=0.9,
            confidence=0.5,
            reasoning="ok",
        )
        result = await agent._augment_with_llm(candidate, initial_state(candidate), heuristic)
        assert result is None

    async def test_augment_with_llm_noop_for_block(self, trend_factory) -> None:
        agent = HedgeAgent(shared_memory=None, use_llm=False)
        candidate = trend_factory()
        heuristic = AgentDecision(
            agent="hedge",
            trend_id=candidate.trend_id,
            correlation_id=candidate.correlation_id,
            verdict=AgentVerdict.BLOCK,
            score=0.1,
            confidence=0.8,
            reasoning="blocked",
        )
        result = await agent._augment_with_llm(candidate, initial_state(candidate), heuristic)
        assert result is None

    async def test_extra_state_reflects_decision_details(self, trend_factory) -> None:
        agent = HedgeAgent(shared_memory=None, use_llm=False)
        candidate = trend_factory()
        decision = AgentDecision(
            agent="hedge",
            trend_id=candidate.trend_id,
            correlation_id=candidate.correlation_id,
            verdict=AgentVerdict.PROCEED,
            score=0.9,
            confidence=0.5,
            reasoning="ok",
            details={"hedge_passed": True, "hedge_correlation_excess": 0.02},
        )
        extra = agent._extra_state(decision)
        assert extra["hedge_passed"] is True
        assert extra["hedge_correlation_excess"] == pytest.approx(0.02)


class TestHistorianAgent:
    async def test_no_store_proceeds_with_low_confidence(self, trend_factory) -> None:
        agent = HistorianAgent(store=None, use_llm=False)
        candidate = trend_factory()
        partial = await agent(initial_state(candidate))
        d = partial["decisions"][0]
        assert d.verdict is AgentVerdict.PROCEED
        assert partial["historian_analogues"] == []
        assert d.confidence < 0.5  # low-data flag

    async def test_advisory_no_blocked_by(self, trend_factory) -> None:
        agent = HistorianAgent(store=None, use_llm=False)
        candidate = trend_factory()
        partial = await agent(initial_state(candidate))
        assert "blocked_by" not in partial


class TestErrorHandling:
    """Ensure base class converts agent exceptions to error decisions."""

    async def test_exception_in_heuristic_caught(self, trend_factory) -> None:
        from aegis.agents.nodes.base import AgentNode

        class ExplodeAgent(AgentNode):
            name = "explode"

            async def _decide_heuristic(self, candidate, state):  # type: ignore[override]
                raise RuntimeError("boom")

        agent = ExplodeAgent(use_llm=False)
        candidate = trend_factory()
        partial = await agent(initial_state(candidate))
        d: AgentDecision = partial["decisions"][0]
        assert d.verdict is AgentVerdict.HOLD
        assert d.score == 0.0
        assert "boom" in d.reasoning
