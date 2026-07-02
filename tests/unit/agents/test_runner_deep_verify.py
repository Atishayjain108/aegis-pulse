"""Pass 10 — runner deep-verify integration (_deep_verify_high_confidence_result).

Verifies the async second-pass gate: P0 results (score >= 0.85) that fail a
check are downgraded to P1 with a 0.70 score floor; passing P0 results are
marked deep_verified=True; sub-threshold results pass through untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aegis.agents.runner import _deep_verify_high_confidence_result
from aegis.agents.schemas import AgentVerdict, GraphResult, Priority

pytestmark = pytest.mark.asyncio


def _result(score: float, **trend_data: object) -> GraphResult:
    now = datetime.now(UTC)
    return GraphResult(
        trend_id="t-dv",
        correlation_id="c-dv",
        final_verdict=AgentVerdict.PROCEED,
        final_priority=Priority.P0_BREAKOUT,
        final_score=score,
        final_confidence=0.9,
        started_at=now,
        finished_at=now,
        duration_ms=5.0,
        halt_reason="completed",
        trend_data=dict(trend_data),
    )


async def test_subthreshold_passes_through() -> None:
    """Score < 0.85 is not deep-verified — returned unchanged."""
    r = _result(0.5, platforms=["reddit"])
    out = await _deep_verify_high_confidence_result(r)
    assert out is r
    assert out.deep_verified is False


async def test_p0_all_checks_pass() -> None:
    """Happy path: strong P0 passes all 3 checks → deep_verified=True."""
    r = _result(
        0.9,
        velocity_1h=5.0, velocity_6h=12.0, velocity_24h=20.0,
        platforms=["reddit", "hacker_news"],
    )
    out = await _deep_verify_high_confidence_result(r)
    assert out.deep_verified is True
    assert out.deep_verify_failures == []
    assert out.final_score == 0.9


async def test_p0_failure_downgrades_to_p1() -> None:
    """Invariant: a failed check drops score 0.10 (floor 0.70) and → P1."""
    r = _result(0.9, velocity_1h=5.0, velocity_6h=12.0, velocity_24h=20.0, platforms=["reddit"])
    out = await _deep_verify_high_confidence_result(r)
    assert out.deep_verified is False
    assert "cross_source_confirmation" in out.deep_verify_failures
    assert out.final_priority is Priority.P1_EXIT
    assert out.final_score == pytest.approx(0.80)


async def test_failed_score_never_below_floor() -> None:
    """Edge/failure path: downgraded score never drops below the 0.70 floor."""
    r = _result(0.85, velocity_1h=0.01, velocity_6h=12.0, velocity_24h=20.0, platforms=["x"])
    out = await _deep_verify_high_confidence_result(r)
    assert out.final_score >= 0.70
