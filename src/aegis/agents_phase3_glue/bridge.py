"""
Bridge implementation.

Maps `aegis.predict.inference.InferenceResult` → an `AgentDecision`
that Phase 2's SCOUT and SENTINEL agents can return without losing
any of Phase 3's reasoning, halt_reasons, or audit data.

Mapping:
    PredictionAction.ENTER  → AgentDecision verdict='advance', halt=False
    PredictionAction.HOLD   → AgentDecision verdict='hold',    halt=False
    PredictionAction.OBSERVE→ AgentDecision verdict='hold',    halt=False
    PredictionAction.EXIT   → AgentDecision verdict='exit',    halt=True
    PredictionAction.AVOID  → AgentDecision verdict='block',   halt=True

`confidence` carries through unchanged. `score` uses p_breakout for
SCOUT and p_decline for SENTINEL — those are the dimensions each
agent natively cares about.

Halt reasons from Phase 3 (e.g. `latency_budget_exceeded`,
`predictor_timeout`) are appended verbatim to the agent's
`halt_reasons` so Phase 2's supervisor surfaces them in audit.
"""

from __future__ import annotations

from typing import Any

import structlog

from aegis.predict.inference import InferenceResult
from aegis.predict.schemas import PredictionAction

logger = structlog.get_logger("aegis.agents_phase3_glue.bridge")


# Verdict mapping — kept as a top-level constant so it's editable
# without redeploying. Adding a new PredictionAction means adding a
# row here.
_ACTION_TO_VERDICT: dict[PredictionAction, str] = {
    PredictionAction.ENTER: "advance",
    PredictionAction.HOLD: "hold",
    PredictionAction.OBSERVE: "hold",
    PredictionAction.EXIT: "exit",
    PredictionAction.AVOID: "block",
}

_ACTION_TO_HALT: dict[PredictionAction, bool] = {
    PredictionAction.ENTER: False,
    PredictionAction.HOLD: False,
    PredictionAction.OBSERVE: False,
    PredictionAction.EXIT: True,
    PredictionAction.AVOID: True,
}


def inference_to_agent_decision(
    result: InferenceResult,
    *,
    agent_name: str,
    primary_horizon: int = 24,
) -> dict[str, Any]:
    """Build a Phase-2-shaped AgentDecision dict.

    We return a dict — not the actual `AgentDecision` Pydantic model —
    so this module doesn't import `aegis.agents`. Phase 2's graph
    builder converts it to an AgentDecision via `model_validate(...)`
    inside its own state graph.

    Args:
        result: the Phase 3 inference result.
        agent_name: which Phase 2 agent is wrapping the prediction
            (typically "SCOUT" or "SENTINEL").
        primary_horizon: which horizon's prediction drives the verdict.
            Defaults to 24h.

    Returns:
        dict suitable for `AgentDecision.model_validate(...)`.
    """
    bundle = result.bundle
    primary = bundle.by_horizon(primary_horizon)
    if primary is None and bundle.predictions:
        primary = bundle.predictions[0]

    if primary is None:
        return {
            "agent_name": agent_name,
            "verdict": "hold",
            "halt": False,
            "score": 0.0,
            "confidence": 0.0,
            "reasoning": "no prediction produced (empty bundle)",
            "halt_reasons": list(result.halt_reasons),
            "model_id": bundle.model_id,
            "trend_id": bundle.trend_id,
            "correlation_id": bundle.correlation_id,
        }

    verdict = _ACTION_TO_VERDICT.get(primary.action, "hold")
    halt = _ACTION_TO_HALT.get(primary.action, False)

    # SCOUT cares about acceleration → p_breakout drives score.
    # SENTINEL cares about decay → p_decline drives score.
    if agent_name.upper() == "SENTINEL":
        score = primary.p_decline
        decision_axis = "p_decline"
    else:
        score = primary.p_breakout
        decision_axis = "p_breakout"

    halt_reasons = list(result.halt_reasons)
    if primary.confidence < 0.45:
        halt_reasons.append("low_confidence_below_action_floor")

    causal_summary = ", ".join(f"{a.feature}={a.contribution:+.2f}" for a in result.causal[:3])

    reasoning_parts = [
        f"horizon={primary.horizon_hours}h",
        f"stage={primary.stage.value}",
        f"action={primary.action.value}",
        f"{decision_axis}={score:.3f}",
        f"confidence={primary.confidence:.3f}",
    ]
    if primary.reasoning:
        reasoning_parts.append(f"model_reasoning={primary.reasoning[:200]}")
    if causal_summary:
        reasoning_parts.append(f"causal={causal_summary}")
    if bundle.is_heuristic_only:
        reasoning_parts.append("heuristic_only=True")

    return {
        "agent_name": agent_name,
        "verdict": verdict,
        "halt": halt,
        "score": float(score),
        "confidence": float(primary.confidence),
        "reasoning": " | ".join(reasoning_parts),
        "halt_reasons": halt_reasons,
        "model_id": bundle.model_id,
        "trend_id": bundle.trend_id,
        "correlation_id": bundle.correlation_id,
        # Phase 2 supervisor aggregates these — pass through full bundle
        # JSON so the audit trail is preserved end-to-end.
        "phase3_bundle": bundle.model_dump(mode="json"),
        "phase3_audit": result.audit.model_dump(mode="json") if result.audit else None,
    }


