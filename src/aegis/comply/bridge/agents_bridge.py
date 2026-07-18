"""Phase 2 bridge: map a candidate dict to an ``AgentDecision``-shaped dict.

The Phase 2 ``COMPLIANCE`` node calls :func:`evaluate_for_compliance_node` with
a ``TrendCandidate``-shaped mapping. We map it into a ``ComplianceRequest``,
run the deterministic engine, and emit a dict in the Phase 2 ``AgentVerdict``
vocabulary (``proceed`` / ``hold`` / ``block`` / ``escalate``) — *without*
importing LangGraph or any Phase 2 module (structural typing only).

Verdict mapping (fail-closed)::

    CLEAR -> proceed
    FLAG  -> escalate   # route to human / compliance review
    BLOCK -> block

On *any* engine error the bridge returns ``escalate`` with low confidence — a
compliance gate must never fail open.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aegis.comply.engine import ComplianceEngine
from aegis.comply.logging import get_logger
from aegis.comply.schemas import ComplianceRequest, ComplianceVerdict, Jurisdiction

_log = get_logger("aegis.comply.bridge.agents")

_VERDICT_TO_PHASE2: dict[ComplianceVerdict, str] = {
    ComplianceVerdict.CLEAR: "proceed",
    ComplianceVerdict.FLAG: "escalate",
    ComplianceVerdict.BLOCK: "block",
}

# Process-singleton engine so the node doesn't rebuild the registry per call.
_engine_ref: list[ComplianceEngine] = []


def get_engine() -> ComplianceEngine:
    """Return the process-singleton compliance engine."""
    if not _engine_ref:
        _engine_ref.append(ComplianceEngine())
    return _engine_ref[0]


def _coerce_request(candidate: Mapping[str, Any]) -> ComplianceRequest:
    """Map a TrendCandidate-shaped mapping into a ``ComplianceRequest``."""
    jurs_raw = candidate.get("target_jurisdictions") or candidate.get("jurisdictions")
    jurisdictions: tuple[Jurisdiction, ...]
    if jurs_raw:
        parsed: list[Jurisdiction] = []
        for j in jurs_raw:
            try:
                parsed.append(Jurisdiction(str(j).upper()))
            except ValueError:
                continue
        jurisdictions = tuple(parsed) or (Jurisdiction.US,)
    else:
        jurisdictions = (Jurisdiction.US,)

    claims = candidate.get("claims") or ()
    brands = candidate.get("brand_mentions") or candidate.get("platforms") or ()

    return ComplianceRequest(
        trend_id=str(candidate.get("trend_id") or candidate.get("id") or "unknown"),
        title=str(candidate.get("title") or ""),
        description=str(candidate.get("description") or ""),
        category=str(candidate.get("category") or "general"),
        audience=str(candidate.get("audience") or "general"),
        price=candidate.get("price"),
        currency=str(candidate.get("currency") or "USD"),
        claims=tuple(str(c) for c in claims),
        brand_mentions=tuple(str(b) for b in brands),
        target_jurisdictions=jurisdictions,
        endorsement_present=bool(candidate.get("endorsement_present", False)),
        disclosure_present=bool(candidate.get("disclosure_present", False)),
        collects_personal_data=bool(candidate.get("collects_personal_data", False)),
        has_privacy_policy=bool(candidate.get("has_privacy_policy", False)),
        has_age_gate=bool(candidate.get("has_age_gate", False)),
    )


def evaluate_for_compliance_node(
    candidate: Mapping[str, Any],
    *,
    engine: ComplianceEngine | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate a candidate and return an ``AgentDecision``-shaped dict.

    The returned dict is intentionally permissive in shape so the Phase 2 node
    can splice it into its ``AgentDecision`` without a hard schema import.
    """
    eng = engine or get_engine()
    try:
        request = _coerce_request(candidate)
        result = eng.evaluate(request)
        phase2_verdict = _VERDICT_TO_PHASE2[result.verdict]
        return {
            "agent": "compliance",
            "verdict": phase2_verdict,
            "score": round(1.0 - result.risk_score, 4),  # higher = safer
            "confidence": result.confidence,
            "reasoning": result.reasoning,
            "compliance": {
                "verdict": result.verdict.value,
                "risk_score": result.risk_score,
                "blocking_reasons": list(result.blocking_reasons),
                "remediation": list(result.remediation),
                "rule_ids": [h.rule_id for h in result.rule_hits],
                "trademarks": [m.mark for m in result.trademark_matches],
                "counterfeit": [s.brand for s in result.counterfeit_signals],
                "content_id": result.content_id,
                "engine_version": result.engine_version,
            },
        }
    except Exception as exc:
        _log.error(
            "comply.bridge_error",
            trend_id=str(candidate.get("trend_id", "unknown")),
            error=str(exc),
        )
        return {
            "agent": "compliance",
            "verdict": "escalate",
            "score": 0.0,
            "confidence": 0.30,
            "reasoning": f"Compliance evaluation failed; escalating for review ({exc}).",
            "compliance": {"verdict": "flag", "error": str(exc)},
        }
