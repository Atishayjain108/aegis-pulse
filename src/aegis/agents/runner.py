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
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from aegis.schemas import SwarmResult

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

# ---------------------------------------------------------------------------
# Langfuse LLM tracing — optional; degrades gracefully when unavailable.
# Uses the native Langfuse Python SDK (no langchain dependency).
# Pattern mirrors HARDEN_AVAILABLE / _langfuse_available from harden_shim.py.
# ---------------------------------------------------------------------------
try:
    import os as _os

    from langfuse import Langfuse as _Langfuse  # type: ignore[import-untyped]

    _langfuse_available: bool = bool(
        _os.getenv("LANGFUSE_PUBLIC_KEY") and _os.getenv("LANGFUSE_SECRET_KEY")
    )
except Exception:
    _Langfuse = None  # type: ignore[assignment,misc]
    _langfuse_available: bool = False  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Causal explainer — optional; degrades gracefully when unavailable.
# Same pattern as Langfuse block above.
# ---------------------------------------------------------------------------
try:
    from aegis.predict.causal.explainer import generate_explanation as _gen_exp

    _explainer_available: bool = True
except Exception:
    _gen_exp = None  # type: ignore[assignment]
    _explainer_available: bool = False  # type: ignore[no-redef]


def _make_langfuse_trace(trend_id: str, candidate: TrendCandidate) -> tuple[Any, Any] | tuple[None, None]:
    """Return (langfuse_client, trace) or (None, None) when unavailable.

    The trace captures the full TrendCandidate as input so Langfuse shows
    the exact signal values that drove each pipeline run.
    """
    if not _langfuse_available or _Langfuse is None:
        return None, None
    try:
        import os

        lf = _Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
        trace = lf.trace(
            name="aegis.run_trend",
            input={
                "trend_id": trend_id,
                "title": candidate.title,
                "velocity_1h": candidate.velocity_1h,
                "velocity_6h": candidate.velocity_6h,
                "commercial_intent": candidate.commercial_intent,
                "coordination_risk": candidate.coordination_risk,
                "signal_count": candidate.signal_count,
                "platforms": candidate.platforms,
            },
            session_id=trend_id,
            tags=["aegis-pulse", "phase2-agents"],
        )
        return lf, trace
    except Exception as exc:
        _log.debug("langfuse.trace_init_failed", error=str(exc))
        return None, None


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

    # Buyer-demand stamp: fetch Google Trends ONCE per query here (harvest/entry)
    # and stash it on the candidate so SCOUT reads the stamped value instead of
    # re-querying Trends per node and getting 429'd. Fail-open + auto-disabled
    # under AEGIS_ENV=test (see aegis.agents.nodes._demand.stamp_demand).
    try:
        from aegis.agents.nodes._demand import stamp_demand

        await stamp_demand(candidate)
    except Exception as exc:
        _log.debug("runner.demand_stamp_failed", error=type(exc).__name__)

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

    # PASS2-2D: best-effort accuracy-weight refresh (1-hour in-process cache;
    # neutral weights when Redis or the hash is absent). Never raises.
    try:
        from aegis.agents.supervisor import refresh_agent_weights

        await refresh_agent_weights(stream_client)
    except Exception as exc:
        _log.debug("agent_weights_refresh_failed", error=str(exc))

    state: GraphState = initial_state(
        candidate, tenant_id=tenant_id, signals=signals, swarm_context=swarm_context
    )

    # Langfuse native trace — (client, trace) or (None, None) when not configured.
    _lf_client, _lf_trace = _make_langfuse_trace(candidate.trend_id, candidate)

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

    # PASS6-6A: attach raw signal metadata for the deep-verify checks, then run
    # the second-pass verification on P0 results (score >= 0.85). Never raises;
    # a verification failure downgrades to P1, it never blocks the result.
    try:
        result = result.model_copy(update={
            "trend_data": {
                "velocity_1h": candidate.velocity_1h,
                "velocity_6h": candidate.velocity_6h,
                "velocity_24h": candidate.velocity_24h,
                "platforms": list(candidate.platforms),
                "signal_count": candidate.signal_count,
                "unique_authors": candidate.unique_authors,
            },
        })
        result = await _deep_verify_high_confidence_result(result)
    except Exception as exc:
        _log.warning("deep_verify.error", trend_id=candidate.trend_id, error=str(exc))

    # Best-effort causal explanation — non-blocking; never affects verdict.
    if _explainer_available and _gen_exp is not None:
        try:
            tc = candidate
            exp, cf, drivers = _gen_exp(
                trend_id=tc.trend_id,
                verdict=_VERDICT_TO_PHASE4.get(result.final_verdict.value, "HOLD"),
                score=result.final_score,
                confidence=result.final_confidence,
                velocity_1h=tc.velocity_1h,
                velocity_6h=tc.velocity_6h,
                velocity_24h=tc.velocity_24h,
                sentiment=tc.sentiment,
                commercial_intent=tc.commercial_intent,
                novelty=tc.novelty,
                coordination_risk=tc.coordination_risk,
                signal_count=tc.signal_count,
                unique_authors=tc.unique_authors,
                platforms=list(tc.platforms),
            )
            result = result.model_copy(update={
                "explanation": exp,
                "counterfactual": cf,
                "primary_drivers": drivers,
            })
        except Exception as exc:
            _log.warning("explainer.failed", trend_id=candidate.trend_id, error=str(exc))

    # Best-effort Langfuse trace finalisation — non-blocking.
    if _lf_trace is not None and _lf_client is not None:
        try:
            _lf_trace.update(
                output={
                    "verdict": result.final_verdict.value,
                    "score": round(result.final_score, 4),
                    "confidence": round(result.final_confidence, 4),
                    "halt_reason": result.halt_reason,
                    "agents_ran": [d.agent for d in result.decisions],
                },
            )
            _lf_client.flush()
            trace_url = _lf_trace.get_trace_url()
            _log.debug("langfuse.trace", trace_url=trace_url, trend_id=candidate.trend_id)
        except Exception:
            pass  # tracing is best-effort; never affect the result

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


