"""Schema validation tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from aegis.agents.schemas import (
    SCHEMA_VERSION,
    AgentDecision,
    AgentMessage,
    AgentVerdict,
    GraphResult,
    Priority,
    TrendCandidate,
)


class TestPriority:
    def test_ordering(self) -> None:
        assert int(Priority.P0_BREAKOUT) == 0
        assert int(Priority.P1_EXIT) == 1
        assert int(Priority.P2_OPPORTUNITY) == 2
        assert int(Priority.P3_HOUSEKEEPING) == 3

    def test_int_compare(self) -> None:
        # Lower number = higher priority — these comparisons matter
        # because the supervisor uses them in routing decisions.
        assert Priority.P0_BREAKOUT < Priority.P1_EXIT
        assert Priority.P1_EXIT < Priority.P3_HOUSEKEEPING


class TestAgentVerdict:
    def test_values(self) -> None:
        assert AgentVerdict.PROCEED.value == "proceed"
        assert AgentVerdict.HOLD.value == "hold"
        assert AgentVerdict.BLOCK.value == "block"
        assert AgentVerdict.ESCALATE.value == "escalate"


class TestAgentDecision:
    def _build(self, **overrides):
        defaults = {
            "agent": "scout",
            "trend_id": "t1",
            "correlation_id": "c1",
            "verdict": AgentVerdict.PROCEED,
            "score": 0.7,
            "confidence": 0.8,
        }
        defaults.update(overrides)
        return AgentDecision(**defaults)

    def test_minimal_construct(self) -> None:
        d = self._build()
        assert d.agent == "scout"
        assert d.score == 0.7
        assert d.schema_version == SCHEMA_VERSION

    def test_score_bounds_enforced(self) -> None:
        with pytest.raises(ValidationError):
            self._build(score=1.5)
        with pytest.raises(ValidationError):
            self._build(score=-0.1)

    def test_confidence_bounds_enforced(self) -> None:
        with pytest.raises(ValidationError):
            self._build(confidence=1.5)
        with pytest.raises(ValidationError):
            self._build(confidence=-0.1)

    def test_frozen(self) -> None:
        d = self._build()
        with pytest.raises(ValidationError):
            d.score = 0.5  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            AgentDecision(
                agent="scout",
                trend_id="t1",
                correlation_id="c1",
                verdict=AgentVerdict.PROCEED,
                score=0.7,
                confidence=0.8,
                bogus_field="x",  # type: ignore[call-arg]
            )

    def test_warning_on_inverted_block(self) -> None:
        # A BLOCK with very high score gets a `_warning` baked into
        # details automatically by the validator.
        d = self._build(verdict=AgentVerdict.BLOCK, score=0.99)
        assert "_warning" in d.details

    def test_warning_on_inverted_proceed(self) -> None:
        d = self._build(verdict=AgentVerdict.PROCEED, score=0.0)
        assert "_warning" in d.details


class TestAgentMessage:
    def _build(self, **overrides):
        defaults = {
            "correlation_id": "c1",
            "from_agent": "scout",
            "to_agent": "auditor",
        }
        defaults.update(overrides)
        return AgentMessage(**defaults)

    def test_default_priority(self) -> None:
        msg = self._build()
        assert msg.priority is Priority.P2_OPPORTUNITY

    def test_default_ttl(self) -> None:
        msg = self._build()
        assert msg.ttl_seconds == 300

    def test_ttl_bounds(self) -> None:
        with pytest.raises(ValidationError):
            self._build(ttl_seconds=0)
        with pytest.raises(ValidationError):
            self._build(ttl_seconds=86_401)

    def test_is_expired_false(self) -> None:
        msg = self._build(ttl_seconds=300)
        assert msg.is_expired() is False

    def test_is_expired_true(self) -> None:
        old_time = datetime.now(tz=UTC) - timedelta(seconds=600)
        msg = self._build(ttl_seconds=300, timestamp=old_time)
        assert msg.is_expired() is True

    def test_naive_datetime_coerced_to_utc(self) -> None:
        # Pydantic v2 + UTC validator accepts naive datetimes and
        # coerces them. Pacific time would be normalized.
        msg = self._build(timestamp=datetime(2025, 1, 1, 12, 0, 0))
        assert msg.timestamp.tzinfo is not None


class TestTrendCandidate:
    def test_minimal(self) -> None:
        c = TrendCandidate(trend_id="t1", title="Test")
        assert c.trend_id == "t1"
        assert c.signal_count == 0
        assert c.platforms == []

    def test_platforms_dedup_case_insensitive(self) -> None:
        c = TrendCandidate(
            trend_id="t1", title="Test", platforms=["Reddit", "reddit", "TikTok"]
        )
        assert len(c.platforms) == 2
        # Order preserved, first occurrence wins.
        assert c.platforms[0].lower() == "reddit"

    def test_sentiment_bounds(self) -> None:
        with pytest.raises(ValidationError):
            TrendCandidate(trend_id="t1", title="Test", sentiment=1.5)
        with pytest.raises(ValidationError):
            TrendCandidate(trend_id="t1", title="Test", sentiment=-1.5)

    def test_signal_count_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            TrendCandidate(trend_id="t1", title="Test", signal_count=-1)


class TestGraphResult:
    def test_minimal(self) -> None:
        now = datetime.now(tz=UTC)
        result = GraphResult(
            trend_id="t1",
            correlation_id="c1",
            final_verdict=AgentVerdict.PROCEED,
            final_priority=Priority.P0_BREAKOUT,
            final_score=0.85,
            final_confidence=0.9,
            decisions=[],
            blocked_by=[],
            started_at=now,
            finished_at=now,
            duration_ms=1.0,
            halt_reason="completed",
        )
        assert result.final_verdict is AgentVerdict.PROCEED

    def test_invalid_halt_reason_rejected(self) -> None:
        now = datetime.now(tz=UTC)
        with pytest.raises(ValidationError):
            GraphResult(
                trend_id="t1",
                correlation_id="c1",
                final_verdict=AgentVerdict.PROCEED,
                final_priority=Priority.P0_BREAKOUT,
                final_score=0.5,
                final_confidence=0.5,
                started_at=now,
                finished_at=now,
                duration_ms=0.0,
                halt_reason="bogus_reason",  # type: ignore[arg-type]
            )

    def test_frozen(self) -> None:
        now = datetime.now(tz=UTC)
        result = GraphResult(
            trend_id="t1",
            correlation_id="c1",
            final_verdict=AgentVerdict.PROCEED,
            final_priority=Priority.P0_BREAKOUT,
            final_score=0.5,
            final_confidence=0.5,
            started_at=now,
            finished_at=now,
            duration_ms=0.0,
            halt_reason="completed",
        )
        with pytest.raises(ValidationError):
            result.final_score = 0.9  # type: ignore[misc]
