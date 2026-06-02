"""Optional FastAPI router exposing the compliance engine over HTTP.

Mountable into the AEGIS gateway app::

    from aegis.comply.api import build_router
    app.include_router(build_router(), prefix="/v1")

The module imports with zero hard dependency on FastAPI: if FastAPI/Pydantic
aren't present, ``build_router`` raises a clear error and ``router_available``
is ``False`` — callers can skip mounting it. The engine itself never requires
the web stack.

Endpoints:
    POST /comply/check   -> evaluate a ComplianceRequest, return the verdict
    GET  /comply/health  -> liveness + engine metadata
    GET  /comply/rules   -> loaded rules (optionally ?jurisdiction=EU)
"""

from __future__ import annotations

from typing import Any

from aegis.comply import VERSION
from aegis.comply.logging import get_logger
from aegis.comply.schemas import ComplianceRequest

_log = get_logger("aegis.comply.api")

try:
    from fastapi import APIRouter, HTTPException, Query

    router_available = True
except Exception:  # pragma: no cover - exercised only without fastapi
    router_available = False


def build_router() -> Any:
    """Construct and return the compliance ``APIRouter``.

    Raises ``RuntimeError`` if FastAPI is not installed so the failure is
    explicit at mount time rather than at import time.
    """
    if not router_available:
        raise RuntimeError(
            "FastAPI is not installed; the compliance API router is unavailable. "
            "Install with: pip install fastapi"
        )

    from aegis.comply.engine import ComplianceEngine
    from aegis.comply.rules.loader import default_registry
    from aegis.comply.schemas import ComplianceVerdictResult

    router = APIRouter(tags=["compliance"])
    engine = ComplianceEngine()

    @router.post("/comply/check", response_model=None)
    def check(request: ComplianceRequest) -> dict[str, Any]:
        """Evaluate a listing and return its deterministic compliance verdict."""
        try:
            result: ComplianceVerdictResult = engine.evaluate(request)
        except Exception as exc:
            _log.warning("comply.api.check_error", trend_id=request.trend_id, error=str(exc))
            # Fail closed: present an escalation rather than a hard error.
            raise HTTPException(
                status_code=422,
                detail={"verdict": "escalate", "reason": "evaluation_error"},
            ) from exc
        return {
            "trend_id": result.trend_id,
            "verdict": result.verdict.value,
            "risk_score": result.risk_score,
            "confidence": result.confidence,
            "content_id": result.content_id,
            "blocking_reasons": list(result.blocking_reasons),
            "rule_hits": [
                {
                    "rule_id": h.rule_id,
                    "name": h.name,
                    "severity": h.severity.value,
                    "category": h.category.value,
                    "jurisdiction": h.jurisdiction.value,
                }
                for h in result.rule_hits
            ],
            "trademark_matches": [
                {"mark": m.mark, "owner": m.owner, "similarity": m.similarity}
                for m in result.trademark_matches
            ],
            "counterfeit_signals": [
                {"brand": s.brand, "risk": s.risk, "reason": s.reason}
                for s in result.counterfeit_signals
            ],
            "remediation": list(result.remediation),
            "reasoning": result.reasoning,
            "engine_version": result.engine_version,
        }

    @router.get("/comply/health")
    def health() -> dict[str, Any]:
        """Liveness probe with engine metadata."""
        registry = default_registry()
        return {
            "status": "ok",
            "engine_version": VERSION,
            "rules_loaded": len(registry.all_rules),
        }

    @router.get("/comply/rules")
    def rules(jurisdiction: str | None = Query(default=None)) -> dict[str, Any]:
        """List loaded rules, optionally filtered by jurisdiction."""
        registry = default_registry()
        out = []
        for rule in registry.all_rules:
            if jurisdiction and rule.jurisdiction.value.upper() != jurisdiction.upper():
                continue
            out.append(
                {
                    "rule_id": rule.id,
                    "name": rule.name,
                    "severity": rule.severity.value,
                    "category": rule.category.value,
                    "jurisdiction": rule.jurisdiction.value,
                }
            )
        return {"count": len(out), "rules": out}

    return router
