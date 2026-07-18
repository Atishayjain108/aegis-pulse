"""
PASS6-6A: unit tests for the deep-verification second pass in the runner.

These test the pure verification functions and the orchestrator directly,
without compiling the LangGraph pipeline — so they run in the minimal-test
environment (no langgraph required).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aegis.agents import runner
from aegis.agents.schemas import (
    AgentDecision,
    AgentVerdict,
    GraphResult,
    Priority,
)


def _make_result(
    *,
    score: float,
    trend_data: dict | None = None,
    decisions: list[AgentDecision] | None = None,
) -> GraphResult:
    now = datetime.now(tz=UTC)
    return GraphResult(
        trend_id="t-deep-001",
        correlation_id="00000000-0000-0000-0000-000000000abc",
        final_verdict=AgentVerdict.PROCEED,
        final_priority=Priority.P0_BREAKOUT,
        final_score=score,
        final_confidence=0.9,
        decisions=decisions or [],
        blocked_by=[],
        started_at=now,
        finished_at=now,
        duration_ms=1.0,
        halt_reason="completed",
        trend_data=trend_data or {},
    )


def _red_team_decision(verdict: AgentVerdict) -> AgentDecision:
    return AgentDecision(
        agent="red_team",
        trend_id="t-deep-001",
        correlation_id="00000000-0000-0000-0000-000000000abc",
        verdict=verdict,
        score=0.5,
        confidence=0.5,
    )


class TestDeepVerifyGate:
    async def test_below_threshold_passes_through_unchanged(self) -> None:
        result = _make_result(score=0.80)
        out = await runner._deep_verify_high_confidence_result(result)
        assert out is result  # not even copied
        assert out.deep_verified is False

    async def test_p0_all_checks_pass_marks_verified(self) -> None:
        result = _make_result(
            score=0.90,
            trend_data={
                "velocity_1h": 100.0,
                "velocity_6h": 300.0,
                "velocity_24h": 600.0,
                "platforms": ["reddit", "hacker_news"],
            },
            decisions=[_red_team_decision(AgentVerdict.PROCEED)],
        )
        out = await runner._deep_verify_high_confidence_result(result)
        assert out.deep_verified is True
        assert out.deep_verify_failures == []
        assert out.final_score == 0.90
        assert out.final_priority is Priority.P0_BREAKOUT

    async def test_single_platform_fails_cross_source(self) -> None:
        result = _make_result(
            score=0.92,
            trend_data={
                "velocity_1h": 100.0,
                "velocity_6h": 300.0,
                "velocity_24h": 600.0,
                "platforms": ["reddit"],  # only one platform
            },
            decisions=[_red_team_decision(AgentVerdict.PROCEED)],
        )
        out = await runner._deep_verify_high_confidence_result(result)
        assert out.deep_verified is False
        assert "cross_source_confirmation" in out.deep_verify_failures
        # downgraded one priority level, score reduced by 0.10
        assert out.final_priority is Priority.P1_EXIT
        assert out.final_score == pytest.approx(0.82)

    async def test_red_team_block_fails_review(self) -> None:
        result = _make_result(
            score=0.95,
            trend_data={
                "velocity_1h": 100.0,
                "velocity_6h": 300.0,
                "velocity_24h": 600.0,
                "platforms": ["reddit", "hacker_news"],
            },
            decisions=[_red_team_decision(AgentVerdict.BLOCK)],
        )
        out = await runner._deep_verify_high_confidence_result(result)
        assert out.deep_verified is False
        assert "red_team_review" in out.deep_verify_failures

    async def test_spike_and_crash_fails_temporal(self) -> None:
        # 1h pace is far below the 6h average pace → decelerating spike.
        result = _make_result(
            score=0.91,
            trend_data={
                "velocity_1h": 1.0,
                "velocity_6h": 600.0,
                "velocity_24h": 800.0,
                "platforms": ["reddit", "hacker_news"],
            },
            decisions=[_red_team_decision(AgentVerdict.PROCEED)],
        )
        out = await runner._deep_verify_high_confidence_result(result)
        assert out.deep_verified is False
        assert "temporal_consistency" in out.deep_verify_failures

    async def test_failed_p0_never_drops_below_floor(self) -> None:
        result = _make_result(
            score=0.85,
            trend_data={"platforms": []},  # fails cross-source
        )
        out = await runner._deep_verify_high_confidence_result(result)
        assert out.final_score >= 0.70


class TestTemporalConsistency:
    def test_accelerating_passes(self) -> None:
        result = _make_result(
            score=0.9,
            trend_data={"velocity_1h": 100.0, "velocity_6h": 300.0, "velocity_24h": 600.0},
        )
        assert runner._check_temporal_consistency(result) is True

    def test_insufficient_data_passes(self) -> None:
        result = _make_result(score=0.9, trend_data={"velocity_6h": 0.0, "velocity_24h": 0.0})
        assert runner._check_temporal_consistency(result) is True

    def test_missing_keys_passes(self) -> None:
        result = _make_result(score=0.9, trend_data={})
        assert runner._check_temporal_consistency(result) is True


class TestCrossSourceConfirmation:
    def test_two_platforms_passes(self) -> None:
        result = _make_result(score=0.9, trend_data={"platforms": ["a", "b"]})
        assert runner._check_cross_source_confirmation(result) is True

    def test_one_platform_fails(self) -> None:
        result = _make_result(score=0.9, trend_data={"platforms": ["a"]})
        assert runner._check_cross_source_confirmation(result) is False

    def test_duplicate_platform_counts_once(self) -> None:
        result = _make_result(score=0.9, trend_data={"platforms": ["a", "a"]})
        assert runner._check_cross_source_confirmation(result) is False
