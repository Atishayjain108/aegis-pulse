"""LLM augmentation for compliance verdicts (Phase 11 connectivity point).

Strictly subordinate to the deterministic engine. Augmentation may only make a
verdict *more* restrictive (``clear`` -> ``flag`` -> ``block``) and may only
*lower* confidence. It can never reverse a block, raise a verdict's freedom, or
inflate confidence. This is the compliance-specific form of the project-wide
"heuristic-first, LLM as augmentation" doctrine: for a compliance gate the safe
augmentation direction is fail-closed.

The augmentor is fully optional. If no LLM client is wired (or the call fails,
times out, or returns malformed output) the original deterministic result is
returned unchanged. No exception escapes ``augment``.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from aegis.comply.logging import get_logger
from aegis.comply.schemas import (
    ComplianceRequest,
    ComplianceVerdict,
    ComplianceVerdictResult,
)

_log = get_logger("aegis.comply.llm.augmentor")

# Ordering of verdicts by restrictiveness; index only ever moves up.
_ORDER: dict[ComplianceVerdict, int] = {
    ComplianceVerdict.CLEAR: 0,
    ComplianceVerdict.FLAG: 1,
    ComplianceVerdict.BLOCK: 2,
}
_BY_RANK = {v: k for k, v in _ORDER.items()}


@runtime_checkable
class LLMComplete(Protocol):
    """Minimal async completion interface (structural typing, no hard import).

    Any Phase 11 client exposing ``await complete(prompt, *, system=...) -> str``
    satisfies this. The compliance package never imports ``aegis.llm`` directly.
    """

    async def complete(self, prompt: str, *, system: str = ...) -> str: ...


_SYSTEM_PROMPT = (
    "You are a conservative regulatory-compliance reviewer for an e-commerce "
    "listing pipeline. You are given a deterministic compliance verdict and the "
    "listing it was computed from. Your ONLY permitted actions are: (1) keep the "
    "verdict as-is, or (2) escalate it to a STRICTER level if you see a clear "
    "compliance risk the rules missed. You may NEVER downgrade or clear a "
    "verdict. Respond with a single minified JSON object and nothing else: "
    '{"escalate_to": "clear|flag|block", "confidence_penalty": 0.0-0.3, '
    '"reason": "short string"}. Use "escalate_to" equal to the current verdict '
    "if no change is warranted."
)


def _build_prompt(result: ComplianceVerdictResult, request: ComplianceRequest) -> str:
    summary = {
        "current_verdict": result.verdict.value,
        "risk_score": result.risk_score,
        "title": request.title,
        "description": request.description[:600],
        "category": request.category,
        "audience": request.audience,
        "claims": list(request.claims)[:10],
        "brand_mentions": list(request.brand_mentions)[:10],
        "jurisdictions": [j.value for j in request.target_jurisdictions],
        "rule_hits": [
            {"id": h.rule_id, "severity": h.severity.value, "category": h.category.value}
            for h in result.rule_hits
        ],
        "trademark_matches": [m.mark for m in result.trademark_matches],
        "counterfeit_brands": [s.brand for s in result.counterfeit_signals],
    }
    return (
        "Review this listing and its deterministic verdict. Escalate only if a "
        "real, missed compliance risk exists.\n\n"
        + json.dumps(summary, separators=(",", ":"), ensure_ascii=False)
    )


class ComplianceAugmentor:
    """Wraps an optional LLM client; enforces escalate-only, never-raises."""

    def __init__(
        self,
        client: LLMComplete | None = None,
        *,
        max_confidence_penalty: float = 0.30,
    ) -> None:
        self._client = client
        self._max_penalty = max_confidence_penalty

    @property
    def available(self) -> bool:
        return self._client is not None

    async def augment(
        self,
        result: ComplianceVerdictResult,
        request: ComplianceRequest,
    ) -> ComplianceVerdictResult:
        """Return a verdict that is equal-or-stricter than ``result``.

        Best-effort: any failure returns the input ``result`` unchanged.
        """
        if self._client is None:
            return result
        # An already-blocked verdict is maximal; nothing to escalate to.
        if result.verdict is ComplianceVerdict.BLOCK:
            return result
        try:
            raw = await self._client.complete(
                _build_prompt(result, request), system=_SYSTEM_PROMPT
            )
            decision = self._parse(raw)
        except Exception as exc:
            _log.warning(
                "comply.augment.client_error",
                trend_id=request.trend_id,
                error=str(exc),
            )
            return result

        if decision is None:
            return result

        proposed, penalty, reason = decision
        new_rank = max(_ORDER[result.verdict], _ORDER[proposed])  # never downgrade
        new_verdict = _BY_RANK[new_rank]
        escalated = new_verdict is not result.verdict

        # Confidence may only decrease; clamp the penalty to the allowed band.
        penalty = max(0.0, min(self._max_penalty, penalty))
        new_confidence = round(max(0.30, result.confidence - penalty), 4)

        if not escalated and new_confidence == result.confidence:
            return result

        reasoning = result.reasoning
        if escalated:
            reasoning = (
                f"{result.reasoning} [LLM escalated "
                f"{result.verdict.value}->{new_verdict.value}: {reason}]"
            ).strip()

        _log.info(
            "comply.augment.applied",
            trend_id=request.trend_id,
            from_verdict=result.verdict.value,
            to_verdict=new_verdict.value,
            confidence_penalty=penalty,
        )
        return result.model_copy(
            update={
                "verdict": new_verdict,
                "confidence": new_confidence,
                "reasoning": reasoning,
                "augmented": True,
            }
        )

    def _parse(
        self, raw: str
    ) -> tuple[ComplianceVerdict, float, str] | None:
        """Parse the model's JSON response defensively."""
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None
        try:
            obj = json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(obj, dict):
            return None
        try:
            proposed = ComplianceVerdict(str(obj.get("escalate_to", "")).lower())
        except ValueError:
            return None
        try:
            penalty = float(obj.get("confidence_penalty", 0.0) or 0.0)
        except (TypeError, ValueError):
            penalty = 0.0
        reason = str(obj.get("reason", ""))[:240]
        return proposed, penalty, reason


