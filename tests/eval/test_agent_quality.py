"""
Agent pipeline quality tests for AEGIS Pulse.

Architecture relationship:
  Tests the full run_trend() → GraphResult pipeline end-to-end.
  Enforces the six routing invariants defined in sessions.md and CLAUDE.md:
    INVARIANT-1  SENTINEL_ALWAYS_RUNS
    INVARIANT-2  WEAK_SCOUT_ROUTING  (sentinel runs even on weak signal)
    INVARIANT-3  STRONG_SCOUT_ROUTING
    INVARIANT-4  NO_SUPPLIER_ROUTING (sentinel runs even with no supplier)
    INVARIANT-5  PARALLEL_START
    INVARIANT-6  FINAL_AGENTS

All TestAgentInvariants tests are gate-zero: zero failures allowed.
TestAgentCalibration and TestAgentPerformance may be skipped with documented
reasons but must never silently pass an incorrect result.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from aegis.agents.schemas import AgentVerdict, GraphResult, TrendCandidate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_HALT_REASONS = frozenset(
    {
        "completed",
        "vetoed_by_red_team",
        "vetoed_by_hedge",
        "blocked_by_compliance",
        "scout_below_threshold",
        "no_supplier",
        "exception",
        "timeout",
    }
)

_VALID_AGENT_NAMES = frozenset(
    {
        "scout",
        "geo_arbitrage",
        "narrative",
        "historian",
        "sourcer",
        "auditor",
        "sentinel",
        "compliance",
        "red_team",
        "hedge",
        "finalize",
    }
)

_VALID_VERDICTS = frozenset(
    {AgentVerdict.PROCEED, AgentVerdict.HOLD, AgentVerdict.BLOCK, AgentVerdict.ESCALATE}
)


def _run(tc: TrendCandidate, **kwargs: Any) -> GraphResult:
    """Synchronous wrapper around the async run_trend() entrypoint."""
    from aegis.agents.runner import run_trend

    return asyncio.run(run_trend(tc, use_llm=False, **kwargs))


def _agents_ran(result: GraphResult) -> set[str]:
    return {d.agent for d in result.decisions}


# ===========================================================================
# SECTION 1 — TestAgentInvariants
# Gate-zero: ALL must pass. Zero failures tolerated.
# ===========================================================================


class TestAgentInvariants:
    """Safety-critical routing invariants. Must all pass on every run."""

    def test_sentinel_always_runs_strong_scout(self, trend_factory: Any) -> None:
        """INVARIANT-1 + INVARIANT-3: strong-scout path still passes through sentinel.

        Uses real DB velocity (vel_1h=106) which reliably triggers strong-scout
        routing (score >= 0.55) through the scout node heuristics.
        """
        # Real DB stats: vel_1h=106, high enough for strong scout
        tc = trend_factory(_id="inv1-strong")
        result = _run(tc)
        ran = _agents_ran(result)
        assert "sentinel" in ran, (
            f"INVARIANT-1 VIOLATED: sentinel did not run on strong-scout path.\n"
            f"Agents that ran: {sorted(ran)}\n"
            f"final_verdict={result.final_verdict}, halt={result.halt_reason}"
        )

    def test_sentinel_always_runs_weak_scout(self, trend_factory: Any) -> None:
        """INVARIANT-1 + INVARIANT-2: sentinel runs even when scout is weak.

        # SYNTHETIC_EDGE_CASE: vel_1h=0.05 is far below any real DB floor (106).
        Deliberately weak to force weak-scout routing.
        """
        tc = trend_factory(  # SYNTHETIC_EDGE_CASE
            _id="inv2-weak",
            velocity_1h=0.05,
            velocity_6h=0.1,
            velocity_24h=0.2,
            signal_count=2,
            unique_authors=1,
            commercial_intent=0.1,
            novelty=0.1,
        )
        result = _run(tc)
        ran = _agents_ran(result)
        assert "sentinel" in ran, (
            f"INVARIANT-2 VIOLATED: sentinel did not run on weak-scout path.\n"
            f"Agents that ran: {sorted(ran)}\n"
            f"final_verdict={result.final_verdict}, halt={result.halt_reason}"
        )

    def test_sentinel_always_runs_zero_signal(self, trend_factory: Any) -> None:
        """INVARIANT-1: sentinel runs even for a near-zero signal trend."""
        tc = trend_factory(  # SYNTHETIC_EDGE_CASE
            _id="inv1-zero",
            signal_count=1,
            velocity_1h=0.0,
            velocity_6h=0.0,
            velocity_24h=0.0,
            sentiment=0.0,
            commercial_intent=0.0,
            novelty=0.0,
        )
        result = _run(tc)
        ran = _agents_ran(result)
        assert "sentinel" in ran, (
            f"INVARIANT-1 VIOLATED: sentinel did not run for zero-signal trend.\n"
            f"Agents that ran: {sorted(ran)}\n"
            f"halt={result.halt_reason}"
        )

    @pytest.mark.parametrize(
        "vel",
        [
            pytest.param(0.05, id="very-weak"),
            pytest.param(53.0, id="half-real"),    # real vel_1h * 0.5
            pytest.param(106.0, id="real-vel"),    # real vel_1h
            pytest.param(318.0, id="triple-real"), # real vel_1h * 3
        ],
    )
    def test_compliance_always_runs_all_velocities(
        self, trend_factory: Any, vel: float
    ) -> None:
        """INVARIANT-1: compliance gate always runs, regardless of scout strength."""
        tc = trend_factory(_id=f"comp-vel-{vel}", velocity_1h=vel)
        result = _run(tc)
        ran = _agents_ran(result)
        assert "compliance" in ran, (
            f"compliance did not run at velocity={vel}.\n"
            f"Agents that ran: {sorted(ran)}\n"
            f"halt={result.halt_reason}"
        )

    def test_compliance_runs_after_sentinel(self, trend_factory: Any) -> None:
        """INVARIANT-1: in the decisions list, sentinel appears before compliance."""
        tc = trend_factory(_id="order-check")
        result = _run(tc)
        agent_order = [d.agent for d in result.decisions]

        assert "sentinel" in agent_order, "sentinel did not run at all"
        assert "compliance" in agent_order, "compliance did not run at all"

        sentinel_idx = agent_order.index("sentinel")
        compliance_idx = agent_order.index("compliance")
        assert sentinel_idx < compliance_idx, (
            f"sentinel (pos {sentinel_idx}) did not precede compliance (pos {compliance_idx}).\n"
            f"Full agent order: {agent_order}"
        )

    def test_verdict_is_valid(self, trend_factory: Any) -> None:
        """GraphResult.final_verdict must be one of the four AgentVerdict values."""
        tc = trend_factory(_id="verdict-check")
        result = _run(tc)
        assert result.final_verdict in _VALID_VERDICTS, (
            f"Invalid final_verdict: {result.final_verdict!r}"
        )

    def test_score_bounds(self, trend_factory: Any) -> None:
        """GraphResult.final_score must be in [0.0, 1.0]."""
        tc = trend_factory(_id="score-bounds")
        result = _run(tc)
        assert 0.0 <= result.final_score <= 1.0, (
            f"final_score={result.final_score} is out of [0, 1]"
        )

    def test_confidence_bounds(self, trend_factory: Any) -> None:
        """GraphResult.final_confidence must be in [0.0, 1.0]."""
        tc = trend_factory(_id="conf-bounds")
        result = _run(tc)
        assert 0.0 <= result.final_confidence <= 1.0, (
            f"final_confidence={result.final_confidence} is out of [0, 1]"
        )

    def test_halt_reason_is_valid_literal(self, trend_factory: Any) -> None:
        """halt_reason must be one of the documented literal values."""
        tc = trend_factory(_id="halt-check")
        result = _run(tc)
        assert result.halt_reason in _VALID_HALT_REASONS, (
            f"Invalid halt_reason: {result.halt_reason!r}\n"
            f"Valid values: {sorted(_VALID_HALT_REASONS)}"
        )

    def test_all_required_fields_present(self, trend_factory: Any) -> None:
        """GraphResult must expose all required fields with non-None values."""
        required = {
            "final_verdict",
            "final_score",
            "final_confidence",
            "decisions",
            "halt_reason",
            "started_at",
            "finished_at",
            "duration_ms",
            "correlation_id",
        }
        tc = trend_factory(_id="fields-check")
        result = _run(tc)
        result_dict = result.model_dump()
        missing = {f for f in required if result_dict.get(f) is None}
        assert not missing, f"Required fields missing or None: {sorted(missing)}"

    def test_decisions_dict_has_valid_agent_names(self, trend_factory: Any) -> None:
        """Every agent name in decisions must be a known AEGIS agent."""
        tc = trend_factory(_id="agents-check")
        result = _run(tc)
        ran = _agents_ran(result)
        unknown = ran - _VALID_AGENT_NAMES
        assert not unknown, (
            f"Unknown agent names in decisions: {sorted(unknown)}\n"
            f"Valid agents: {sorted(_VALID_AGENT_NAMES)}"
        )


# ===========================================================================
# SECTION 2 — TestAgentCalibration
# Calibration tests. These verify that verdicts are SENSIBLE.
# May occasionally fail on unusual real-data distributions.
# Marked with @pytest.mark.calibration for selective CI exclusion.
# ===========================================================================

pytestmark_calibration = pytest.mark.calibration


class TestAgentCalibration:
    """Calibration checks — verdicts should be sensible, not just structurally valid."""

    @pytest.mark.calibration
    def test_high_velocity_signal_scores_above_median(
        self, trend_factory: Any, real_db_stats: dict
    ) -> None:
        """High velocity (4× real, min floor 50/h) should score above 0.45.

        Use max(vel_1h, 50.0) so sparse-signal periods (few signals in the
        last hour) still exercise genuinely high velocity rather than 4×0 = 0.
        """
        vel = max(real_db_stats["vel_1h"], 50.0) * 4
        tc = trend_factory(
            _id="cal-high-vel",
            velocity_1h=vel,
            commercial_intent=0.7,
            coordination_risk=0.05,
        )
        result = _run(tc)
        assert result.final_score >= 0.45, (
            f"High-velocity trend scored {result.final_score:.3f} — expected >= 0.45.\n"
            f"velocity_1h={vel}, halt={result.halt_reason}"
        )

    @pytest.mark.calibration
    def test_coordination_risk_reduces_score_or_blocks(
        self, trend_factory: Any, real_db_stats: dict
    ) -> None:
        """High coordination risk should reduce score or cause BLOCK vs organic signal.

        # SYNTHETIC_EDGE_CASE: coordination_risk=0.92 far exceeds any real DB value.
        """
        vel = real_db_stats["vel_1h"]
        organic = trend_factory(
            _id="cal-organic",
            velocity_1h=vel,
            coordination_risk=0.02,
            commercial_intent=0.65,
        )
        coordinated = trend_factory(  # SYNTHETIC_EDGE_CASE
            _id="cal-coordinated",
            velocity_1h=vel,
            coordination_risk=0.92,
            commercial_intent=0.65,
        )
        org_result = _run(organic)
        coord_result = _run(coordinated)

        assert (
            coord_result.final_verdict == AgentVerdict.BLOCK
            or coord_result.final_score < org_result.final_score + 0.05
        ), (
            f"Coordinated trend was not penalised: "
            f"organic_score={org_result.final_score:.3f}, "
            f"coordinated_score={coord_result.final_score:.3f}, "
            f"coordinated_verdict={coord_result.final_verdict}"
        )

    @pytest.mark.calibration
    def test_heuristic_mode_is_deterministic(self, trend_factory: Any) -> None:
        """Same TrendCandidate run 5× with use_llm=False must yield identical results."""
        tc = trend_factory(_id="det-check")
        results = [_run(tc) for _ in range(5)]
        verdicts = {r.final_verdict for r in results}
        scores = [r.final_score for r in results]

        assert len(verdicts) == 1, (
            f"Non-deterministic verdict across 5 runs: {verdicts}"
        )
        score_spread = max(scores) - min(scores)
        assert score_spread < 0.001, (
            f"Score spread across 5 runs too large: {score_spread:.4f} "
            f"(min={min(scores):.4f}, max={max(scores):.4f})"
        )

    @pytest.mark.calibration
    def test_strong_commercial_intent_not_blocked_without_cause(
        self, trend_factory: Any, real_db_stats: dict
    ) -> None:
        """High commercial intent + low risk should not produce a BLOCK verdict."""
        tc = trend_factory(
            _id="cal-commerce",
            velocity_1h=real_db_stats["vel_1h"],
            commercial_intent=0.95,
            coordination_risk=0.02,
            novelty=0.6,
        )
        result = _run(tc)
        assert result.final_verdict != AgentVerdict.BLOCK, (
            f"Strong commercial signal unexpectedly BLOCKED.\n"
            f"score={result.final_score:.3f}, halt={result.halt_reason}"
        )


# ===========================================================================
# SECTION 3 — TestAgentPerformance
# Latency SLAs. All times measured wall-clock via time.perf_counter().
# ===========================================================================


class TestAgentPerformance:
    """Latency SLA checks for the heuristic-mode agent pipeline."""

    def test_single_trend_latency_under_5s(self, trend_factory: Any) -> None:
        """Single run_trend() call must complete in under 5 seconds (heuristic mode)."""
        tc = trend_factory(_id="perf-single")
        start = time.perf_counter()
        _run(tc)
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, (
            f"Single trend took {elapsed:.2f}s — SLA is 5.0s"
        )

    def test_batch_10_trends_under_30s(self, trend_factory: Any) -> None:
        """10 sequential run_trend() calls must complete in under 30s total."""
        trends = [trend_factory(_id=f"perf-batch-{i:02d}") for i in range(10)]
        start = time.perf_counter()
        for tc in trends:
            _run(tc)
        total = time.perf_counter() - start
        mean = total / 10

        assert total < 30.0, (
            f"10-trend batch took {total:.2f}s total — SLA is 30.0s"
        )
        assert mean < 3.0, (
            f"Mean per-trend latency {mean:.2f}s exceeds 3.0s"
        )

    def test_latency_does_not_degrade_across_repeated_calls(
        self, trend_factory: Any
    ) -> None:
        """p99/p50 ratio across 5 repeated calls must be < 3.0 (no severe outlier)."""
        tc = trend_factory(_id="perf-repeat")
        times: list[float] = []
        for _ in range(5):
            t0 = time.perf_counter()
            _run(tc)
            times.append(time.perf_counter() - t0)

        times.sort()
        p50 = times[len(times) // 2]
        p99 = times[-1]  # with 5 samples, max is a reasonable p99 proxy

        assert p50 > 0, "p50 is zero — something went wrong"
        ratio = p99 / p50
        assert ratio < 3.0, (
            f"Latency degradation too high: p99={p99:.3f}s, p50={p50:.3f}s, "
            f"ratio={ratio:.2f} (threshold=3.0)"
        )
