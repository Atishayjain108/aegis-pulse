"""
Supervisor — terminal aggregation node.

The supervisor is the *last* node in the LangGraph; it reads the full
state (decisions list + per-agent fields) and produces the final
verdict, priority, score, and halt reason. It does not call any
external services — it's a pure function of the accumulated state.

This module also exports `decide_priority(state)` and
`compute_halt_reason(state)` as standalone helpers so they can be
tested independently of the graph.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from .schemas import AgentDecision, AgentVerdict, GraphResult, Priority, TrendCandidate

if TYPE_CHECKING:
    from .state import GraphState

_log = structlog.get_logger("aegis.agents.supervisor")


# Final-score weights. Pure SCOUT confidence is not enough — we want
# the score to reflect end-to-end conviction including auditor margin
# headroom and narrative strength.
_FINAL_WEIGHTS: dict[str, float] = {
    "scout": 0.35,
    "narrative": 0.15,
    "geo_arbitrage": 0.10,
    "auditor": 0.30,
    "red_team": 0.10,
}


def _decision_for(state: GraphState, agent: str) -> AgentDecision | None:
    """Find the most recent decision for a given agent name."""
    decisions = state.get("decisions", []) or []
    for d in reversed(decisions):
        if d.agent == agent:
            return d
    return None


def _auditor_score(state: GraphState) -> float:
    """Map the auditor's p10 margin to a [0,1] score.

    p10 ≤ 0  → 0.0
    p10 ≥ $5 → 1.0
    Linear in between.
    """
    p10 = float(state.get("auditor_margin_p10", 0.0) or 0.0)
    if p10 <= 0.0:
        return 0.0
    return min(1.0, p10 / 5.0)


def compute_priority(state: GraphState) -> Priority:
    """Pick the routing priority for the final alert."""
    # Sentinel exit signal trumps the breakout flow.
    if bool(state.get("sentinel_recommended_exit", False)):
        return Priority.P1_EXIT

    scout_verdict = state.get("scout_verdict")
    scout_score = float(state.get("scout_score", 0.0) or 0.0)
    red_team_passed = bool(state.get("red_team_passed", True))
    compliance_passed = bool(state.get("compliance_passed", True))
    hedge_passed = bool(state.get("hedge_passed", True))

    if (
        scout_verdict is AgentVerdict.PROCEED
        and scout_score >= 0.80
        and red_team_passed
        and compliance_passed
        and hedge_passed
    ):
        return Priority.P0_BREAKOUT

    if scout_verdict is AgentVerdict.PROCEED:
        return Priority.P2_OPPORTUNITY

    return Priority.P3_HOUSEKEEPING


def compute_final_verdict(state: GraphState) -> AgentVerdict:
    """Final verdict respecting hard vetoes."""
    blocked = state.get("blocked_by", []) or []
    if blocked:
        return AgentVerdict.BLOCK

    if state.get("compliance_passed") is False:
        return AgentVerdict.BLOCK
    if state.get("red_team_passed") is False:
        return AgentVerdict.BLOCK
    if state.get("hedge_passed") is False:
        return AgentVerdict.BLOCK

    scout_verdict = state.get("scout_verdict", AgentVerdict.HOLD)
    return scout_verdict if isinstance(scout_verdict, AgentVerdict) else AgentVerdict.HOLD


def compute_halt_reason(state: GraphState) -> str:
    """Halt reason matches the GraphResult Literal.

    Priority: scout_below_threshold first because a SCOUT BLOCK is
    the *root* cause for everything downstream — red_team and
    compliance might add themselves to blocked_by because they read
    `scout_score < 0.55`, but those are *consequences* of the scout
    block, not independent vetoes. Surfacing scout first gives the
    clearest signal to operators.
    """
    scout_verdict = state.get("scout_verdict")
    if scout_verdict is AgentVerdict.BLOCK:
        return "scout_below_threshold"

    blocked = state.get("blocked_by", []) or []
    if "compliance" in blocked or state.get("compliance_passed") is False:
        return "blocked_by_compliance"
    if "red_team" in blocked or state.get("red_team_passed") is False:
        return "vetoed_by_red_team"
    if "hedge" in blocked or state.get("hedge_passed") is False:
        return "vetoed_by_hedge"

    sourcer_decision = _decision_for(state, "sourcer")
    if (
        scout_verdict is AgentVerdict.PROCEED
        and sourcer_decision is not None
        and sourcer_decision.verdict is AgentVerdict.BLOCK
    ):
        return "no_supplier"

    return "completed"


def compute_final_score(state: GraphState) -> tuple[float, float]:
    """Return (final_score, final_confidence) blended across agents."""
    weighted_score = 0.0
    weighted_conf = 0.0
    total_weight = 0.0

    for agent_name, weight in _FINAL_WEIGHTS.items():
        if agent_name == "auditor":
            score = _auditor_score(state)
            decision = _decision_for(state, agent_name)
            confidence = decision.confidence if decision else 0.5
        else:
            decision = _decision_for(state, agent_name)
            if decision is None:
                continue
            score = decision.score
            confidence = decision.confidence

        weighted_score += weight * score
        weighted_conf += weight * confidence
        total_weight += weight

    if total_weight <= 0:
        return 0.0, 0.0
    return (
        max(0.0, min(1.0, weighted_score / total_weight)),
        max(0.0, min(1.0, weighted_conf / total_weight)),
    )


def finalize(state: GraphState) -> dict[str, Any]:
    """Terminal node: writes the final aggregate fields onto the state."""
    final_verdict = compute_final_verdict(state)
    final_priority = compute_priority(state)
    final_score, final_confidence = compute_final_score(state)
    halt_reason = compute_halt_reason(state)

    _log.info(
        "supervisor.finalize",
        trend_id=state.get("trend_id"),
        verdict=final_verdict.value,
        priority=final_priority.value,
        score=round(final_score, 3),
        confidence=round(final_confidence, 3),
        halt=halt_reason,
        decisions=len(state.get("decisions", []) or []),
    )

    return {
        "final_verdict": final_verdict,
        "final_priority": final_priority,
        "final_score": final_score,
        "final_confidence": final_confidence,
        "halt_reason": halt_reason,
    }


def build_graph_result(
    state: GraphState,
    *,
    started_at: datetime,
    finished_at: datetime | None = None,
) -> GraphResult:
    """Build the immutable GraphResult from a finalized state."""
    finished = finished_at or datetime.now(tz=UTC)
    duration_ms = max(0.0, (finished - started_at).total_seconds() * 1000.0)

    halt = state.get("halt_reason", "completed")
    valid_halts = {
        "completed",
        "vetoed_by_red_team",
        "vetoed_by_hedge",
        "blocked_by_compliance",
        "scout_below_threshold",
        "no_supplier",
        "exception",
        "timeout",
    }
    if halt not in valid_halts:
        halt = "completed"

    _candidate: TrendCandidate | None = state.get("candidate")
    _trend_id = state.get("trend_id") or (
        _candidate.trend_id if _candidate is not None else "unknown"
    )
    _correlation_id = state.get("correlation_id") or (
        _candidate.correlation_id
        if _candidate is not None
        else "00000000-0000-0000-0000-000000000000"
    )

    _decisions = list(state.get("decisions", []) or [])
    # Provenance (HALLU-1): aggregate per-agent used_llm into one verdict-level signal.
    _llm_count = sum(1 for d in _decisions if getattr(d, "used_llm", False))
    if _llm_count == 0:
        _source: str = "heuristic"
    elif _llm_count == len(_decisions):
        _source = "llm"
    else:
        _source = "mixed"

    return GraphResult(
        trend_id=_trend_id,
        correlation_id=_correlation_id,
        final_verdict=state.get("final_verdict", AgentVerdict.HOLD),
        final_priority=state.get("final_priority", Priority.P3_HOUSEKEEPING),
        final_score=float(state.get("final_score", 0.0)),
        final_confidence=float(state.get("final_confidence", 0.0)),
        decisions=_decisions,
        blocked_by=list(state.get("blocked_by", []) or []),
        started_at=started_at,
        finished_at=finished,
        duration_ms=duration_ms,
        halt_reason=halt,  # type: ignore[arg-type]
        llm_used=_llm_count > 0,
        reasoning_source=_source,  # type: ignore[arg-type]
    )
