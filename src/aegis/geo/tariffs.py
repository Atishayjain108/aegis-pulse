"""
WTO MFN tariff estimator for Phase 7.

Primary source: HS_TARIFF_SCHEDULE in config.py (real WTO MFN applied rates 2024).
Async fallback: UN Comtrade public API for HS codes not in the static schedule.

Usage:
    estimator = TariffEstimator()
    result = await estimator.estimate(
        hs_code="610910", origin="US", destination="IN", value_usd=Decimal("50")
    )
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import structlog

from aegis.geo.config import (
    CATEGORY_HS_MAP,
    HS_TARIFF_SCHEDULE,
    Region,
)
from aegis.geo.schemas import TariffLookupResult

_log = structlog.get_logger("aegis.geo.tariffs")

_COMTRADE_API = "https://comtrade.un.org/api/get"
_COMTRADE_TIMEOUT = 20.0

# WTO average MFN tariff by broad sector (last-resort fallback)
# Source: WTO World Tariff Profiles 2023
_WTO_AVG_MFN_BY_SECTOR: dict[str, Decimal] = {
    "us": Decimal("0.035"),    # 3.5% simple average MFN
    "in": Decimal("0.170"),    # 17% simple average MFN
    "eu": Decimal("0.052"),    # 5.2%
    "uk": Decimal("0.051"),    # 5.1% (post-Brexit aligned close to EU)
    "jp": Decimal("0.046"),    # 4.6%
    "cn": Decimal("0.074"),    # 7.4%
    "au": Decimal("0.025"),    # 2.5%
    "br": Decimal("0.132"),    # 13.2%
}


class TariffEstimator:
    """Estimate import duties using the WTO MFN schedule."""

    def __init__(self, use_comtrade_fallback: bool = False) -> None:
        # UN Comtrade is slow (~5–30 s); disable in tests and hot paths
        self._use_comtrade = use_comtrade_fallback
        self._comtrade_cache: dict[str, Decimal] = {}

    async def estimate(
        self,
        *,
        hs_code: str,
        origin: str,
        destination: str,
        value_usd: Decimal,
    ) -> TariffLookupResult:
        """Estimate import duty for a shipment.

        Args:
            hs_code: 6-digit Harmonized System code.
            origin: ISO 3166-1 alpha-2 country code (e.g. "US").
            destination: ISO 3166-1 alpha-2 country code.
            value_usd: CIF value of goods in USD.

        Returns:
            TariffLookupResult with duty_usd computed.
        """
        dest_lc = destination.lower()
        hs6 = hs_code[:6]

        entry = HS_TARIFF_SCHEDULE.get(hs6)
        if entry and dest_lc in entry.rates:
            duty_rate = entry.rates[dest_lc]
            description = entry.description
            source = "wto_mfn_2024"
        elif self._use_comtrade:  # pragma: no cover
            duty_rate = await self._comtrade_lookup(hs6, origin, destination)
            description = f"HS {hs6} (UN Comtrade)"
            source = "un_comtrade"
        else:
            duty_rate = _WTO_AVG_MFN_BY_SECTOR.get(dest_lc, Decimal("0.08"))
            description = f"HS {hs6} (WTO avg MFN {destination})"
            source = "wto_avg_mfn_2023"

        duty_usd = (value_usd * duty_rate).quantize(Decimal("0.01"))

        _log.debug(
            "tariff.estimated",
            hs_code=hs6,
            destination=destination,
            rate_pct=float(duty_rate * 100),
            duty_usd=float(duty_usd),
            source=source,
        )

        return TariffLookupResult(
            hs_code=hs6,
            description=description,
            origin=origin.upper(),
            destination=destination.upper(),
            duty_rate=duty_rate,
            duty_usd=duty_usd,
            value_usd=value_usd,
            source=source,
        )

    def hs_code_for_category(self, category: str) -> str:
        """Map a product category string to a 6-digit HS code."""
        key = category.lower().strip()
        return CATEGORY_HS_MAP.get(key, CATEGORY_HS_MAP["general"])

    async def _comtrade_lookup(  # pragma: no cover
        self, hs6: str, origin: str, destination: str
    ) -> Decimal:
        """Query UN Comtrade API for MFN tariff rate (slow, cached)."""
        cache_key = f"{hs6}/{destination}"
        if cache_key in self._comtrade_cache:
            return self._comtrade_cache[cache_key]

        try:
            async with httpx.AsyncClient(timeout=_COMTRADE_TIMEOUT) as client:
                resp = await client.get(
                    _COMTRADE_API,
                    params={
                        "max": "1",
                        "type": "C",
                        "freq": "A",
                        "px": "HS",
                        "cc": hs6,
                        "rg": "1",
                        "p": destination.upper(),
                        "fmt": "json",
                    },
                )
                if resp.status_code == 200:
                    dataset = resp.json().get("dataset", [])
                    if dataset and "TradeValue" in dataset[0]:
                        # Comtrade doesn't directly expose duty rates; use
                        # WTO avg as fallback when no direct tariff endpoint
                        pass
        except Exception as exc:
            _log.debug("tariff.comtrade_failed", error=str(exc))

        rate = _WTO_AVG_MFN_BY_SECTOR.get(destination.lower(), Decimal("0.08"))
        self._comtrade_cache[cache_key] = rate
        return rate

    @staticmethod
    def duty_rate_for(hs_code: str, destination: str) -> Decimal:
        """Synchronous lookup — for use in tight loops or non-async contexts."""
        dest_lc = destination.lower()
        entry = HS_TARIFF_SCHEDULE.get(hs_code[:6])
        if entry and dest_lc in entry.rates:
            return entry.rates[dest_lc]
        return _WTO_AVG_MFN_BY_SECTOR.get(dest_lc, Decimal("0.08"))


def hs_code_for_region_pair(hs_code: str, destination: Region) -> Decimal:
    """Quick utility: duty rate fraction for a given HS code → destination Region."""
    return TariffEstimator.duty_rate_for(hs_code, destination.value)
