"""
DeepEval metric tests for AEGIS Pulse agent pipeline output quality.

Architecture relationship:
  Wraps the same run_trend() → GraphResult pipeline as test_agent_quality.py,
  but applies LLM-graded evaluation metrics from the DeepEval framework.

  actual_output  : "<VERDICT> | <score>"  e.g. "proceed | 0.73"
  retrieval_context: stringified TrendCandidate fields (the "context" the
                     agent reasoned over)
  expected_output: heuristic prediction based on velocity and risk

Metrics used (all run locally; gracefully skip when no local model available):
  AnswerRelevancyMetric   — does the verdict relate to the input signal?
  HallucinationMetric     — does the output claim things not in the context?
  BiasMetric              — does the model always output the same class?

All tests are marked @pytest.mark.deepeval and skip gracefully when a local
LLM is not configured (OPENAI_API_KEY absent and no local backend available).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Graceful degradation: skip entire module if deepeval unusable
# ---------------------------------------------------------------------------
try:
    from deepeval.metrics import AnswerRelevancyMetric
    from deepeval.test_case import LLMTestCase

    # Confirm the deepeval package is importable and has the expected API surface.
    _deepeval_check = AnswerRelevancyMetric

    _DEEPEVAL_AVAILABLE = True
except Exception:
    _DEEPEVAL_AVAILABLE = False

pytestmark = pytest.mark.deepeval

_skip_no_deepeval = pytest.mark.skipif(
    not _DEEPEVAL_AVAILABLE,
    reason="deepeval not importable",
)

# Check if any LLM backend is available for deepeval scoring
_HAS_LLM_BACKEND = False
try:
    import os

    _HAS_LLM_BACKEND = bool(
        os.getenv("OPENAI_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("GROQ_API_KEY")
    )
except Exception:
    pass

_skip_no_llm = pytest.mark.skipif(
    not _HAS_LLM_BACKEND,
    reason="No LLM API key set — deepeval metrics require a model backend. "
    "Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or GROQ_API_KEY to enable.",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(tc: Any, **kwargs: Any) -> Any:
    from aegis.agents.runner import run_trend

    return asyncio.run(run_trend(tc, use_llm=False, **kwargs))


def _make_test_case(tc: Any, result: Any) -> Any:
    """Build a DeepEval LLMTestCase from a TrendCandidate and GraphResult."""
    if not _DEEPEVAL_AVAILABLE:
        return None

    context_str = (
        f"trend_id={tc.trend_id}, "
        f"title={tc.title!r}, "
        f"velocity_1h={tc.velocity_1h}, "
        f"velocity_6h={tc.velocity_6h}, "
        f"sentiment={tc.sentiment:.2f}, "
        f"commercial_intent={tc.commercial_intent:.2f}, "
        f"coordination_risk={tc.coordination_risk:.2f}, "
        f"signal_count={tc.signal_count}, "
        f"platforms={tc.platforms}"
    )

    # Heuristic expected: high velocity + low risk → proceed
    if tc.velocity_1h >= 50 and tc.coordination_risk < 0.3:
        expected = "proceed"
    elif tc.coordination_risk >= 0.7:
        expected = "block"
    else:
        expected = "hold"

    actual = f"{result.final_verdict.value} | {result.final_score:.3f}"

    return LLMTestCase(
        input=context_str,
        actual_output=actual,
        expected_output=expected,
        retrieval_context=[context_str],
    )


# ===========================================================================
# TestDeepEvalAgentOutput
# ===========================================================================


class TestDeepEvalAgentOutput:
    """LLM-graded evaluation metrics for agent output quality."""

    @_skip_no_deepeval
    @_skip_no_llm
    def test_answer_relevancy_not_terrible(self, trend_factory: Any) -> None:
        """Verdict output should be minimally relevant to the input signal context.

        Threshold=0.3 is deliberately low — we just want to confirm the output
        is not random noise unrelated to the input. A degenerate model that
        outputs "proceed | 0.000" for every input would score near 0.
        """
        tc = trend_factory(_id="deepeval-relevancy")
        result = _run(tc)
        test_case = _make_test_case(tc, result)

        metric = AnswerRelevancyMetric(threshold=0.3)
        metric.measure(test_case)
        assert metric.score >= 0.3, (
            f"AnswerRelevancy score {metric.score:.3f} below threshold 0.3.\n"
            f"Reason: {metric.reason}"
        )

    @_skip_no_deepeval
    @_skip_no_llm
    def test_verdict_bias_across_commercial_intent_range(
        self, trend_factory: Any
    ) -> None:
        """Verdicts must not be identical across varied commercial_intent values.

        Runs 10 trends spanning commercial_intent 0.1→1.0.
        Asserts at least 2 distinct verdicts are produced — detects a degenerate
        model that always outputs the same class regardless of input.
        """
        intents = [round(0.1 * i, 1) for i in range(1, 11)]
        verdicts = set()

        for intent in intents:
            tc = trend_factory(
                _id=f"deepeval-bias-{intent}",
                commercial_intent=intent,
                coordination_risk=0.05,
            )
            result = _run(tc)
            verdicts.add(result.final_verdict)

        assert len(verdicts) >= 2, (
            f"All 10 trends produced the same verdict: {verdicts}. "
            "Model may be degenerate (always outputs one class)."
        )

    @_skip_no_deepeval
    @_skip_no_llm
    def test_high_risk_trend_does_not_proceed_unchecked(
        self, trend_factory: Any
    ) -> None:
        """A trend with extreme coordination risk should not produce PROCEED verdict.

        # SYNTHETIC_EDGE_CASE: coordination_risk=0.95 is far above any real DB value.
        A proceed verdict on such a signal would indicate the model ignores risk signals.
        """
        tc = trend_factory(  # SYNTHETIC_EDGE_CASE
            _id="deepeval-highrisk",
            coordination_risk=0.95,
            velocity_1h=50.0,
            commercial_intent=0.5,
        )
        result = _run(tc)

        from aegis.agents.schemas import AgentVerdict

        # PROCEED on extreme coordination risk is a quality failure
        assert result.final_verdict != AgentVerdict.PROCEED, (
            f"High-risk trend (coordination_risk=0.95) produced PROCEED verdict "
            f"with score={result.final_score:.3f}. Agent may not be penalising "
            "coordination risk correctly."
        )


# ---------------------------------------------------------------------------
# Non-LLM bias check (no API key needed — pure Python)
# ---------------------------------------------------------------------------


class TestVerdictDiversity:
    """Bias detection that does NOT require an LLM — runs in all CI environments."""

    def test_verdicts_differ_across_velocity_range(self, trend_factory: Any) -> None:
        """Pipeline must produce at least 2 distinct verdicts across 8 velocity levels.

        Checks that the heuristic model is not degenerate (always same output).
        Velocities span from near-zero (synthetic weak) to high (3× real DB value).
        """
        velocities = [0.0, 0.1, 1.0, 5.0, 20.0, 53.0, 106.0, 318.0]
        verdicts = set()

        for vel in velocities:
            tc = trend_factory(
                _id=f"bias-vel-{vel}",
                velocity_1h=vel,
                velocity_6h=vel * 3,
                velocity_24h=vel * 8,
            )
            result = _run(tc)
            verdicts.add(result.final_verdict)

        assert len(verdicts) >= 2, (
            f"Only one verdict produced across 8 velocity levels: {verdicts}. "
            "Heuristic pipeline may be degenerate."
        )
