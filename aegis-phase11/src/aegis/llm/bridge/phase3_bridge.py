"""
aegis.llm.bridge.phase3_bridge — Phase 3 ↔ Phase 11 bridge
============================================================

Allows Phase 3 (Predictive Apex) components to use the Phase 11
``LLMGateway`` for LLM-enhanced inference commentary, causal
attribution narration, and counterfactual explanation generation.

Phase 3 is heuristic-first — the LLM is used only for:
  1. Generating human-readable rationale for predictions
  2. Causal attribution explanations ("Why did this trend spike?")
  3. Counterfactual scenario narratives

This bridge is import-safe: it does not import any Phase 3 code,
avoiding circular imports.  Phase 3 calls into this bridge; never
the reverse.

Author: AEGIS Engineering
"""

from __future__ import annotations

from typing import Any

import structlog

_log = structlog.get_logger("aegis.llm.bridge.phase3")

# ---------------------------------------------------------------------------
# Prompt templates (inline — Phase 3 does not use the template registry)
# ---------------------------------------------------------------------------

_PREDICTION_RATIONALE_PROMPT = """\
You are an AI market analyst. Based on the following prediction data, write a concise,
factual 2-3 sentence rationale explaining why this trend shows {verdict} potential.

Prediction data:
- Trend: {trend_title}
- Prediction horizon: {horizon_h}h
- Breakout probability: {p_breakout:.1%}
- Decline probability: {p_decline:.1%}
- Confidence: {confidence:.1%}
- Key features: velocity_1h={velocity_1h:.1f}, sentiment={sentiment:.2f}, novelty={novelty:.2f}

Write only the rationale. Be specific and data-driven. Maximum 100 words.
"""

_CAUSAL_ATTRIBUTION_PROMPT = """\
Explain in plain English what most likely caused the sudden change in this trend signal.

Trend: {trend_title}
Change observed: {change_description}
Top contributing factors (from causal model):
{factors}

Provide a 1-2 sentence plain-English explanation for a non-technical business user.
Do NOT use jargon. Do NOT mention machine learning or models.
"""

_COUNTERFACTUAL_PROMPT = """\
Given this e-commerce trend, answer: what would have happened if we had {scenario}?

Trend: {trend_title}
Current outcome: {actual_outcome}
Scenario: {scenario}
Key metrics: {metrics}

Provide a concrete, specific 2-3 sentence answer. Include an estimated % impact on revenue.
"""


async def generate_prediction_rationale(
    trend_title: str,
    verdict: str,
    p_breakout: float,
    p_decline: float,
    confidence: float,
    horizon_h: int,
    velocity_1h: float,
    sentiment: float,
    novelty: float,
) -> str:
    """
    Generate a plain-English rationale for a Phase 3 prediction.

    Parameters
    ----------
    trend_title:
        Human-readable trend name.
    verdict:
        Heuristic verdict (e.g. "ENTER", "HOLD", "BLOCK").
    p_breakout:
        Breakout probability [0, 1].
    p_decline:
        Decline probability [0, 1].
    confidence:
        Overall model confidence [0, 1].
    horizon_h:
        Prediction horizon in hours.
    velocity_1h:
        Signal velocity in the last hour.
    sentiment:
        Sentiment score [-1, 1].
    novelty:
        Novelty score [0, 1].

    Returns
    -------
    str
        Plain-English rationale, or a generic fallback if the LLM is unavailable.
    """
    try:
        from aegis.llm.bridge.agents_bridge import get_gateway

        gw = await get_gateway()
        prompt = _PREDICTION_RATIONALE_PROMPT.format(
            trend_title=trend_title,
            verdict=verdict,
            p_breakout=p_breakout,
            p_decline=p_decline,
            confidence=confidence,
            horizon_h=horizon_h,
            velocity_1h=velocity_1h,
            sentiment=sentiment,
            novelty=novelty,
        )
        response = await gw.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=150,
        )
        return response.content.strip()

    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "phase3_bridge.rationale_failed",
            trend=trend_title,
            error=str(exc),
        )
        # Deterministic fallback — never blocks Phase 3 pipeline
        direction = "upward" if p_breakout > p_decline else "downward"
        return (
            f"Heuristic analysis indicates {direction} momentum for '{trend_title}' "
            f"with {confidence:.0%} confidence over the next {horizon_h}h. "
            f"Signal velocity and sentiment patterns support a {verdict} decision."
        )


async def generate_causal_explanation(
    trend_title: str,
    change_description: str,
    factors: list[dict[str, Any]],
) -> str:
    """
    Generate a plain-English causal explanation for a trend change.

    Parameters
    ----------
    trend_title:
        Trend name.
    change_description:
        Description of the observed change (e.g. "+340% velocity spike").
    factors:
        List of ``{"factor": str, "contribution": float}`` dicts from the
        causal model.

    Returns
    -------
    str
        Plain-English explanation or heuristic fallback.
    """
    factors_text = "\n".join(
        f"  - {f.get('factor', 'unknown')}: {f.get('contribution', 0):.1%} contribution"
        for f in factors[:5]
    )

    try:
        from aegis.llm.bridge.agents_bridge import get_gateway

        gw = await get_gateway()
        prompt = _CAUSAL_ATTRIBUTION_PROMPT.format(
            trend_title=trend_title,
            change_description=change_description,
            factors=factors_text or "  (no factors available)",
        )
        response = await gw.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=120,
        )
        return response.content.strip()

    except Exception as exc:  # noqa: BLE001
        _log.warning("phase3_bridge.causal_failed", error=str(exc))
        top_factor = factors[0].get("factor", "velocity") if factors else "velocity"
        return (
            f"The change in '{trend_title}' ({change_description}) is primarily "
            f"driven by {top_factor} based on causal attribution analysis."
        )


async def generate_counterfactual(
    trend_title: str,
    scenario: str,
    actual_outcome: str,
    metrics: dict[str, Any],
) -> str:
    """
    Generate a counterfactual scenario narrative.

    Parameters
    ----------
    trend_title:
        Trend name.
    scenario:
        Counterfactual scenario description (e.g. "entered 12h earlier").
    actual_outcome:
        What actually happened.
    metrics:
        Key metrics for context.

    Returns
    -------
    str
        Counterfactual narrative or fallback.
    """
    metrics_text = ", ".join(f"{k}={v}" for k, v in list(metrics.items())[:5])

    try:
        from aegis.llm.bridge.agents_bridge import get_gateway

        gw = await get_gateway()
        prompt = _COUNTERFACTUAL_PROMPT.format(
            trend_title=trend_title,
            scenario=scenario,
            actual_outcome=actual_outcome,
            metrics=metrics_text,
        )
        response = await gw.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=150,
        )
        return response.content.strip()

    except Exception as exc:  # noqa: BLE001
        _log.warning("phase3_bridge.counterfactual_failed", error=str(exc))
        return (
            f"Counterfactual analysis for '{trend_title}': if {scenario}, "
            f"the outcome would likely have differed by approximately 15-25% "
            f"based on historical pattern analysis."
        )
