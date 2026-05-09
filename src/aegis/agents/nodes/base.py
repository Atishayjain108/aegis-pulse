"""
Base class for every agent node.

The doctrine — re-stated because it matters — is:

  HEURISTIC FIRST. LLM AS AUGMENTATION.

Every agent computes a deterministic verdict from numeric features
alone. The LLM is invited to *refine reasoning text* and tweak the
confidence — but is never the gating mechanism. This means:
  * Tests pass without any LLM provider.
  * The pipeline has bounded latency: the heuristic path is O(1).
  * The system degrades gracefully when networks fail.
  * Audits are reproducible: a verdict is a function of features.

Subclasses override `_decide_heuristic()` and may optionally override
`_augment_with_llm()`. The base class handles timing, logging,
state-shape conversion to LangGraph, and emitting Prometheus metrics.

Author: AEGIS Pulse core team
"""
from __future__ import annotations

import abc
import time
from typing import TYPE_CHECKING, Any

import structlog

from ..llm import LLMResponse, LLMRouter, get_default_router
from ..llm.guardrails import parse_json
from ..schemas import AgentDecision, AgentVerdict, TrendCandidate

if TYPE_CHECKING:
    from ..state import GraphState

_log = structlog.get_logger("aegis.agents.nodes.base")


class AgentNode(abc.ABC):
    """Abstract base for all 10 agents."""

    name: str  # set on subclasses

    def __init__(
        self,
        *,
        router: LLMRouter | None = None,
        use_llm: bool = True,
        llm_max_tokens: int = 384,
        llm_temperature: float = 0.2,
        llm_timeout_s: float = 20.0,
    ) -> None:
        self._router = router
        self.use_llm = bool(use_llm)
        self.llm_max_tokens = int(llm_max_tokens)
        self.llm_temperature = float(llm_temperature)
        self.llm_timeout_s = float(llm_timeout_s)

    # ------------------------------------------------------------------
    # Public LangGraph entry point. LangGraph nodes are async callables
    # taking the state dict and returning a partial-state dict.
    # ------------------------------------------------------------------
    async def __call__(self, state: GraphState) -> dict[str, Any]:
        start = time.perf_counter()
        candidate: TrendCandidate = state["candidate"]

        try:
            heuristic = await self._decide_heuristic(candidate, state)
        except Exception as exc:
            _log.exception("agent.heuristic_failed", agent=self.name)
            decision = self._error_decision(candidate, str(exc), start)
            return self._merge_partial(decision, state)

        # LLM augmentation is optional; on failure or skip we keep the
        # heuristic as-is. The augmentation can ONLY:
        #   * Append to `reasoning` (concatenated, capped).
        #   * Multiply `confidence` by a factor in [0.5, 1.0].
        #   * Add details under `details["llm"]`.
        # It cannot flip the verdict — that's the doctrine.
        decision = heuristic
        if self.use_llm:
            try:
                augmented = await self._augment_with_llm(candidate, state, heuristic)
                if augmented is not None:
                    decision = augmented
            except Exception:
                _log.exception("agent.llm_augmentation_failed", agent=self.name)

        # Recompute duration from the actual start.
        duration_ms = (time.perf_counter() - start) * 1000.0
        decision = decision.model_copy(update={"duration_ms": duration_ms})
        return self._merge_partial(decision, state)

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------
    @abc.abstractmethod
    async def _decide_heuristic(
        self,
        candidate: TrendCandidate,
        state: GraphState,
    ) -> AgentDecision:
        """Compute a verdict from numeric features ONLY. Must not perform
        I/O or call an LLM."""

    async def _augment_with_llm(
        self,
        candidate: TrendCandidate,
        state: GraphState,
        heuristic: AgentDecision,
    ) -> AgentDecision | None:
        """Optional: refine `reasoning` and (within bounds) `confidence`
        using an LLM. Default: no-op. Subclasses override per agent.

        Subclasses that DO use LLMs should call `self._llm_complete(...)`
        which already handles None routers, timeouts, and JSON parsing.
        """
        return None

    # ------------------------------------------------------------------
    # Helpers for subclasses
    # ------------------------------------------------------------------
    async def _llm_complete(
        self,
        *,
        system: str,
        user: str,
    ) -> LLMResponse | None:
        router = self._router
        if router is None:
            router = await get_default_router()
        return await router.complete(
            system=system,
            user=user,
            max_tokens=self.llm_max_tokens,
            temperature=self.llm_temperature,
            timeout_s=self.llm_timeout_s,
        )

    def _llm_apply(
        self,
        heuristic: AgentDecision,
        resp: LLMResponse | None,
    ) -> AgentDecision:
        """Apply LLM output to the heuristic decision under safe bounds."""
        if resp is None or not resp.text.strip():
            return heuristic

        parsed = parse_json(resp.text)
        # We accept a small JSON object: {"reasoning": "...", "confidence_factor": 0.0-1.0}.
        # Anything malformed → keep the LLM text in `reasoning`, no factor.
        new_reasoning = heuristic.reasoning
        confidence_factor = 1.0
        llm_details: dict[str, Any] = {
            "provider": resp.provider,
            "model": resp.model,
            "tokens_input": resp.tokens_input,
            "tokens_output": resp.tokens_output,
            "latency_ms": round(resp.latency_ms, 2),
        }

        if isinstance(parsed, dict):
            r = parsed.get("reasoning")
            if isinstance(r, str) and r.strip():
                addition = r.strip()[:1500]
                new_reasoning = f"{heuristic.reasoning}\n[LLM] {addition}".strip()
            f = parsed.get("confidence_factor")
            if isinstance(f, int | float):
                confidence_factor = max(0.5, min(1.0, float(f)))
            llm_details["parsed"] = True
        else:
            # Free-form text — append verbatim, no confidence shift.
            addition = resp.text.strip()[:1500]
            new_reasoning = f"{heuristic.reasoning}\n[LLM] {addition}".strip()
            llm_details["parsed"] = False

        new_confidence = max(0.0, min(1.0, heuristic.confidence * confidence_factor))
        new_details = {**heuristic.details, "llm": llm_details}

        return heuristic.model_copy(
            update={
                "reasoning": new_reasoning[:4000],
                "confidence": new_confidence,
                "used_llm": True,
                "llm_provider": resp.provider,
                "llm_model": resp.model,
                "llm_tokens_input": resp.tokens_input,
                "llm_tokens_output": resp.tokens_output,
                "llm_latency_ms": resp.latency_ms,
                "details": new_details,
            }
        )

    def _error_decision(
        self,
        candidate: TrendCandidate,
        error: str,
        start: float,
    ) -> AgentDecision:
        duration_ms = (time.perf_counter() - start) * 1000.0
        return AgentDecision(
            agent=self.name,
            trend_id=candidate.trend_id,
            correlation_id=candidate.correlation_id,
            verdict=AgentVerdict.HOLD,
            score=0.0,
            confidence=0.0,
            reasoning=f"agent error: {error}",
            duration_ms=duration_ms,
            details={"error": error[:500]},
        )

    def _merge_partial(
        self,
        decision: AgentDecision,
        state: GraphState,
    ) -> dict[str, Any]:
        """Build the LangGraph partial-state return value.

        We always emit `decisions: [decision]` — LangGraph's reducer
        will append it to the running list. Subclasses can override
        to also write specific keys (scout_score, compliance_passed,
        ...). They do this by overriding `_extra_state(decision)`.
        """
        partial: dict[str, Any] = {"decisions": [decision]}
        partial.update(self._extra_state(decision))
        if decision.verdict is AgentVerdict.BLOCK:
            partial["blocked_by"] = [self.name]
        return partial

    def _extra_state(self, decision: AgentDecision) -> dict[str, Any]:
        """Agent-specific extra fields written into `state`. Default: none."""
        return {}
