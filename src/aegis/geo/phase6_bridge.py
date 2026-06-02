"""
Bridge: Phase 7 GeoOpportunity → Phase 6 ExecutionIntent.

When CrossMarketAnalyzer identifies a high-score geo opportunity, this
bridge converts it to an ExecutionIntent that the Phase 6 ExecutionEngine
can process. Uses the real ExecutionIntent schema from aegis.execute.schemas.intent.
"""

from __future__ import annotations

import json
import uuid

import structlog

from aegis.geo.schemas import GeoOpportunity

_log = structlog.get_logger("aegis.geo.phase6_bridge")

# Lazy import — avoid hard dependency on aegis-phase4 workspace member
try:
    from aegis.execute.schemas.intent import ExecutionIntent, IntentKind

    _PHASE6_AVAILABLE = True
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    _PHASE6_AVAILABLE = False
    ExecutionIntent = None  # type: ignore[assignment,misc]
    IntentKind = None  # type: ignore[assignment]

# Default tenant for standalone geo runs (matches AEGIS_DEFAULT_TENANT_ID)
_DEFAULT_TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")


def geo_opportunity_to_execution_intent(
    opp: GeoOpportunity,
    *,
    tenant_id: uuid.UUID | str = _DEFAULT_TENANT,
    alert_id: str = "",
) -> object | None:
    """Convert a GeoOpportunity to a Phase 6 ExecutionIntent.

    Returns None when aegis-phase4 (Phase 6) is not installed.

    Priority / kind mapping:
      gross_margin_pct ≥ 40% → ENTER_POSITION (high-confidence geo arb)
      gross_margin_pct ≥ 20% → ENTER_POSITION
      gross_margin_pct ≥  5% → HOLD_POSITION
    """
    if not _PHASE6_AVAILABLE or ExecutionIntent is None or IntentKind is None:  # pragma: no cover
        _log.warning("phase6_bridge.unavailable", sku=opp.product_sku)
        return None

    margin_f = float(opp.gross_margin_pct)
    confidence = min(1.0, opp.demand_intensity * opp.market_size_score)

    if margin_f >= 20.0:
        kind = IntentKind.ENTER_POSITION
    else:
        kind = IntentKind.HOLD_POSITION

    # advised_units: scale demand intensity to a rough unit count (1–100 range)
    advised_units = max(1, int(opp.demand_intensity * opp.market_size_score * 50))

    # advised_capital: origin price × units (how much to deploy)
    origin_price_usd = float(opp.origin_price_local) * float(opp.fx_rate_used)
    advised_capital_usd = origin_price_usd * advised_units

    # Pack geo-specific data into rationale (no metadata field on the model)
    rationale = json.dumps({
        "geo_opportunity_id": opp.opportunity_id,
        "origin_region": opp.origin_region.value,
        "destination_region": opp.destination_region.value,
        "origin_currency": opp.origin_currency,
        "destination_currency": opp.destination_currency,
        "origin_price_local": str(opp.origin_price_local),
        "destination_price_usd": str(opp.destination_price_usd),
        "shipping_cost_usd": str(opp.shipping_cost_usd),
        "duty_cost_usd": str(opp.duty_cost_usd),
        "gross_margin_pct": str(opp.gross_margin_pct),
        "hs_code": opp.hs_code,
        "opportunity_score": opp.opportunity_score,
        "demand_intensity": opp.demand_intensity,
        "market_size_score": opp.market_size_score,
        "carrier": opp.metadata.get("shipping_carrier", ""),
        "transit_days": opp.metadata.get("transit_days", 0),
    }, default=str)[:2000]  # truncate to model max_length

    tid = uuid.UUID(str(tenant_id)) if not isinstance(tenant_id, uuid.UUID) else tenant_id
    intent_id = f"geo-{opp.opportunity_id}"
    _alert_id = alert_id or f"geo-alert-{opp.opportunity_id[:8]}"

    intent = ExecutionIntent(
        intent_id=intent_id,
        alert_id=_alert_id,
        tenant_id=tid,
        trend_id=f"{opp.product_sku}:{opp.origin_region.value}→{opp.destination_region.value}",
        kind=kind,
        advised_units=advised_units,
        advised_capital_usd=round(advised_capital_usd, 2),
        expected_margin_usd=round(float(opp.gross_margin_usd) * advised_units, 2),
        loss_probability=round(1.0 - confidence, 4),
        horizon_hours=24,
        rationale=rationale,
    )

    _log.info(
        "phase6_bridge.intent_created",
        intent_id=intent_id,
        kind=kind.value if hasattr(kind, "value") else str(kind),
        margin_pct=margin_f,
        origin=opp.origin_region.value,
        destination=opp.destination_region.value,
    )

    return intent