# ----------------------------------------------------------------------
# PASS6-6A: deep verification of high-confidence (P0) results
# ----------------------------------------------------------------------

# Score floor for the P0 deep-verify gate; results below it pass through.
_DEEP_VERIFY_THRESHOLD = 0.85


async def _deep_verify_high_confidence_result(result: GraphResult) -> GraphResult:
    """Second-pass verification for P0 signals (score >= 0.85).

    Runs 3 independent verification checks:
      1. Temporal consistency: velocity pattern consistent with organic trend?
      2. Cross-source confirmation: at least 2 independent platforms agree?
      3. Red-team challenge: does the RED_TEAM agent's analysis hold up?

    If all 3 checks pass: result.deep_verified = True.
    If any check fails: score reduced by 0.10, priority downgraded to P1.

    Never delays below actionable range: a failed P0 becomes P1 with a
    score floor of 0.70 — still actionable, just flagged.
    """
    if result.final_score < _DEEP_VERIFY_THRESHOLD:
        return result  # only deep-verify P0

    checks: list[tuple[str, bool]] = []

    # Check 1: temporal consistency
    try:
        checks.append(("temporal_consistency", _check_temporal_consistency(result)))
    except Exception:
        checks.append(("temporal_consistency", True))  # benefit of doubt on error

    # Check 2: cross-source confirmation
    try:
        checks.append(("cross_source_confirmation", _check_cross_source_confirmation(result)))
    except Exception:
        checks.append(("cross_source_confirmation", True))

    # Check 3: red-team decision review
    try:
        rt_decision = next(
            (d for d in (result.decisions or []) if d.agent == "red_team"), None
        )
        rt_ok = rt_decision is None or rt_decision.verdict is not AgentVerdict.BLOCK
        checks.append(("red_team_review", rt_ok))
    except Exception:
        checks.append(("red_team_review", True))

    failures = [name for name, ok in checks if not ok]
    if failures:
        new_score = max(0.70, result.final_score - 0.10)
        _log.warning(
            "deep_verify.failed",
            trend_id=result.trend_id,
            failed_checks=failures,
            original_score=result.final_score,
            adjusted_score=new_score,
        )
        return result.model_copy(update={
            "final_score": new_score,
            "final_priority": Priority.P1_EXIT,
            "deep_verified": False,
            "deep_verify_failures": failures,
        })

    _log.info(
        "deep_verify.passed",
        trend_id=result.trend_id,
        score=result.final_score,
    )
    return result.model_copy(update={"deep_verified": True, "deep_verify_failures": []})


def _check_temporal_consistency(result: GraphResult) -> bool:
    """Velocity pattern must be accelerating, not spike-and-crash.

    Organic trends accelerate (1h pace >= 6h average pace, 6h pace >= 24h
    average pace); coordinated spikes decelerate. Insufficient data passes.
    """
    try:
        v1h = float(result.trend_data.get("velocity_1h", 0))
        v6h = float(result.trend_data.get("velocity_6h", 0))
        v24h = float(result.trend_data.get("velocity_24h", 0))
        if v6h > 0 and v24h > 0:
            return v1h >= v6h / 6 and v6h >= v24h / 4
        return True  # insufficient data → pass
    except Exception:
        return True


