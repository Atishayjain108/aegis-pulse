"""Tests for GraphState reducer functions."""

from __future__ import annotations

from datetime import UTC, datetime

from aegis.agents.schemas import AgentDecision, AgentVerdict
from aegis.agents.state import (
    _merge_blockers,
    _merge_decisions,
    _merge_dicts,
    initial_state,
)


def _decision(agent: str, correlation_id: str = "c1", score: float = 0.5):
    return AgentDecision(
        agent=agent,
        trend_id="t1",
        correlation_id=correlation_id,
        verdict=AgentVerdict.HOLD,
        score=score,
        confidence=0.5,
        timestamp=datetime.now(tz=UTC),
    )


class TestMergeDecisions:
    def test_concat(self) -> None:
        a = [_decision("scout")]
        b = [_decision("auditor")]
        merged = _merge_decisions(a, b)
        assert [d.agent for d in merged] == ["scout", "auditor"]

    def test_dedupe_latest_wins(self) -> None:
        # Two decisions for the same (agent, correlation_id) — the
        # rightmost (latest) should win.
        a = [_decision("scout", score=0.3)]
        b = [_decision("scout", score=0.7)]
        merged = _merge_decisions(a, b)
        assert len(merged) == 1
        assert merged[0].score == 0.7

    def test_different_correlations_kept(self) -> None:
        a = [_decision("scout", correlation_id="c1")]
        b = [_decision("scout", correlation_id="c2")]
        merged = _merge_decisions(a, b)
        assert len(merged) == 2

    def test_empty_inputs(self) -> None:
        assert _merge_decisions([], []) == []


class TestMergeBlockers:
    def test_union_preserve_order(self) -> None:
        a = ["compliance"]
        b = ["red_team", "compliance"]
        out = _merge_blockers(a, b)
        assert out == ["compliance", "red_team"]

    def test_empty(self) -> None:
        assert _merge_blockers([], []) == []


class TestMergeDicts:
    def test_right_biased(self) -> None:
        a = {"x": 1, "y": 2}
        b = {"y": 99, "z": 3}
        out = _merge_dicts(a, b)
        assert out == {"x": 1, "y": 99, "z": 3}


class TestInitialState:
    def test_carries_inputs(self, trend_factory) -> None:
        candidate = trend_factory()
        state = initial_state(candidate, tenant_id="acme")
        assert state["candidate"] is candidate
        assert state["tenant_id"] == "acme"
        assert state["trend_id"] == candidate.trend_id
        assert state["correlation_id"] == candidate.correlation_id
        assert state["decisions"] == []
        assert state["blocked_by"] == []
        assert state["error_count"] == 0
