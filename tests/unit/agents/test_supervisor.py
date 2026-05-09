"""Tests for the supervisor aggregation logic."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aegis.agents.schemas import (
    AgentDecision,
    AgentVerdict,
    GraphResult,
    Priority,
)
from aegis.agents.state import initial_state
from aegis.agents.supervisor import (
    build_graph_result,
    compute_final_score,
    compute_final_verdict,
    compute_halt_reason,
    compute_priority,
    finalize,
)


def _decision(agent: str, verdict: AgentVerdict, score: float = 0.7,
              confidence: float = 0.8) -> AgentDecision:
    return AgentDecision(
        agent=agent,
        trend_id="t1",
        correlation_id="c1",
        verdict=verdict,
        score=score,
        confidence=confidence,
    )


class TestComputeFinalVerdict:
    def test_blocked_by_anything_blocks(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["blocked_by"] = ["compliance"]  # type: ignore[typeddict-item]
        assert compute_final_verdict(state) is AgentVerdict.BLOCK

    def test_compliance_failed_blocks(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["compliance_passed"] = False  # type: ignore[typeddict-item]
        assert compute_final_verdict(state) is AgentVerdict.BLOCK

    def test_red_team_failed_blocks(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["red_team_passed"] = False  # type: ignore[typeddict-item]
        assert compute_final_verdict(state) is AgentVerdict.BLOCK

    def test_hedge_failed_blocks(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["hedge_passed"] = False  # type: ignore[typeddict-item]
        assert compute_final_verdict(state) is AgentVerdict.BLOCK

    def test_default_to_scout_verdict(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["scout_verdict"] = AgentVerdict.PROCEED  # type: ignore[typeddict-item]
        assert compute_final_verdict(state) is AgentVerdict.PROCEED

    def test_no_scout_verdict_holds(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        assert compute_final_verdict(state) is AgentVerdict.HOLD


class TestComputePriority:
    def test_sentinel_exit_p1(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["sentinel_recommended_exit"] = True  # type: ignore[typeddict-item]
        assert compute_priority(state) is Priority.P1_EXIT

    def test_strong_breakout_p0(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["scout_verdict"] = AgentVerdict.PROCEED  # type: ignore[typeddict-item]
        state["scout_score"] = 0.9  # type: ignore[typeddict-item]
        state["red_team_passed"] = True  # type: ignore[typeddict-item]
        state["compliance_passed"] = True  # type: ignore[typeddict-item]
        state["hedge_passed"] = True  # type: ignore[typeddict-item]
        assert compute_priority(state) is Priority.P0_BREAKOUT

    def test_proceed_but_red_team_blocks_drops_to_p2(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["scout_verdict"] = AgentVerdict.PROCEED  # type: ignore[typeddict-item]
        state["scout_score"] = 0.9  # type: ignore[typeddict-item]
        state["red_team_passed"] = False  # type: ignore[typeddict-item]
        state["compliance_passed"] = True  # type: ignore[typeddict-item]
        state["hedge_passed"] = True  # type: ignore[typeddict-item]
        assert compute_priority(state) is Priority.P2_OPPORTUNITY

    def test_block_drops_to_p3(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["scout_verdict"] = AgentVerdict.BLOCK  # type: ignore[typeddict-item]
        assert compute_priority(state) is Priority.P3_HOUSEKEEPING


class TestComputeHaltReason:
    def test_compliance_blocked(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["compliance_passed"] = False  # type: ignore[typeddict-item]
        state["blocked_by"] = ["compliance"]  # type: ignore[typeddict-item]
        assert compute_halt_reason(state) == "blocked_by_compliance"

    def test_red_team_veto(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["red_team_passed"] = False  # type: ignore[typeddict-item]
        state["blocked_by"] = ["red_team"]  # type: ignore[typeddict-item]
        assert compute_halt_reason(state) == "vetoed_by_red_team"

    def test_hedge_veto(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["hedge_passed"] = False  # type: ignore[typeddict-item]
        state["blocked_by"] = ["hedge"]  # type: ignore[typeddict-item]
        assert compute_halt_reason(state) == "vetoed_by_hedge"

    def test_scout_below_threshold(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["scout_verdict"] = AgentVerdict.BLOCK  # type: ignore[typeddict-item]
        assert compute_halt_reason(state) == "scout_below_threshold"

    def test_completed_default(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["scout_verdict"] = AgentVerdict.PROCEED  # type: ignore[typeddict-item]
        assert compute_halt_reason(state) == "completed"


class TestComputeFinalScore:
    def test_blends_decisions(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        decisions = [
            _decision("scout", AgentVerdict.PROCEED, score=0.9, confidence=0.9),
            _decision("narrative", AgentVerdict.PROCEED, score=0.7, confidence=0.7),
            _decision("geo_arbitrage", AgentVerdict.PROCEED, score=0.6, confidence=0.6),
            _decision("auditor", AgentVerdict.PROCEED, score=0.5, confidence=0.7),
            _decision("red_team", AgentVerdict.PROCEED, score=0.8, confidence=0.8),
        ]
        state["decisions"] = decisions  # type: ignore[typeddict-item]
        state["auditor_margin_p10"] = 4.0  # type: ignore[typeddict-item]
        score, confidence = compute_final_score(state)
        assert 0.0 < score <= 1.0
        assert 0.0 < confidence <= 1.0

    def test_empty_state_zero(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        score, confidence = compute_final_score(state)
        # Auditor weight always counted via margin path.
        assert score >= 0.0
        assert confidence >= 0.0


class TestFinalize:
    def test_writes_all_finals(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["scout_verdict"] = AgentVerdict.PROCEED  # type: ignore[typeddict-item]
        state["scout_score"] = 0.9  # type: ignore[typeddict-item]
        state["red_team_passed"] = True  # type: ignore[typeddict-item]
        state["compliance_passed"] = True  # type: ignore[typeddict-item]
        state["hedge_passed"] = True  # type: ignore[typeddict-item]
        state["auditor_margin_p10"] = 3.0  # type: ignore[typeddict-item]
        state["decisions"] = [  # type: ignore[typeddict-item]
            _decision("scout", AgentVerdict.PROCEED, score=0.9),
            _decision("auditor", AgentVerdict.PROCEED, score=0.5),
        ]
        out = finalize(state)
        assert out["final_verdict"] is AgentVerdict.PROCEED
        assert out["final_priority"] is Priority.P0_BREAKOUT
        assert "final_score" in out
        assert "final_confidence" in out
        assert out["halt_reason"] == "completed"


class TestBuildGraphResult:
    def test_well_formed(self, trend_factory) -> None:
        candidate = trend_factory()
        state = initial_state(candidate)
        state["final_verdict"] = AgentVerdict.PROCEED  # type: ignore[typeddict-item]
        state["final_priority"] = Priority.P0_BREAKOUT  # type: ignore[typeddict-item]
        state["final_score"] = 0.8  # type: ignore[typeddict-item]
        state["final_confidence"] = 0.7  # type: ignore[typeddict-item]
        state["halt_reason"] = "completed"  # type: ignore[typeddict-item]
        started = datetime.now(tz=UTC) - timedelta(seconds=1)
        result = build_graph_result(state, started_at=started)
        assert isinstance(result, GraphResult)
        assert result.final_verdict is AgentVerdict.PROCEED
        assert result.final_priority is Priority.P0_BREAKOUT
        assert result.duration_ms > 0

    def test_invalid_halt_normalized(self, trend_factory) -> None:
        candidate = trend_factory()
        state = initial_state(candidate)
        state["final_verdict"] = AgentVerdict.HOLD  # type: ignore[typeddict-item]
        state["final_priority"] = Priority.P3_HOUSEKEEPING  # type: ignore[typeddict-item]
        state["final_score"] = 0.0  # type: ignore[typeddict-item]
        state["final_confidence"] = 0.0  # type: ignore[typeddict-item]
        state["halt_reason"] = "weird_string_not_in_literal"  # type: ignore[typeddict-item]
        result = build_graph_result(state, started_at=datetime.now(tz=UTC))
        assert result.halt_reason == "completed"