def build_default_augmentor() -> ComplianceAugmentor:
    """Best-effort wiring to a Phase 11 LLM client; no-op if unavailable.

    Tries the canonical Phase 11 entry points without a hard dependency. If
    none resolve, returns an augmentor whose ``augment`` is a pass-through.
    """
    client = _discover_client()
    return ComplianceAugmentor(client)


def _discover_client() -> LLMComplete | None:
    """Attempt to locate an async LLM client from sibling phases.

    Returns ``None`` (graceful) if no compatible client is importable.
    """
    # Candidate factories, tried in order. Each returns an LLMComplete or raises.
    candidates: list[Callable[[], Any]] = []

    def _from_llm_bridge() -> Any:
        from aegis.llm.bridge import get_async_client  # type: ignore

        return get_async_client()

    def _from_agents_llm() -> Any:
        from aegis.agents.llm import get_client  # type: ignore

        return get_client()

    candidates.extend([_from_llm_bridge, _from_agents_llm])

    for factory in candidates:
        try:
            client = factory()
        except Exception:
            continue
        if _adapt(client) is not None:
            return _adapt(client)
    return None


def _adapt(client: Any) -> LLMComplete | None:
    """Adapt a discovered client to the ``LLMComplete`` protocol if possible."""
    if client is None:
        return None
    if isinstance(client, LLMComplete):
        return client
    # Adapt a common ``acomplete``/``generate`` shape.
    fn: Callable[..., Awaitable[str]] | None = None
    for attr in ("acomplete", "complete_async", "generate", "ainvoke"):
        cand = getattr(client, attr, None)
        if callable(cand):
            fn = cand
            break
    if fn is None:
        return None

    class _Adapter:
        async def complete(self, prompt: str, *, system: str = "") -> str:
            try:
                return str(await fn(prompt, system=system))  # type: ignore[misc]
            except TypeError:
                return str(await fn(prompt))  # type: ignore[misc]

    return _Adapter()
