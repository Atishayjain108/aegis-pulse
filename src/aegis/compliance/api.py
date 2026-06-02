"""
Phase 8 FastAPI router — /compliance/* endpoints.

Routes:
  GET  /compliance/health                       — liveness
  POST /compliance/assess                       — full compliance risk assessment
  POST /compliance/assess/batch                 — batch assessment (up to 20)
  GET  /compliance/trademark/{query}            — trademark lookup only
  GET  /compliance/sanctions/{country}          — OFAC/FATF check for a country
  GET  /compliance/ftc                          — FTC rule check (query params)
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from aegis.compliance.aml import AMLChecker
from aegis.compliance.engine import ComplianceEngine
from aegis.compliance.ftc import FTCRuleEngine
from aegis.compliance.ipr import IPRChecker
from aegis.compliance.schemas import ComplianceRequest, ComplianceRiskAssessment

_log = structlog.get_logger("aegis.compliance.api")

router = APIRouter(prefix="/compliance", tags=["compliance"])

# Process-singleton instances (cache/connection-pool reuse)
_engine = ComplianceEngine()
_ipr = IPRChecker()
_aml = AMLChecker()
_ftc = FTCRuleEngine()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class AssessRequest(BaseModel):
    product_sku: str = Field(..., min_length=1, max_length=128)
    product_title: str = Field(..., min_length=1, max_length=256)
    product_description: str = Field(default="", max_length=2000)
    product_image_url: str | None = None
    category: str = Field(default="general", max_length=64)
    origin_country: str = Field(default="US", min_length=2, max_length=3)
    destination_country: str = Field(default="US", min_length=2, max_length=3)
    price_usd: float | None = Field(default=None, ge=0.0)
    tenant_id: str = Field(default="00000000-0000-0000-0000-000000000001")


class BatchAssessRequest(BaseModel):
    items: list[AssessRequest] = Field(..., min_length=1, max_length=20)


class SanctionCheckResponse(BaseModel):
    country: str
    is_sanctioned: bool
    is_fatf_high_risk: bool
    sanction_program: str
    risk_score: float


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/health")
async def compliance_health() -> dict[str, str]:
    return {"status": "ok", "phase": "8"}


@router.post("/assess", response_model=dict[str, Any])
async def assess_product(req: AssessRequest) -> dict[str, Any]:
    """Full compliance risk assessment for a product + trade route.

    Returns PROCEED / ESCALATE / BLOCK with detailed risk breakdown,
    matched trademarks, FDA enforcements, FTC violations, and sanctions.
    """
    cr = ComplianceRequest(
        product_sku=req.product_sku,
        product_title=req.product_title,
        product_description=req.product_description,
        product_image_url=req.product_image_url,
        category=req.category,
        origin_country=req.origin_country.upper(),
        destination_country=req.destination_country.upper(),
        price_usd=req.price_usd,
        tenant_id=req.tenant_id,
    )
    try:
        result: ComplianceRiskAssessment = await _engine.assess(cr)
    except Exception as exc:
        _log.error("compliance.api_assess_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result.model_dump(mode="json")


@router.post("/assess/batch", response_model=list[dict[str, Any]])
async def assess_batch(req: BatchAssessRequest) -> list[dict[str, Any]]:
    """Assess up to 20 products concurrently."""
    requests = [
        ComplianceRequest(
            product_sku=item.product_sku,
            product_title=item.product_title,
            product_description=item.product_description,
            product_image_url=item.product_image_url,
            category=item.category,
            origin_country=item.origin_country.upper(),
            destination_country=item.destination_country.upper(),
            price_usd=item.price_usd,
            tenant_id=item.tenant_id,
        )
        for item in req.items
    ]
    try:
        results = await _engine.assess_many(requests)
    except Exception as exc:
        _log.error("compliance.api_batch_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return [r.model_dump(mode="json") for r in results]


@router.get("/trademark/{query}", response_model=dict[str, Any])
async def trademark_check(
    query: str,
    description: str = Query(default=""),
) -> dict[str, Any]:
    """Look up trademark matches for a product title (USPTO + EUIPO)."""
    try:
        matches, risk = await _ipr.check_trademark(query, description)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "query": query,
        "trademark_risk": risk,
        "matches": [m.model_dump() for m in matches],
        "match_count": len(matches),
    }


@router.get("/sanctions/{country}", response_model=SanctionCheckResponse)
async def sanctions_check(country: str) -> SanctionCheckResponse:
    """Check if a country is OFAC-sanctioned or FATF high-risk."""
    from aegis.compliance.constants import (
        FATF_HIGH_RISK,
        OFAC_SANCTIONED_COUNTRIES,
    )

    code = country.upper()

    from aegis.compliance.aml import _OFAC_COUNTRY_PROGRAM

    is_sanctioned = code in OFAC_SANCTIONED_COUNTRIES
    is_fatf = code in FATF_HIGH_RISK

    sanction_program = _OFAC_COUNTRY_PROGRAM.get(code, "") if is_sanctioned else ""
    risk_score = 0.99 if is_sanctioned else (0.50 if is_fatf else 0.02)

    return SanctionCheckResponse(
        country=code,
        is_sanctioned=is_sanctioned,
        is_fatf_high_risk=is_fatf,
        sanction_program=sanction_program,
        risk_score=risk_score,
    )


@router.get("/ftc", response_model=dict[str, Any])
async def ftc_check(
    title: str = Query(..., description="Product title to check"),
    description: str = Query(default="", description="Product description to check"),
) -> dict[str, Any]:
    """Run FTC advertising rule engine against product title + description."""
    violations, risk = _ftc.assess(title, description)
    return {
        "title": title,
        "ftc_risk": risk,
        "violations": [v.model_dump() for v in violations],
        "violation_count": len(violations),
        "recommendation": "BLOCK" if risk >= 0.7 else "ESCALATE" if risk >= 0.5 else "PROCEED",
    }
