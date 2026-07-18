"""
Shipping cost resolver for Phase 7.

Uses the static SHIPPING_MATRIX_USD from config (real EMS/postal 2024 rates
for a 0.5 kg tracked parcel) as the baseline.  An optional ShipEngine API
integration can be activated with AEGIS_GEO_SHIPENGINE_KEY for live quotes.

Design: economy-tier postal/EMS rates are the right baseline for dropship
and POD operations where packages weigh < 1 kg and 5–10 day transit is
acceptable.  DHL Express rates (3× higher) are available on request.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import NamedTuple

import httpx
import structlog

from aegis.geo.config import (
    SHIPPING_DEFAULT_USD,
    SHIPPING_MATRIX_USD,
    Region,
)

_log = structlog.get_logger("aegis.geo.shipping")

_SHIPENGINE_API = "https://api.shipengine.com/v1/rates/estimate"
_SHIPENGINE_KEY = os.getenv("AEGIS_GEO_SHIPENGINE_KEY", "")


class ShippingQuote(NamedTuple):
    origin: Region
    destination: Region
    cost_usd: Decimal
    carrier: str
    transit_days_estimate: int
    source: str  # "matrix" | "shipengine"


# Approximate transit day ranges (economy/postal) — informational only
_TRANSIT_DAYS: dict[tuple[Region, Region], int] = {
    (Region.US, Region.IN): 10,
    (Region.US, Region.EU): 8,
    (Region.US, Region.UK): 7,
    (Region.US, Region.JP): 9,
    (Region.US, Region.CN): 8,
    (Region.US, Region.AU): 10,
    (Region.US, Region.BR): 14,
    (Region.IN, Region.US): 10,
    (Region.IN, Region.EU): 10,
    (Region.IN, Region.UK): 9,
    (Region.IN, Region.JP): 10,
    (Region.IN, Region.CN): 8,
    (Region.IN, Region.AU): 11,
    (Region.IN, Region.BR): 18,
    (Region.EU, Region.US): 9,
    (Region.EU, Region.IN): 11,
    (Region.EU, Region.UK): 4,
    (Region.EU, Region.JP): 10,
    (Region.EU, Region.CN): 9,
    (Region.EU, Region.AU): 12,
    (Region.EU, Region.BR): 14,
    (Region.UK, Region.US): 8,
    (Region.UK, Region.IN): 10,
    (Region.UK, Region.EU): 4,
    (Region.UK, Region.JP): 10,
    (Region.UK, Region.CN): 9,
    (Region.UK, Region.AU): 11,
    (Region.UK, Region.BR): 14,
    (Region.JP, Region.US): 9,
    (Region.JP, Region.IN): 10,
    (Region.JP, Region.EU): 10,
    (Region.JP, Region.UK): 10,
    (Region.JP, Region.CN): 4,
    (Region.JP, Region.AU): 8,
    (Region.JP, Region.BR): 18,
    (Region.CN, Region.US): 14,
    (Region.CN, Region.IN): 12,
    (Region.CN, Region.EU): 14,
    (Region.CN, Region.UK): 12,
    (Region.CN, Region.JP): 5,
    (Region.CN, Region.AU): 12,
    (Region.CN, Region.BR): 20,
    (Region.AU, Region.US): 11,
    (Region.AU, Region.IN): 12,
    (Region.AU, Region.EU): 13,
    (Region.AU, Region.UK): 12,
    (Region.AU, Region.JP): 8,
    (Region.AU, Region.CN): 10,
    (Region.AU, Region.BR): 22,
    (Region.BR, Region.US): 14,
    (Region.BR, Region.IN): 20,
    (Region.BR, Region.EU): 15,
    (Region.BR, Region.UK): 14,
    (Region.BR, Region.JP): 20,
    (Region.BR, Region.CN): 18,
    (Region.BR, Region.AU): 22,
}


class ShippingResolver:
    """Resolve shipping costs for origin → destination pairs."""

    async def get_quote(
        self, origin: Region, destination: Region, *, weight_kg: float = 0.5
    ) -> ShippingQuote:
        """Return a shipping cost quote.

        Tries ShipEngine if the API key is configured; otherwise uses
        the static economy rate matrix (EMS/postal published rates 2024).
        """
        if _SHIPENGINE_KEY and weight_kg <= 5.0:
            try:
                return await self._shipengine_quote(origin, destination, weight_kg)
            except Exception as exc:
                _log.debug("shipping.shipengine_failed", error=str(exc), fallback="matrix")

        return self._matrix_quote(origin, destination, weight_kg)

    def _matrix_quote(
        self, origin: Region, destination: Region, weight_kg: float
    ) -> ShippingQuote:
        base = SHIPPING_MATRIX_USD.get((origin, destination), SHIPPING_DEFAULT_USD)
        # Weight surcharge: +$1.50 per kg above 0.5 kg
        extra = Decimal(str(max(0.0, weight_kg - 0.5) * 1.5))
        cost = (base + extra).quantize(Decimal("0.01"))
        transit = _TRANSIT_DAYS.get((origin, destination), 14)
        return ShippingQuote(
            origin=origin,
            destination=destination,
            cost_usd=cost,
            carrier="EMS/Postal",
            transit_days_estimate=transit,
            source="matrix",
        )

    async def _shipengine_quote(  # pragma: no cover
        self, origin: Region, destination: Region, weight_kg: float
    ) -> ShippingQuote:
        """Fetch real-time rate from ShipEngine API."""
        payload = {
            "carrier_ids": ["se-123456"],  # placeholder — set real carrier IDs
            "from_country_code": origin.value,
            "to_country_code": destination.value,
            "weight": {"value": weight_kg * 1000, "unit": "gram"},
        }
        async with httpx.AsyncClient(
            headers={"API-Key": _SHIPENGINE_KEY}, timeout=15.0
        ) as client:
            resp = await client.post(_SHIPENGINE_API, json=payload)
            resp.raise_for_status()
            data = resp.json()
            rates_list = data.get("rate_response", {}).get("rates", [])
            if rates_list:
                cheapest = min(rates_list, key=lambda r: r.get("shipping_amount", {}).get("amount", 999))
                cost = Decimal(str(cheapest["shipping_amount"]["amount"]))
                carrier = cheapest.get("carrier_friendly_name", "ShipEngine")
                transit = cheapest.get("delivery_days", 10)
                return ShippingQuote(
                    origin=origin,
                    destination=destination,
                    cost_usd=cost,
                    carrier=carrier,
                    transit_days_estimate=transit,
                    source="shipengine",
                )
        raise ValueError("ShipEngine returned no rates")
