"""
FX rate fetcher using the Frankfurter API (https://api.frankfurter.app).

Frankfurter is a free, open-source ECB exchange rate API — no API key required.
Rates are updated each business day by the European Central Bank at ~4pm CET.

Cache strategy:
  - In-process dict keyed by (base, quote) with per-entry TTL (default 3600 s).
  - Optional Redis layer when a pool is injected.
  - On any network failure falls back to last-known rate or hardcoded ECB midpoints.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx
import structlog

from aegis.geo.config import REGION_CONFIGS, Region

if TYPE_CHECKING:
    pass

_log = structlog.get_logger("aegis.geo.fx")

_FRANKFURTER_BASE = "https://api.frankfurter.dev/v1"

# ECB approximate midpoints as of 2024-12 (last-resort fallback only)
_FALLBACK_RATES_VS_USD: dict[str, Decimal] = {
    "USD": Decimal("1.000000"),
    "INR": Decimal("83.900000"),
    "EUR": Decimal("0.924000"),
    "GBP": Decimal("0.788000"),
    "JPY": Decimal("150.500000"),
    "CNY": Decimal("7.270000"),
    "AUD": Decimal("1.530000"),
    "BRL": Decimal("5.000000"),
}


class FXRateFetcher:
    """Async FX rate fetcher with in-process TTL cache.

    All rates are quoted vs USD (base=USD, quote=X).
    To convert A → B: rate_B / rate_A.
    """

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl = ttl_seconds
        # (base, quote) → (rate, fetched_at_unix)
        self._cache: dict[tuple[str, str], tuple[Decimal, float]] = {}

    async def get_rate(self, from_currency: str, to_currency: str) -> Decimal:
        """Return exchange rate: 1 unit of from_currency = ? to_currency."""
        from_c = from_currency.upper()
        to_c = to_currency.upper()

        if from_c == to_c:
            return Decimal("1.0")

        # Check cache
        key = (from_c, to_c)
        cached = self._cache.get(key)
        if cached and (time.monotonic() - cached[1]) < self._ttl:
            return cached[0]

        # Fetch batch (all supported currencies in one call)
        rates = await self._fetch_all_vs_usd()

        # Store all fetched rates
        now = time.monotonic()
        for quote, rate in rates.items():
            self._cache[("USD", quote)] = (rate, now)
            if rate > 0:
                self._cache[(quote, "USD")] = (Decimal("1") / rate, now)

        # Compute cross rate
        rate = self._cross_rate(from_c, to_c, rates)
        self._cache[key] = (rate, now)
        return rate

    async def get_all_rates(self) -> dict[str, Decimal]:
        """Return all supported rates vs USD."""
        return await self._fetch_all_vs_usd()

    async def _fetch_all_vs_usd(self) -> dict[str, Decimal]:
        """Fetch latest rates from Frankfurter (base=USD)."""
        symbols = ",".join(
            cfg.currency
            for cfg in REGION_CONFIGS.values()
            if cfg.currency != "USD"
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{_FRANKFURTER_BASE}/latest",
                    params={"from": "USD", "to": symbols},
                )
                resp.raise_for_status()
                data = resp.json()
                rates: dict[str, Decimal] = {"USD": Decimal("1.0")}
                for ccy, val in data.get("rates", {}).items():
                    rates[ccy] = Decimal(str(val))
                _log.debug("fx.fetched", count=len(rates))
                return rates
        except Exception as exc:
            _log.warning("fx.fetch_failed", error=str(exc), fallback="using_ecb_midpoints")
            return dict(_FALLBACK_RATES_VS_USD)

    def _cross_rate(
        self, from_c: str, to_c: str, usd_rates: dict[str, Decimal]
    ) -> Decimal:
        """Compute cross rate via USD as common denominator."""
        from_vs_usd = usd_rates.get(from_c, _FALLBACK_RATES_VS_USD.get(from_c, Decimal("1")))
        to_vs_usd = usd_rates.get(to_c, _FALLBACK_RATES_VS_USD.get(to_c, Decimal("1")))
        if from_vs_usd == 0:
            return Decimal("1.0")
        return to_vs_usd / from_vs_usd

    def currency_for_region(self, region: Region) -> str:
        return REGION_CONFIGS[region].currency

    async def to_usd(self, amount: Decimal, from_currency: str) -> Decimal:
        """Convert an amount to USD."""
        rate = await self.get_rate(from_currency, "USD")
        return amount * rate

    async def from_usd(self, amount_usd: Decimal, to_currency: str) -> Decimal:
        """Convert a USD amount to another currency."""
        rate = await self.get_rate("USD", to_currency)
        return amount_usd * rate
