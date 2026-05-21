"""
Runner — public entry point.

`run_trend(candidate)` is the one-call API for the rest of the system:

    >>> from aegis.agents import runner
    >>> result = await runner.run_trend(candidate)
    >>> result.final_verdict
    AgentVerdict.PROCEED

The runner:
    * Lazily compiles the graph (cached singleton per (use_llm, ...)).
    * Catches all exceptions and converts them to a GraphResult with
      `halt_reason="exception"`. A graph never raises out.
    * Enforces a wall-clock timeout on the whole traversal.
    * Persists a snapshot of the final state to MinIO if a snapshot
      manager is configured (best-effort, non-blocking on failure).

The runner is deliberately simple. The full production scheduler
(priority queue, leader election, parallel candidates) lives one
layer above this in the supervisor service.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from aegis.scrape.swarm_result import SwarmResult

from .schemas import (
    AgentVerdict,
    GraphResult,
    Priority,
    TrendCandidate,
)
from .state import GraphState, initial_state
from .supervisor import build_graph_result

if TYPE_CHECKING:  # pragma: no cover
    from .llm.router import LLMRouter
    from .memory.chroma_store import ChromaMemoryStore
    from .memory.shared_memory import SharedWorkingMemory
    from .memory.snapshots import SnapshotManager


_log = structlog.get_logger("aegis.agents.runner")


# Compiled-graph cache. Keyed by a small tuple of construction params
# so most callers reuse a single compiled graph.
_graph_cache: dict[tuple[Any, ...], Any] = {}
_graph_lock = asyncio.Lock()


async def _get_graph(
    *,
    llm_router: LLMRouter | None,
    historian_store: ChromaMemoryStore | None,
    shared_memory: SharedWorkingMemory | None,
    use_llm: bool,
) -> Any:
    """Lazy-compile the graph, caching by parameter tuple."""
    cache_key = (id(llm_router), id(historian_store), id(shared_memory), bool(use_llm))
    if cache_key in _graph_cache:
        return _graph_cache[cache_key]
    async with _graph_lock:
        if cache_key in _graph_cache:
            return _graph_cache[cache_key]
        # Lazy-import build_graph so the runner module remains
        # importable even when langgraph is not yet installed.
        from .graph import build_graph

        compiled = build_graph(
            llm_router=llm_router,
            historian_store=historian_store,
            shared_memory=shared_memory,
            use_llm=use_llm,
        )
        _graph_cache[cache_key] = compiled
        return compiled


async def reset_graph_cache() -> None:
    """Test-only helper: tear down the compiled-graph cache."""
    async with _graph_lock:
        _graph_cache.clear()


async def run_trend(
    candidate: TrendCandidate,
    *,
    tenant_id: str = "default",
    signals: list[dict] | None = None,
    llm_router: LLMRouter | None = None,
    historian_store: ChromaMemoryStore | None = None,
    shared_memory: SharedWorkingMemory | None = None,
    snapshot_manager: SnapshotManager | None = None,
    use_llm: bool = True,
    timeout_s: float = 120.0,
    stream_client: Any | None = None,
) -> GraphResult:
    """Execute the agent graph against `candidate` and return the result.

    Never raises. On any failure the result has `halt_reason="exception"`
    or `"timeout"` so callers can route the candidate to the dead-letter
    queue without try/except.
    """
    started_at = datetime.now(tz=UTC)

    # Fetch latest SwarmResult from Redis for cross-platform market context.
    # Non-blocking: any failure is logged and pipeline continues with None.
    swarm_context: SwarmResult | None = None
    if stream_client is not None:
        try:
            raw = await stream_client.get("aegis:swarm:latest")
            if raw:
                swarm_context = SwarmResult.model_validate_json(raw)
        except Exception as exc:
            _log.warning("swarm_context_fetch_failed", error=str(exc))

    state: GraphState = initial_state(
        candidate, tenant_id=tenant_id, signals=signals, swarm_context=swarm_context
    )

    try:
        compiled = await _get_graph(
            llm_router=llm_router,
            historian_store=historian_store,
            shared_memory=shared_memory,
            use_llm=use_llm,
        )
    except Exception as exc:
        _log.exception("runner.graph_compile_failed", trend_id=candidate.trend_id)
        return _build_exception_result(state, started_at, error=str(exc), halt="exception")

    try:
        final_state = await asyncio.wait_for(
            compiled.ainvoke(state),
            timeout=float(timeout_s),
        )
    except TimeoutError:
        _log.warning("runner.timeout", trend_id=candidate.trend_id, timeout_s=timeout_s)
        return _build_exception_result(
            state,
            started_at,
            error=f"graph traversal exceeded {timeout_s}s",
            halt="timeout",
        )
    except Exception as exc:
        _log.exception("runner.graph_failed", trend_id=candidate.trend_id)
        return _build_exception_result(state, started_at, error=str(exc), halt="exception")

    # `ainvoke` returns the merged final state dict.
    if not isinstance(final_state, dict):
        _log.error(
            "runner.unexpected_state_type",
            trend_id=candidate.trend_id,
            type=type(final_state).__name__,
        )
        return _build_exception_result(
            state,
            started_at,
            error=f"unexpected state type {type(final_state).__name__}",
            halt="exception",
        )

    result = build_graph_result(final_state, started_at=started_at)

    # Best-effort snapshot. Never blocks on failure.
    if snapshot_manager is not None:
        try:
            await snapshot_manager.write(
                tenant_id=tenant_id,
                correlation_id=result.correlation_id,
                payload=result.model_dump(mode="json"),
            )
        except Exception:  # pragma: no cover
            _log.exception("runner.snapshot_failed", trend_id=candidate.trend_id)

    # Best-effort Phase 4 stream publish — never raises.
    if stream_client is not None:
        try:
            await _publish_phase2_result(stream_client, result, tenant_id)
        except Exception:
            _log.warning("runner.stream_publish_failed", trend_id=candidate.trend_id)

    data_confidence_log: float = getattr(result, "data_confidence", 1.0)
    _log.info(
        "runner.completed",
        trend_id=candidate.trend_id,
        verdict=result.final_verdict.value,
        priority=int(result.final_priority),
        score=round(result.final_score, 3),
        confidence=round(result.final_confidence, 3),
        data_confidence=round(data_confidence_log, 3),
        halt=result.halt_reason,
        decisions=len(result.decisions),
        duration_ms=round(result.duration_ms, 1),
    )
    return result


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


# Maps Phase 2 AgentVerdict values to Phase 4's execution vocabulary.
# Phase 4 composer expects ENTER/HOLD/EXIT/BLOCK; AgentVerdict uses proceed/hold/block/escalate.
_VERDICT_TO_PHASE4: dict[str, str] = {
    "proceed": "ENTER",
    "hold": "HOLD",
    "block": "BLOCK",
    "escalate": "HOLD",  # escalation has no Phase 4 equivalent; conservatively hold
}


async def _publish_phase2_result(
    redis_client: Any,
    result: GraphResult,
    tenant_id: str,
) -> None:
    """XADD a GraphResult summary to the Phase 4 intake stream."""
    raw_verdict = result.final_verdict.value
    phase4_verdict = _VERDICT_TO_PHASE4.get(raw_verdict, "HOLD")
    # Pull data_confidence from result metadata if the runner attached it.
    # Falls back to 1.0 (assume clean) when not set — avoids breaking old
    # callers that do not run the confidence gate.
    data_confidence: float = getattr(result, "data_confidence", 1.0)

    payload = {
        # Phase 4 intake fields (verdict mapped to P4 vocabulary)
        "trend_id": result.trend_id,
        "final_verdict": phase4_verdict,
        "final_score": result.final_score,
        "final_confidence": result.final_confidence,
        "final_priority": int(result.final_priority),
        "halt_reason": result.halt_reason,
        "blocked_by": list(result.blocked_by),
        "correlation_id": result.correlation_id,
        "decision_window": "default",
        "tenant_id": tenant_id,
        # Dashboard display fields (raw verdict + per-agent breakdown)
        "raw_verdict": raw_verdict,
        "started_at": result.started_at.isoformat() if result.started_at else None,
        "finished_at": result.finished_at.isoformat() if result.finished_at else None,
        "duration_ms": result.duration_ms,
        # Phase 5: data quality confidence from the scrape confidence gate
        "data_confidence": round(data_confidence, 4),
        "decisions": [
            {
                "agent": d.agent,
                "verdict": d.verdict.value,
                "score": d.score,
                "confidence": d.confidence,
                "reasoning": d.reasoning,
                "used_llm": d.used_llm,
                "duration_ms": d.duration_ms,
            }
            for d in result.decisions
        ],
    }
    body = json.dumps(payload, default=str)
    await redis_client.xadd("aegis:phase2:graph_results", {"body": body})


def _build_exception_result(
    state: GraphState,
    started_at: datetime,
    *,
    error: str,
    halt: str,
) -> GraphResult:
    """Synthesize a GraphResult when the graph itself failed.

    Defensive: if the candidate state is malformed we still emit a
    valid GraphResult so callers never have to handle an exception.
    """
    finished_at = datetime.now(tz=UTC)
    duration_ms = max(0.0, (finished_at - started_at).total_seconds() * 1000.0)

    candidate = state.get("candidate")
    trend_id = candidate.trend_id if candidate is not None else state.get("trend_id", "unknown")
    correlation_id = (
        candidate.correlation_id
        if candidate is not None
        else state.get("correlation_id", "00000000-0000-0000-0000-000000000000")
    )

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
    halt_norm = halt if halt in valid_halts else "exception"

    return GraphResult(
        trend_id=trend_id,
        correlation_id=correlation_id,
        final_verdict=AgentVerdict.HOLD,
        final_priority=Priority.P3_HOUSEKEEPING,
        final_score=0.0,
        final_confidence=0.0,
        decisions=list(state.get("decisions", []) or []),
        blocked_by=list(state.get("blocked_by", []) or []),
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        halt_reason=halt_norm,  # type: ignore[arg-type]
    )
