"""Pass 10 — second-pass deep-verification logic (pure check helpers).

The P0 deep-verify gate (score >= 0.85) runs three independent checks:
temporal consistency, cross-source confirmation, red-team review. This
file covers the synchronous, pure check helpers and the threshold constant.
The async integration is covered in tests/unit/agents/test_runner_deep_verify.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aegis.agents.runner import (
    _DEEP_VERIFY_THRESHOLD,
    _check_cross_source_confirmation,
    _check_temporal_consistency,
)
from aegis.agents.schemas import AgentVerdict, GraphResult, Priority


def _result(**trend_data: object) -> GraphResult:
    now = datetime.now(UTC)
    return GraphResult(
        trend_id="t-1",
        correlation_id="c-1",
        final_verdict=AgentVerdict.PROCEED,
        final_priority=Priority.P0_BREAKOUT,
        final_score=0.9,
        final_confidence=0.9,
        started_at=now,
        finished_at=now,
        duration_ms=5.0,
        halt_reason="completed",
        trend_data=dict(trend_data),
    )


def test_threshold_is_p0_floor() -> None:
    assert _DEEP_VERIFY_THRESHOLD == 0.85


def test_temporal_consistency_accelerating_passes() -> None:
    """Happy path: accelerating velocity pattern passes."""
    r = _result(velocity_1h=5.0, velocity_6h=12.0, velocity_24h=20.0)
    assert _check_temporal_consistency(r) is True


def test_temporal_consistency_spike_crash_fails() -> None:
    """A decelerating spike-and-crash pattern fails the check."""
    r = _result(velocity_1h=0.1, velocity_6h=12.0, velocity_24h=20.0)
    assert _check_temporal_consistency(r) is False


def test_temporal_insufficient_data_passes() -> None:
    """Edge case: zero 6h/24h velocity → insufficient data → pass."""
    r = _result(velocity_1h=0.0, velocity_6h=0.0, velocity_24h=0.0)
    assert _check_temporal_consistency(r) is True


def test_cross_source_two_platforms_passes() -> None:
    r = _result(platforms=["reddit", "hacker_news"])
    assert _check_cross_source_confirmation(r) is True


def test_cross_source_single_platform_fails() -> None:
    """Invariant: a single-platform signal fails cross-source confirmation."""
    r = _result(platforms=["reddit"])
    assert _check_cross_source_confirmation(r) is False


def test_checks_tolerate_missing_trend_data() -> None:
    """Failure path: empty trend_data must not raise — defaults to pass."""
    r = _result()
    assert _check_temporal_consistency(r) is True
    assert _check_cross_source_confirmation(r) is False
