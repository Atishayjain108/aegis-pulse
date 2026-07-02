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

# ---------------------------------------------------------------------------
# PASS2-2D / BRAIN-4: feedback-weighted ensemble.
#
# Each agent carries an accuracy weight in [0.5, 1.5] (0.5 + realized
# accuracy over the last 30 days of settled prediction_outcomes), computed
# nightly by the autonomous scheduler and shared via the Redis hash below.
# The supervisor multiplies its static role weight by the accuracy weight,
# so when every agent has the same accuracy (or no history exists) the
# aggregation is numerically identical to the historical equal treatment.
# ---------------------------------------------------------------------------

_WEIGHTS_REDIS_KEY = "aegis:agents:accuracy_weights"
_WEIGHTS_REDIS_TTL_S = 7 * 86400
_WEIGHTS_CACHE_TTL_S = 3600.0
_DEFAULT_ACCURACY = 0.5  # no history → weight 0.5 + 0.5 = 1.0 (neutral)

_accuracy_weights: dict[str, float] = {}
_weights_loaded_at: datetime | None = None
_weights_update_ts: datetime | None = None


def get_agent_weight(agent: str) -> float:
    """Accuracy weight for *agent*; 1.0 (neutral) when no history is loaded."""
    return _accuracy_weights.get(agent, 0.5 + _DEFAULT_ACCURACY)


def get_cached_weights() -> dict[str, float]:
    """Snapshot of the currently loaded accuracy weights (may be empty)."""
    return dict(_accuracy_weights)


def reset_agent_weights() -> None:
    """Test/maintenance hook: drop the in-process weight cache."""
    global _accuracy_weights, _weights_loaded_at, _weights_update_ts  # noqa: PLW0603
    _accuracy_weights = {}
    _weights_loaded_at = None
    _weights_update_ts = None


async def refresh_agent_weights(redis_client: Any | None) -> dict[str, float]:
    """Best-effort load of accuracy weights from Redis with a 1-hour cache.

    Called by the runner before each graph invocation. Never raises; on any
    failure (no Redis, empty hash, parse error) the cache is left as-is and
    every agent falls back to the neutral weight 1.0.
    """
    global _weights_loaded_at, _weights_update_ts  # noqa: PLW0603
    now = datetime.now(tz=UTC)
    if (
        _weights_loaded_at is not None
        and (now - _weights_loaded_at).total_seconds() < _WEIGHTS_CACHE_TTL_S
    ):
        return dict(_accuracy_weights)
    if redis_client is None:
        return dict(_accuracy_weights)
    try:
        raw = await redis_client.hgetall(_WEIGHTS_REDIS_KEY)
        if not isinstance(raw, dict):
            return dict(_accuracy_weights)
        loaded: dict[str, float] = {}
        update_ts: datetime | None = None
        for k, v in raw.items():
            key = k.decode() if isinstance(k, bytes) else str(k)
            val = v.decode() if isinstance(v, bytes) else str(v)
            if key == "_updated_at":
                try:
                    update_ts = datetime.fromisoformat(val)
                except ValueError:
                    update_ts = None
                continue
            try:
                loaded[key] = max(0.5, min(1.5, float(val)))
            except ValueError:
                continue
        _accuracy_weights.clear()
        _accuracy_weights.update(loaded)
        _weights_update_ts = update_ts
        _weights_loaded_at = now
        if loaded:
            _log.debug("supervisor.weights_loaded", agents=len(loaded))
    except Exception as exc:
        _log.debug("supervisor.weights_load_failed", error=str(exc))
    return dict(_accuracy_weights)


