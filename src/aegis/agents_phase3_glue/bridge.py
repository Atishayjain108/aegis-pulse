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

import os
from dataclasses import dataclass, field
from typing import Any

import structlog

from aegis.predict.inference import InferenceResult
from aegis.predict.schemas import PredictionAction

logger = structlog.get_logger("aegis.agents_phase3_glue.bridge")

# CONN-3: agents use the in-process bridge exclusively (see ADR 0016).
# AEGIS_PREDICT_FORCE_HTTP=true is a debugging override that routes the
# enrichment through the HTTP :8100 service instead — useful for verifying
# parity between the two paths. Any HTTP failure falls back to in-process.
_FORCE_HTTP_ENV = "AEGIS_PREDICT_FORCE_HTTP"
_HTTP_TIMEOUT_S = 10.0


def _force_http() -> bool:
    return os.environ.get(_FORCE_HTTP_ENV, "false").strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class _HttpInferenceResult:
    """Duck-typed stand-in for `InferenceResult` built from a /predict response.

    Carries exactly the attributes `inference_to_agent_decision` reads.
    `causal` is empty and `audit` is None — the HTTP envelope exposes only
    summaries of those, and this path exists for debugging parity, not audit.
    """

    bundle: Any
    halt_reasons: tuple[str, ...] = ()
    duration_ms: float = 0.0
    causal: tuple[Any, ...] = ()
    audit: Any = None
    graph_summary: dict[str, float] = field(default_factory=dict)


async def _run_via_http(state: dict, trend_id: str) -> _HttpInferenceResult:
    """POST to the Phase 3 serving API and rebuild a bridge-shaped result.

    Raises on any transport/validation error — the caller falls back to the
    in-process runner.
    """
    import httpx

    from aegis.predict.schemas import PredictionBundle

    base_url = os.environ.get("AEGIS_PREDICT_API_URL", "http://localhost:8100").rstrip("/")
    payload: dict[str, Any] = {
        "tenant_id": state.get("tenant_id", "default"),
        "trend_id": trend_id,
        "signals": state.get("signals"),
    }
    window = state.get("feature_window")
    if window is not None:
        payload["feature_window"] = (
            window.model_dump(mode="json") if hasattr(window, "model_dump") else window
        )
        payload.pop("signals", None)

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
        resp = await client.post(f"{base_url}/predict", json=payload)
        resp.raise_for_status()
    data = resp.json()
    return _HttpInferenceResult(
        bundle=PredictionBundle.model_validate(data["bundle"]),
        halt_reasons=tuple(data.get("halt_reasons", ())),
        duration_ms=float(data.get("duration_ms", 0.0)),
        graph_summary=dict(data.get("graph_summary", {})),
    )


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


async def _persist_prediction(result: Any, *, tenant_id: str | None) -> None:
    """Best-effort write of the inference bundle to the ``predictions`` table.

    Uses the process-wide shared pool (set at startup); a no-op when it is
    unset (e.g. unit tests, or callers that never configured a DB). Never
    raises — Phase 3 output is advisory, persistence is observability only.
    """
    try:
        from aegis.db.pool import get_shared_pool
        from aegis.db.predictions import insert_prediction

        pool = get_shared_pool()
        if pool is None:
            return
        bundle = getattr(result, "bundle", None)
        if bundle is None:
            return
        record = getattr(result, "record", None)
        signature = getattr(record, "signature", "UNSIGNED") if record else "UNSIGNED"
        kwargs: dict[str, Any] = {"signature": signature}
        if tenant_id:
            kwargs["tenant_id"] = str(tenant_id)
        await insert_prediction(pool, bundle, **kwargs)
    except Exception as exc:  # never break inference on a persistence error
        # STAGE 1.4: DEBUG here hid an always-empty predictions table for the
        # project's entire history (set_shared_pool had zero call sites). A
        # missing pool outside tests is a wiring failure — say so at ERROR.
        import os

        level = logger.debug if os.environ.get("AEGIS_ENV") == "test" else logger.error
        level("phase3_glue.persist_skipped", error=str(exc)[:160])


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

    # CONN-3 debugging override: route through HTTP :8100 when forced.
    # Falls back to the canonical in-process path on any HTTP failure.
    http_result: _HttpInferenceResult | None = None
    if _force_http():
        try:
            http_result = await _run_via_http(state, trend_id)
            logger.info(
                "phase3_glue.http_override_used",
                agent=agent_name,
                trend_id=trend_id,
                duration_ms=http_result.duration_ms,
            )
        except Exception as exc:
            logger.warning(
                "phase3_glue.http_override_failed_falling_back",
                agent=agent_name,
                error=str(exc)[:200],
            )

    # Guard: InferenceRunner.run() requires non-empty signals OR a feature window.
    # When dedup + the confidence/stale-data gate drain the batch, both are
    # empty/None and runner.run() would raise ValueError. Degrade gracefully to
    # an OBSERVE no-op instead of crashing the SCOUT/SENTINEL node.
    _signals = state.get("signals")
    _window = state.get("feature_window")
    if http_result is None and not _signals and _window is None:
        logger.warning(
            "phase3_glue.no_input_data",
            agent=agent_name,
            trend_id=trend_id,
        )
        return {
            **state,
            "phase3_decision": {
                "agent_name": agent_name,
                "verdict": "hold",
                "halt": False,
                "score": 0.0,
                "confidence": 0.0,
                "reasoning": "phase3_skipped:no_signal_data",
                "halt_reasons": [],
                "trend_id": trend_id,
            },
        }

    try:
        result = http_result or await runner.run(
            tenant_id=state.get("tenant_id", "default"),
            trend_id=trend_id,
            signals=_signals,
            window=_window,
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

    # OMEGA: persist the prediction bundle to the `predictions` table. For the
    # entire project history this was built and discarded, leaving Phase 3 dead
    # weight (predictions table empty). Best-effort, fail-open — a write error
    # must never break the SCOUT/SENTINEL node. Idempotent on correlation_id.
    await _persist_prediction(result, tenant_id=state.get("tenant_id"))

    decision = inference_to_agent_decision(
        result, agent_name=agent_name, primary_horizon=primary_horizon
    )
    # Strip the full bundle/audit objects from the state-carried dict.
    # They inflate LangGraph state significantly; the full data lives in
    # phase3_result for any caller that needs the complete audit trail.
    _STATE_STRIP = {"phase3_bundle", "phase3_audit"}
    compact_decision = {k: v for k, v in decision.items() if k not in _STATE_STRIP}
    return {**state, "phase3_decision": compact_decision, "phase3_result": result}