def _check_cross_source_confirmation(result: GraphResult) -> bool:
    """At least 2 independent platforms must show the signal."""
    try:
        platforms = result.trend_data.get("platforms", [])
        return len(set(platforms)) >= 2
    except Exception:
        return True


# Maps Phase 2 AgentVerdict values to Phase 4's execution vocabulary.
# Phase 4 composer expects ENTER/HOLD/EXIT/BLOCK; AgentVerdict uses proceed/hold/block/escalate.
_VERDICT_TO_PHASE4: dict[str, str] = {
    "proceed": "ENTER",
    "hold": "HOLD",
    "block": "BLOCK",
    "escalate": "HOLD",  # escalation has no Phase 4 equivalent; conservatively hold
}

# OMEGA (b): minimum *calibrated* P(correct) required to let an ENTER through to
# Phase 4. Below this, a calibrated ENTER is downgraded to HOLD. Only applied
# when a fitted calibration map exists (never on raw/UNVERIFIED confidence).
# Default 0.5 == refuse to ENTER on a measured worse-than-coin-flip.
_CALIBRATED_ENTER_FLOOR: float = float(os.getenv("AEGIS_CALIBRATED_ENTER_FLOOR", "0.5"))

# OMEGA (skill gate): minimum measured Brier *skill* (vs a base-rate predictor)
# required to let an ENTER reach Phase 4. brier_skill <= 0 means the model is no
# better than always guessing the majority class — acting on its ENTERs is
# acting on noise. Read from the latest calibration_snapshots row (refreshed by
# the daily refit job). Default 0.0 == refuse to ENTER until skill is positive.
_MODEL_SKILL_FLOOR: float = float(os.getenv("AEGIS_MODEL_SKILL_FLOOR", "0.0"))
# Cache the measured skill briefly so we do not hit the DB once per trend.
_skill_cache: dict[str, tuple[float, float | None]] = {}
_SKILL_CACHE_TTL_S: float = 300.0


async def _recent_model_skill(tenant_id: str) -> float | None:
    """Latest measured Brier skill of the deployed heuristic, or None.

    Returns ``None`` (→ do NOT gate) when no snapshot exists yet, so a fresh
    deployment with no measurements never blocks. Once the refit job persists a
    snapshot, a non-positive skill gates ENTERs. Fails open on any error.
    """
    import time as _time

    cached = _skill_cache.get(tenant_id)
    if cached is not None and (_time.monotonic() - cached[0]) < _SKILL_CACHE_TTL_S:
        return cached[1]
    skill: float | None = None
    try:
        from aegis.db.pool import get_shared_pool

        pool = get_shared_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant', $1, false)", tenant_id
            )
            row = await conn.fetchrow(
                "SELECT brier_skill FROM calibration_snapshots "
                "WHERE entity_kind = 'model' AND status = 'ok' "
                "AND brier_skill IS NOT NULL "
                "ORDER BY computed_at DESC LIMIT 1"
            )
        if row is not None and row["brier_skill"] is not None:
            skill = float(row["brier_skill"])
    except Exception as exc:  # fail open — never block on a measurement error
        _log.debug("runner.skill_unavailable", error=str(exc)[:120])
        skill = None
    _skill_cache[tenant_id] = (_time.monotonic(), skill)
    return skill


async def _calibrate_confidence(
    raw_confidence: float, tenant_id: str
) -> tuple[float, bool]:
    """Map raw blended confidence through the fitted isotonic calibration map.

    PROJECT OMEGA (A-deep): the agent path produces evidence-volume confidence
    that is badly miscalibrated (measured 0.98 → 0.38 realized). When a REAL
    fitted map exists in ``calibration_maps`` (built from settled signal
    outcomes), we surface the calibrated number instead. Returns
    ``(value, calibrated)`` where ``calibrated`` is ``True`` only when a fitted
    map was applied. Fails OPEN — any error, no shared pool, or only an identity
    (unfitted) map → returns the raw value with ``calibrated=False`` (the caller
    labels it UNVERIFIED). We never fabricate calibration from thin data.
    """
    try:
        from aegis.db.pool import get_shared_pool
        from aegis.trust.store import TrustStore

        pool = get_shared_pool()
        cal = await TrustStore(pool, tenant_id=tenant_id).load_map()
        if not cal.knots:  # identity / unfitted → do not touch the raw number
            return raw_confidence, False
        return max(0.0, min(1.0, cal.apply(raw_confidence))), True
    except Exception as exc:  # fail open — confidence is advisory, never blocks
        _log.debug("runner.calibration_unavailable", error=str(exc)[:120])
        return raw_confidence, False