async def compute_accuracy_weights(
    redis_client: Any,
    pool: Any,
    tenant_id: str,
    *,
    stream: str = "aegis:phase2:graph_results",
    sample: int = 500,
) -> dict[str, float]:
    """PASS2-2D nightly job body: per-agent accuracy → Redis weight hash.

    For each agent A over recent settled outcomes:
        correct = ENTER-vote outcomes with roi > 0
                + HOLD/BLOCK-vote outcomes with roi <= 0
        accuracy_A = correct / total votes   (skipped if no votes matched)
        weight_A   = 0.5 + accuracy_A        (range 0.5 .. 1.5)

    Agent votes come from the Phase 2 graph-results stream ("body" field,
    §1); ground truth from ``prediction_outcomes`` (RLS tenant set, §2).
    Returns the written weights ({} when there is no vote/outcome overlap).
    """
    import json

    # 1. Per-trend agent votes from the Phase 2 stream.
    votes_by_trend: dict[str, dict[str, str]] = {}
    try:
        entries = await redis_client.xrevrange(stream, count=sample)
    except Exception as exc:
        _log.warning("supervisor.weight_update.stream_failed", error=str(exc))
        return {}
    for _entry_id, fields in entries or []:
        body = fields.get(b"body") if isinstance(fields, dict) else None
        if body is None and isinstance(fields, dict):
            body = fields.get("body")
        if not body:
            continue
        try:
            payload = json.loads(body)
            trend_id = str(payload.get("trend_id") or "")
            decisions = payload.get("decisions") or []
            if not trend_id or not decisions:
                continue
            votes = votes_by_trend.setdefault(trend_id, {})
            for d in decisions:
                agent = str(d.get("agent") or "")
                verdict = str(d.get("verdict") or "").lower()
                if agent and verdict:
                    votes[agent] = verdict
        except Exception:
            continue
    if not votes_by_trend:
        return {}

    # 2. Settled ground truth for those trends.
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant', $1, TRUE)", tenant_id
            )
            rows = await conn.fetch(
                """
                SELECT trend_id, roi
                FROM prediction_outcomes
                WHERE settlement_timestamp > NOW() - INTERVAL '30 days'
                  AND trend_id = ANY($1::text[])
                """,
                list(votes_by_trend.keys()),
            )
    except Exception as exc:
        _log.warning("supervisor.weight_update.db_failed", error=str(exc))
        return {}

    roi_by_trend = {str(r["trend_id"]): float(r["roi"]) for r in rows}
    if not roi_by_trend:
        return {}

    # 3. Accuracy per agent.
    correct: dict[str, int] = {}
    total: dict[str, int] = {}
    enter_votes = {"proceed", "enter"}
    for trend_id, votes in votes_by_trend.items():
        roi = roi_by_trend.get(trend_id)
        if roi is None:
            continue
        for agent, verdict in votes.items():
            total[agent] = total.get(agent, 0) + 1
            voted_enter = verdict in enter_votes
            if (voted_enter and roi > 0) or (not voted_enter and roi <= 0):
                correct[agent] = correct.get(agent, 0) + 1
    weights = {
        agent: round(0.5 + correct.get(agent, 0) / n, 4)
        for agent, n in total.items()
        if n > 0
    }
    if not weights:
        return {}

    # 4. Persist for every consumer process.
    try:
        mapping = {a: str(w) for a, w in weights.items()}
        mapping["_updated_at"] = datetime.now(tz=UTC).isoformat()
        await redis_client.hset(_WEIGHTS_REDIS_KEY, mapping=mapping)
        await redis_client.expire(_WEIGHTS_REDIS_KEY, _WEIGHTS_REDIS_TTL_S)
    except Exception as exc:
        _log.warning("supervisor.weight_update.persist_failed", error=str(exc))
        return {}

    _log.info("supervisor.weights_updated", weights=weights)
    return weights


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
    """Return (final_score, final_confidence) blended across agents.

    PASS2-2D: each agent's static role weight is multiplied by its realized
    accuracy weight (0.5–1.5, neutral 1.0). With uniform accuracy weights the
    result is numerically identical to the historical role-weight-only blend
    because the common factor cancels in the normalisation.
    """
    weighted_score = 0.0
    weighted_conf = 0.0
    total_weight = 0.0

    for agent_name, role_weight in _FINAL_WEIGHTS.items():
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

        weight = role_weight * get_agent_weight(agent_name)
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
        agent_weights=get_cached_weights(),
        weight_update_ts=_weights_update_ts,
    )
