"""
Phase 7 FastAPI router — /geo/* endpoints.

Routes:
  GET  /geo/health                     — liveness
  POST /geo/analyze                    — find cross-market opportunities for a product
  GET  /geo/fx                         — current FX rates vs USD
  GET  /geo/tariff/{hs_code}/{dest}    — duty rate lookup
  GET  /geo/shipping/{origin}/{dest}   — shipping cost quote
  GET  /geo/opportunities              — list recent persisted opportunities
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from aegis.geo.arbitrage import CrossMarketAnalyzer
from aegis.geo.config import Region
from aegis.geo.fx import FXRateFetcher
from aegis.geo.schemas import GeoArbitrageReport, TariffLookupResult
from aegis.geo.shipping import ShippingResolver
from aegis.geo.tariffs import TariffEstimator

_log = structlog.get_logger("aegis.geo.api")

router = APIRouter(prefix="/geo", tags=["geo"])

_fx = FXRateFetcher()
_shipping = ShippingResolver()
_tariffs = TariffEstimator()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class AnalyzeRequest(BaseModel):
    product_sku: str = Field(..., min_length=1, max_length=128)
    product_title: str = Field(..., min_length=1, max_length=256)
    category: str = Field(default="general", max_length=64)
    top_n: int = Field(default=10, ge=1, le=50)
    tenant_id: str = Field(default="00000000-0000-0000-0000-000000000001")


class FXResponse(BaseModel):
    base: str
    rates: dict[str, float]
    source: str


class ShippingResponse(BaseModel):
    origin: str
    destination: str
    cost_usd: float
    carrier: str
    transit_days_estimate: int
    source: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health")
async def geo_health() -> dict[str, str]:
    return {"status": "ok", "phase": "7"}


@router.post("/analyze", response_model=dict[str, Any])
async def analyze_product(req: AnalyzeRequest) -> dict[str, Any]:
    """Find cross-market arbitrage opportunities for a product.

    Returns the top N (origin, destination) pairs ranked by opportunity_score.
    Prices come from Phase 1 signals DB when available; fall back to published
    category medians when no signals exist for this SKU.
    """
    analyzer = CrossMarketAnalyzer()
    try:
        report: GeoArbitrageReport = await analyzer.find_opportunities(
            req.product_sku,
            req.product_title,
            req.category,
            tenant_id=req.tenant_id,
            top_n=req.top_n,
        )
    except Exception as exc:
        _log.error("geo.analyze_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    payload = report.model_dump(mode="json")
    # CONN-1: publish the result onto the unified event bus (best-effort).
    try:
        from aegis.core.event_bus import STREAM_GEO, publish_event

        await publish_event(STREAM_GEO, payload)
    except Exception:  # pragma: no cover - defensive
        pass
    return payload


@router.get("/fx", response_model=FXResponse)
async def get_fx_rates() -> FXResponse:
    """Return current FX rates vs USD from Frankfurter (ECB source)."""
    try:
        rates = await _fx.get_all_rates()
        return FXResponse(
            base="USD",
            rates={k: float(v) for k, v in rates.items() if k != "USD"},
            source="frankfurter.app (ECB)",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX fetch failed: {exc}") from exc


@router.get("/tariff/{hs_code}/{destination}", response_model=dict[str, Any])
async def get_tariff(
    hs_code: str,
    destination: str,
    value_usd: float = Query(default=100.0, ge=0.01),
    origin: str = Query(default="US"),
) -> dict[str, Any]:
    """Return WTO MFN import duty estimate for a given HS code + destination."""
    result: TariffLookupResult = await _tariffs.estimate(
        hs_code=hs_code,
        origin=origin.upper(),
        destination=destination.upper(),
        value_usd=Decimal(str(value_usd)),
    )
    return result.model_dump(mode="json")


@router.get("/shipping/{origin}/{destination}", response_model=ShippingResponse)
async def get_shipping_quote(
    origin: str,
    destination: str,
    weight_kg: float = Query(default=0.5, ge=0.01, le=30.0),
) -> ShippingResponse:
    """Return a shipping cost quote for an origin→destination pair."""
    try:
        orig = Region(origin.upper())
        dest = Region(destination.upper())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown region: {exc}") from exc

    quote = await _shipping.get_quote(orig, dest, weight_kg=weight_kg)
    return ShippingResponse(
        origin=orig.value,
        destination=dest.value,
        cost_usd=float(quote.cost_usd),
        carrier=quote.carrier,
        transit_days_estimate=quote.transit_days_estimate,
        source=quote.source,
    )