async def enrich_scout_decision(
    state: dict,
    *,
    primary_horizon: int = 24,
) -> dict:
    """Run Phase 3 inference and stamp the result into a SCOUT state.

    Args:
        state: Phase 2 GraphState dict with at minimum
            {trend_id, tenant_id, signals: list[dict]}.
        primary_horizon: which horizon to drive SCOUT's verdict.

    Returns:
        A new state dict with `state["phase3_decision"]` populated.
        The original state is not mutated.
    """
    return await _enrich(state, agent_name="SCOUT", primary_horizon=primary_horizon)


async def enrich_sentinel_decision(
    state: dict,
    *,
    primary_horizon: int = 6,
) -> dict:
    """Run Phase 3 inference and stamp the result into a SENTINEL state.

    SENTINEL defaults to a shorter horizon — its job is detecting
    saturation, which manifests on a 1-6 hour timescale, not 24-72h.
    """
    return await _enrich(state, agent_name="SENTINEL", primary_horizon=primary_horizon)


async def _enrich(
    state: dict,
    *,
    agent_name: str,
    primary_horizon: int,
) -> dict:
    from aegis.predict.inference import InferenceRunner

    trend_id = state.get("trend_id")
    if not trend_id:
        logger.warning("phase3_glue.missing_trend_id", agent=agent_name)
        return state

    runner = state.get("_phase3_runner")
    if runner is None:
        runner = InferenceRunner()
        state = {**state, "_phase3_runner": runner}

    try:
        result = await runner.run(
            tenant_id=state.get("tenant_id", "default"),
            trend_id=trend_id,
            signals=state.get("signals"),
            window=state.get("feature_window"),
        )
    except Exception as exc:
        logger.exception("phase3_glue.inference_failed", agent=agent_name, exc_type=type(exc).__name__)
        return {
            **state,
            "phase3_decision": {
                "agent_name": agent_name,
                "verdict": "hold",
                "halt": True,
                "score": 0.0,
                "confidence": 0.0,
                "reasoning": f"phase3_inference_failed:{type(exc).__name__}",
                "halt_reasons": [f"phase3_inference_failed:{type(exc).__name__}"],
                "trend_id": trend_id,
            },
        }

    decision = inference_to_agent_decision(
        result, agent_name=agent_name, primary_horizon=primary_horizon
    )
    return {**state, "phase3_decision": decision, "phase3_result": result}
