"""
Phase 8 ↔ Phase 6 compliance gate.

Wired into ExecutionEngine.execute_plan() to gate every execution plan
through the compliance risk matrix before any capital is committed.

Usage (called automatically by ExecutionEngine when compliance is enabled)::

    from aegis.execute.compliance_gate import gate_execution_plan

    approved, reason = await gate_execution_plan(plan, product_title=..., ...)
    if not approved:
        raise ComplianceBlockError(reason)
"""

from __future__ import annotations

from typing import Any

import structlog

_log = structlog.get_logger("aegis.execute.compliance_gate")


async def gate_execution_plan(
    plan: Any,
    product_title: str,
    product_description: str = "",
    product_image_url: str | None = None,
    origin_country: str = "US",
    destination_country: str = "US",
    category: str = "general",
    price_usd: float | None = None,
) -> tuple[bool, str]:
    """
    Gate an ExecutionPlan through Phase 8 compliance checks.

    Args:
        plan: ExecutionPlan from Phase 6 ExecutionEngine.
        product_title: Human-readable product name.
        product_description: Product description text.
        product_image_url: Optional image URL for CLIP counterfeit check.
        origin_country: ISO-2 origin country (default "US").
        destination_country: ISO-2 destination country (default "US").
        category: Product category string.
        price_usd: Listed price in USD (for price-anomaly counterfeit check).

    Returns:
        (should_execute: bool, reason: str)
        True  → PROCEED: compliance passed, execution approved.
        False → BLOCK or ESCALATE: execution rejected / held for review.
    """
    try:
        from aegis.compliance.engine import ComplianceEngine
        from aegis.compliance.schemas import ComplianceRequest, Recommendation
    except (ImportError, ModuleNotFoundError):
        # Phase 8 not installed — fail open (allow execution) with a warning
        _log.warning(
            "compliance_gate.module_absent",
            message="aegis.compliance not installed — compliance gate bypassed",
            plan_id=getattr(plan, "plan_id", "unknown"),
        )
        return True, "Compliance gate bypassed (module not installed)"

    engine = ComplianceEngine()

    intent_id = getattr(plan, "intent_id", "") or getattr(plan, "plan_id", "unknown")

    req = ComplianceRequest(
        product_sku=str(intent_id),
        product_title=product_title,
        product_description=product_description,
        product_image_url=product_image_url,
        category=category,
        origin_country=origin_country.upper(),
        destination_country=destination_country.upper(),
        price_usd=price_usd,
    )

    assessment = await engine.assess(req)

    if assessment.recommendation == Recommendation.BLOCK:
        reasons_str = "; ".join(assessment.reasons[:3])
        _log.error(
            "compliance_gate.blocked",
            plan_id=str(intent_id),
            risk_score=assessment.overall_risk_score,
            reasons=assessment.reasons,
            error_code="AEGIS-COMPLY-0001",
        )
        return False, (
            f"COMPLIANCE BLOCK (risk={assessment.overall_risk_score:.0%}): {reasons_str}"
        )

    if assessment.recommendation == Recommendation.ESCALATE:
        reasons_str = "; ".join(assessment.reasons[:3])
        _log.warning(
            "compliance_gate.escalated",
            plan_id=str(intent_id),
            risk_score=assessment.overall_risk_score,
            reasons=assessment.reasons,
        )
        return False, (
            f"COMPLIANCE ESCALATION required (risk={assessment.overall_risk_score:.0%}): "
            f"{reasons_str}. Awaiting human review."
        )

    _log.info(
        "compliance_gate.approved",
        plan_id=str(intent_id),
        risk_score=assessment.overall_risk_score,
    )
    return True, f"Compliance approved (risk={assessment.overall_risk_score:.0%})"

