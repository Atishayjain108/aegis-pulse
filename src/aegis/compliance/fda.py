"""
FDA import restriction + enforcement checker — Phase 8.

Uses OpenFDA REST API (https://api.fda.gov/) — free, no key needed for
basic usage (1 000 req/day).  Set AEGIS_COMPLY_FDA_API_KEY for 120 000/day.

Endpoints queried:
  - GET /drug/enforcement.json   — drug recalls + import alerts
  - GET /food/enforcement.json   — food/supplement recalls
  - GET /device/enforcement.json — medical device recalls

Two-layer check:
  Layer 1 (fast, <1 ms): keyword match against hard-coded banned product list.
  Layer 2 (live,  ~3 s): OpenFDA enforcement query by product description.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from aegis.compliance.config import ComplianceSettings
from aegis.compliance.constants import FDA_API_BASE, FDA_BANNED_KEYWORDS, FDA_HIGH_RISK_CATEGORIES
from aegis.compliance.schemas import FDAEnforcement

_log = structlog.get_logger("aegis.compliance.fda")

_FDA_ENDPOINTS = ["drug", "food", "device"]
_ENFORCEMENT_SUFFIX = "/enforcement.json"

# Recall classifications and their risk contribution
_CLASS_RISK: dict[str, float] = {
    "Class I": 0.95,   # serious adverse health consequences or death
    "Class II": 0.65,  # temporary/reversible adverse health consequences
    "Class III": 0.30, # unlikely to cause adverse health consequences
}


class FDAChecker:
    """Check product against FDA import bans and active enforcement actions."""

    def __init__(self, settings: ComplianceSettings | None = None) -> None:
        self._cfg = settings or ComplianceSettings()
        self._cache: dict[str, tuple[list[FDAEnforcement], float, float]] = {}

    async def check_product(
        self,
        product_title: str,
        origin_country: str,
        category: str = "general",
    ) -> tuple[list[FDAEnforcement], float]:
        """Return (enforcements, risk_score 0–1).

        risk_score 0.0 = no FDA concerns.
        risk_score 0.95 = matches hard-coded banned keyword (near-certain block).
        """
        cache_key = f"{product_title.lower().strip()}|{origin_country.upper()}"
        if cache_key in self._cache:
            enforcements, risk, expire_at = self._cache[cache_key]
            if time.monotonic() < expire_at:
                return enforcements, risk

        enforcements: list[FDAEnforcement] = []
        risk = 0.0

        # Layer 1: hard-coded banned keyword check (zero latency)
        banned_risk = self._check_banned_keywords(product_title)
        if banned_risk >= 0.9:
            # Immediate block — no need to call FDA API
            self._store(cache_key, [], banned_risk)
            _log.warning(
                "compliance.fda_banned_keyword",
                product=product_title[:60],
                risk=banned_risk,
            )
            return [], banned_risk

        # Layer 2: live OpenFDA API search
        live_enforcements = await self._search_openfda(product_title, category)
        enforcements.extend(live_enforcements)

        # Compute risk from enforcement records
        if enforcements:
            risk = self._compute_fda_risk(enforcements)
        else:
            risk = banned_risk  # may be >0 even without live match (keyword hints)

        self._store(cache_key, enforcements, risk)

        _log.info(
            "compliance.fda_check",
            product=product_title[:60],
            origin=origin_country,
            enforcements=len(enforcements),
            risk_score=round(risk, 3),
        )
        return enforcements, risk

    # ------------------------------------------------------------------
    # Layer 1: hard-coded banned keyword check
    # ------------------------------------------------------------------

    def _check_banned_keywords(self, product_title: str) -> float:
        title_lower = product_title.lower()
        for keyword in FDA_BANNED_KEYWORDS:
            if keyword in title_lower:
                return 0.95
        # Elevated but not certain for high-risk categories
        for category in FDA_HIGH_RISK_CATEGORIES:
            if category.replace("_", " ") in title_lower:
                return 0.15  # flag for live API check, not auto-block
        return 0.0

    # ------------------------------------------------------------------
    # Layer 2: OpenFDA enforcement API
    # ------------------------------------------------------------------

    async def _search_openfda(
        self, product_title: str, category: str
    ) -> list[FDAEnforcement]:
        """Query OpenFDA enforcement records for product description matches."""
        # Build a short search query from the most distinctive words
        words = [
            w.strip(".,!?\"'()").lower()
            for w in product_title.split()
            if len(w) >= 4
        ]
        query_terms = " ".join(words[:4])
        if not query_terms.strip():
            return []

        # Determine which FDA endpoint to query based on category
        endpoints = self._pick_endpoints(category)

        results: list[FDAEnforcement] = []
        params: dict[str, Any] = {
            "search": f'product_description:"{query_terms}"',
            "limit": 5,
        }
        if self._cfg.fda_api_key:
            params["api_key"] = self._cfg.fda_api_key

        for endpoint in endpoints:
            url = f"{FDA_API_BASE}/{endpoint}{_ENFORCEMENT_SUFFIX}"
            try:
                async with httpx.AsyncClient(timeout=self._cfg.api_timeout_s) as client:
                    resp = await client.get(url, params=params)

                if resp.status_code == 404:
                    # 404 from OpenFDA means no results for this query (normal)
                    continue
                if resp.status_code == 429:
                    _log.warning("compliance.fda_rate_limited", endpoint=endpoint)
                    continue
                if resp.status_code != 200:
                    _log.debug("compliance.fda_non_200", status=resp.status_code, endpoint=endpoint)
                    continue

                data = resp.json()
                parsed = self._parse_openfda_response(data, endpoint)
                results.extend(parsed)

            except Exception as exc:
                _log.debug("compliance.fda_api_error", endpoint=endpoint, error=str(exc)[:120])

        return results

    def _parse_openfda_response(
        self, data: dict[str, Any], endpoint: str
    ) -> list[FDAEnforcement]:
        records: list[FDAEnforcement] = []
        for item in data.get("results", []):
            records.append(
                FDAEnforcement(
                    recall_number=str(item.get("recall_number") or f"{endpoint}-unknown"),
                    reason=str(item.get("reason_for_recall") or "")[:300],
                    product_description=str(item.get("product_description") or "")[:300],
                    classification=str(item.get("classification") or ""),
                    status=str(item.get("status") or "Ongoing"),
                    distribution=str(item.get("distribution_pattern") or "")[:200],
                    source=f"openfda_{endpoint}",
                )
            )
        return records

    # ------------------------------------------------------------------
    # Risk computation
    # ------------------------------------------------------------------

    def _compute_fda_risk(self, enforcements: list[FDAEnforcement]) -> float:
        if not enforcements:
            return 0.0
        max_risk = 0.0
        for enf in enforcements:
            class_risk = _CLASS_RISK.get(enf.classification, 0.45)
            # Ongoing recall is higher risk than completed
            status_mult = 1.0 if enf.status in ("Ongoing", "In-Progress") else 0.5
            max_risk = max(max_risk, class_risk * status_mult)
        return min(1.0, max_risk)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pick_endpoints(self, category: str) -> list[str]:
        cat = category.lower()
        if any(k in cat for k in ("drug", "pharma", "medication", "supplement")):
            return ["drug", "food"]
        if any(k in cat for k in ("device", "medical")):
            return ["device"]
        if any(k in cat for k in ("food", "beverage", "cosmetic", "beauty")):
            return ["food"]
        # General: check all three (but limit to 2 to reduce latency)
        return ["drug", "food"]

    def _store(self, key: str, enforcements: list[FDAEnforcement], risk: float) -> None:
        expire_at = time.monotonic() + self._cfg.fda_cache_ttl_hours * 3600
        self._cache[key] = (enforcements, risk, expire_at)
