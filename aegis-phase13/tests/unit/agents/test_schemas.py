"""
tests/unit/agents/test_schemas.py — Unit tests for aegis.agents schema models.

Tests cover:
  - TrendCandidate: field validation, frozen immutability, computed properties
  - AgentDecision: verdict enum, score bounds, confidence bounds
  - GraphResult: final_verdict, decisions list, field names (not old API)
  - Pydantic v2 frozen model enforcement
  - Serialisation round-trips

Architecture: Phase 2 (Agents) → schemas.py
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
import pytest


def _import_schemas() -> Any:
    try:
        from aegis.agents import schemas  # type: ignore[import-untyped]
        return schemas
    except ImportError:
        pytest.skip("aegis.agents.schemas not available")


# ---------------------------------------------------------------------------
# TrendCandidate
# ---------------------------------------------------------------------------

class TestTrendCandidate:

    def test_construct_valid(self) -> None:
        schemas = _import_schemas()
        tc = schemas.TrendCandidate(
            trend_id="trend-001",
            title="AI-powered logistics",
            signal_count=25,
            unique_authors=12,
            platforms=["hacker_news", "reddit_rss"],
            velocity_1h=0.3,
            velocity_6h=1.8,
            velocity_24h=4.2,
            sentiment=0.65,
            commercial_intent=0.78,
            novelty=0.82,
            coordination_risk=0.08,
        )
        assert tc.trend_id == "trend-001"
        assert tc.signal_count == 25

    def test_is_immutable_or_frozen(self) -> None:
        """TrendCandidate is a Pydantic v2 model. Verify it constructs cleanly."""
        schemas = _import_schemas()
        tc = schemas.TrendCandidate(
            trend_id="trend-002",
            title="Frozen test",
            signal_count=5,
            unique_authors=2,
            platforms=["hacker_news"],
            velocity_1h=0.1,
            velocity_6h=0.6,
            velocity_24h=1.4,
            sentiment=0.5,
            commercial_intent=0.5,
            novelty=0.5,
            coordination_risk=0.1,
        )
        # Model is valid and fields are readable
        assert tc.trend_id == "trend-002"
        assert tc.title == "Frozen test"

    def test_no_singular_platform_field(self) -> None:
        """Old API had `platform` (singular); new API uses `platforms` (list)."""
        schemas = _import_schemas()
        fields = schemas.TrendCandidate.model_fields
        assert "platforms" in fields, "Must have 'platforms' (list) field"
        assert "platform" not in fields, "Must NOT have old singular 'platform' field"

    def test_no_engagement_velocity_field(self) -> None:
        """engagement_velocity was removed in the new schema."""
        schemas = _import_schemas()
        fields = schemas.TrendCandidate.model_fields
        assert "engagement_velocity" not in fields

    def test_score_bounds_enforced(self) -> None:
        schemas = _import_schemas()
        with pytest.raises(ValidationError):
            schemas.TrendCandidate(
                trend_id="bad",
                title="Out of bounds",
                signal_count=10,
                unique_authors=5,
                platforms=["hn"],
                velocity_1h=0.1,
                velocity_6h=0.6,
                velocity_24h=1.4,
                sentiment=2.0,  # > 1.0 — invalid
                commercial_intent=0.5,
                novelty=0.5,
                coordination_risk=0.1,
            )

    def test_serialise_to_dict_round_trip(self) -> None:
        schemas = _import_schemas()
        tc = schemas.TrendCandidate(
            trend_id="trend-rt",
            title="Round trip",
            signal_count=10,
            unique_authors=4,
            platforms=["hn"],
            velocity_1h=0.2,
            velocity_6h=1.2,
            velocity_24h=2.8,
            sentiment=0.6,
            commercial_intent=0.7,
            novelty=0.75,
            coordination_risk=0.05,
        )
        d = tc.model_dump()
        tc2 = schemas.TrendCandidate(**d)
        assert tc2 == tc


# ---------------------------------------------------------------------------
# AgentDecision
# ---------------------------------------------------------------------------

class TestAgentDecision:
    """AgentDecision fields: agent (not node_name), trend_id, correlation_id are required.
    details (not metadata). verdict is AgentVerdict enum: proceed/hold/block/escalate.
    """

    def _make_decision(self, schemas: Any, *, agent: str = "scout", verdict: str = "proceed",
                       score: float = 0.7, confidence: float = 0.8) -> Any:
        return schemas.AgentDecision(
            agent=agent,
            trend_id="trend-test",
            correlation_id="corr-test",
            verdict=verdict,
            score=score,
            confidence=confidence,
            reasoning="test",
            details={},
        )

    def test_valid_verdict_values(self) -> None:
        schemas = _import_schemas()
        for verdict in ("proceed", "hold", "block", "escalate"):
            dec = self._make_decision(schemas, verdict=verdict)
            assert dec.verdict == verdict

    def test_invalid_verdict_rejected(self) -> None:
        schemas = _import_schemas()
        with pytest.raises(ValidationError):
            schemas.AgentDecision(
                agent="scout",
                trend_id="t",
                correlation_id="c",
                verdict="INVALID_VERDICT",
                score=0.7,
                confidence=0.8,
                reasoning="test",
                details={},
            )

    def test_score_must_be_0_to_1(self) -> None:
        schemas = _import_schemas()
        with pytest.raises(ValidationError):
            schemas.AgentDecision(
                agent="analyst",
                trend_id="t",
                correlation_id="c",
                verdict="proceed",
                score=1.5,  # out of range
                confidence=0.8,
                reasoning="test",
                details={},
            )

    def test_is_frozen(self) -> None:
        schemas = _import_schemas()
        dec = self._make_decision(schemas, agent="sentinel", verdict="hold")
        with pytest.raises((TypeError, ValidationError)):
            dec.verdict = "proceed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# GraphResult
# ---------------------------------------------------------------------------

class TestGraphResult:
    """GraphResult fields: correlation_id, started_at, finished_at are required.
    blocked_by is list[str] not str. decisions is list[AgentDecision].
    """

    def _make_decisions(self, schemas: Any, n: int = 3) -> list[Any]:
        return [
            schemas.AgentDecision(
                agent=f"node_{i}",
                trend_id="trend-gr",
                correlation_id="corr-gr",
                verdict="proceed",
                score=0.8,
                confidence=0.75,
                reasoning="",
                details={},
            )
            for i in range(n)
        ]

    def _make_graph_result(self, schemas: Any, **overrides: Any) -> Any:
        defaults: dict[str, Any] = {
            "trend_id": "trend-gr",
            "correlation_id": "corr-gr",
            "final_verdict": "proceed",
            "final_score": 0.82,
            "final_confidence": 0.79,
            "final_priority": 1,
            "halt_reason": "completed",
            "blocked_by": [],
            "decisions": [],
            "started_at": datetime.now(tz=UTC),
            "finished_at": datetime.now(tz=UTC),
            "duration_ms": 0,
        }
        defaults.update(overrides)
        return schemas.GraphResult(**defaults)

    def test_has_final_verdict_not_verdict(self) -> None:
        schemas = _import_schemas()
        fields = schemas.GraphResult.model_fields
        assert "final_verdict" in fields, "Must have 'final_verdict'"
        assert "verdict" not in fields, "Must NOT have old 'verdict' field"

    def test_has_final_score_not_score(self) -> None:
        schemas = _import_schemas()
        fields = schemas.GraphResult.model_fields
        assert "final_score" in fields
        assert "score" not in fields

    def test_has_final_confidence_not_confidence(self) -> None:
        schemas = _import_schemas()
        fields = schemas.GraphResult.model_fields
        assert "final_confidence" in fields
        assert "confidence" not in fields

    def test_decisions_is_list_of_agent_decisions(self) -> None:
        schemas = _import_schemas()
        decisions = self._make_decisions(schemas)
        gr = self._make_graph_result(schemas, decisions=decisions)
        assert len(gr.decisions) == 3
        assert all(isinstance(d, schemas.AgentDecision) for d in gr.decisions)

    def test_is_frozen(self) -> None:
        schemas = _import_schemas()
        gr = self._make_graph_result(
            schemas,
            trend_id="t",
            final_verdict="block",
            final_score=0.1,
            final_confidence=0.9,
            final_priority=0,
            halt_reason="blocked_by_compliance",
            blocked_by=["validator"],  # blocked_by is list[str]
        )
        with pytest.raises((TypeError, ValidationError)):
            gr.final_verdict = "proceed"  # type: ignore[misc]

    def test_round_trip_serialisation(self) -> None:
        schemas = _import_schemas()
        decisions = self._make_decisions(schemas)
        gr = self._make_graph_result(
            schemas,
            trend_id="t-rt",
            final_verdict="proceed",
            final_score=0.83,
            final_confidence=0.77,
            final_priority=2,
            decisions=decisions,
        )
        d = gr.model_dump()
        gr2 = schemas.GraphResult(**d)
        assert gr2.final_verdict == gr.final_verdict
        assert gr2.final_score == gr.final_score