async def _publish_phase2_result(
    redis_client: Any,
    result: GraphResult,
    tenant_id: str,
) -> None:
    """XADD a GraphResult summary to the Phase 4 intake stream."""
    raw_verdict = result.final_verdict.value
    phase4_verdict = _VERDICT_TO_PHASE4.get(raw_verdict, "HOLD")
    # A-deep: surface the calibrated confidence when a fitted map exists; else
    # keep the raw value and label it UNVERIFIED. Best-effort, never blocks.
    raw_confidence = result.final_confidence
    shown_confidence, confidence_calibrated = await _calibrate_confidence(
        raw_confidence, tenant_id
    )
    # Pull data_confidence from result metadata if the runner attached it.
    # Falls back to 1.0 (assume clean) when not set — avoids breaking old
    # callers that do not run the confidence gate.
    data_confidence: float = getattr(result, "data_confidence", 1.0)

    # ── OMEGA (b): wire the settled-outcome loop into the LIVE verdict ──────
    # The calibration map is fitted from settled signal_outcomes, so a
    # *calibrated* confidence is a real measured P(correct). We refuse to send
    # an ENTER downstream when that measured probability is below the floor
    # (default 0.5 — worse than a coin flip). This only fires when a fitted map
    # exists (`confidence_calibrated`); with no real evidence we never override.
    confidence_gated = False
    if (
        phase4_verdict == "ENTER"
        and confidence_calibrated
        and shown_confidence < _CALIBRATED_ENTER_FLOOR
    ):
        phase4_verdict = "HOLD"
        confidence_gated = True
        _log.info(
            "runner.enter_gated_by_calibration",
            trend_id=result.trend_id,
            calibrated_confidence=round(shown_confidence, 4),
            floor=_CALIBRATED_ENTER_FLOOR,
        )

    # ── OMEGA (skill gate): refuse to ENTER while the model has no measured ──
    # skill. brier_skill <= floor means the deployed predictor is no better than
    # guessing the base rate; its ENTERs are noise. Only fires once a real
    # snapshot exists (None → no measurement yet → never gate). This is the
    # system being honest by default: it will not act on a skill-less model.
    skill_gated = False
    if phase4_verdict == "ENTER":
        measured_skill = await _recent_model_skill(tenant_id)
        if measured_skill is not None and measured_skill <= _MODEL_SKILL_FLOOR:
            phase4_verdict = "HOLD"
            confidence_gated = True
            skill_gated = True
            _log.info(
                "runner.enter_gated_by_skill",
                trend_id=result.trend_id,
                measured_brier_skill=round(measured_skill, 4),
                floor=_MODEL_SKILL_FLOOR,
            )

    payload = {
        # Phase 4 intake fields (verdict mapped to P4 vocabulary)
        "trend_id": result.trend_id,
        "final_verdict": phase4_verdict,
        "confidence_gated": confidence_gated,
        "skill_gated": skill_gated,
        "final_score": result.final_score,
        # A-deep: `final_confidence` is the calibrated number when a fitted map
        # exists; `confidence_raw` always preserves the original; the flag +
        # basis tell the UI whether the shown number is measured or UNVERIFIED.
        "final_confidence": round(shown_confidence, 4),
        "confidence_raw": round(raw_confidence, 4),
        "confidence_calibrated": confidence_calibrated,
        "confidence_basis": "calibrated" if confidence_calibrated else "UNVERIFIED_raw",
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
        # Causal explanation layer
        "explanation": result.explanation,
        "counterfactual": result.counterfactual,
        "primary_drivers": result.primary_drivers,
        # Provenance (HALLU-1) — lets the dashboard badge heuristic-only verdicts.
        "llm_used": result.llm_used,
        "reasoning_source": result.reasoning_source,
        # PASS6-6A: second-pass verification outcome for P0 results.
        "deep_verified": result.deep_verified,
        "deep_verify_failures": list(result.deep_verify_failures),
    }
    body = json.dumps(payload, default=str)
    await redis_client.xadd(
        "aegis:phase2:graph_results",
        {"body": body},
        maxlen=10_000,
        approximate=True,
    )


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
