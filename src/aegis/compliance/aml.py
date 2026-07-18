"""
AML (Anti-Money Laundering) + KYC + Sanctions checker — Phase 8.

Three-layer approach:
  Layer 1 (instant):  Country-level OFAC sanctions check (static list).
  Layer 2 (instant):  FATF high-risk / grey-list check (static list).
  Layer 3 (live):     Trade.gov Consolidated Screening List (CSL) entity search.
                      Requires AEGIS_COMPLY_TRADE_GOV_API_KEY (free registration).
                      Falls back gracefully when key absent.

Data sources:
  - OFAC country programs  https://www.treasury.gov/resource-center/sanctions/
  - FATF grey/black list   https://www.fatf-gafi.org/en/topics/high-risk-jurisdictions.html
  - Trade.gov CSL API      https://api.trade.gov/gateway/v1/consolidated_screening_list/
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from aegis.compliance.config import ComplianceSettings
from aegis.compliance.constants import (
    FATF_HIGH_RISK,
    OFAC_SANCTIONED_COUNTRIES,
    TRADE_GOV_CSL_URL,
)
from aegis.compliance.schemas import SanctionMatch

_log = structlog.get_logger("aegis.compliance.aml")

# OFAC programs by country (abbreviated; full list at treasury.gov)
_OFAC_COUNTRY_PROGRAM: dict[str, str] = {
    "CU": "CUBA",
    "IR": "IRAN",
    "KP": "NPWMD (North Korea)",
    "SY": "SYRIA",
    "RU": "RUSSIA-EO14024",
    "BY": "BELARUS-EO14038",
    "VE": "VENEZUELA-EO13884",
    "MM": "BURMA-EO14014",
    "YE": "YEMEN (Houthis - EO14005)",
    "SD": "SUDAN",
    "SS": "SOUTH-SUDAN",
    "CD": "DRC",
    "CF": "CAR",
    "LY": "LIBYA",
    "SO": "SOMALIA",
    "ZW": "ZIMBABWE",
}


class AMLChecker:
    """AML / sanctions / KYC risk checker."""

    def __init__(self, settings: ComplianceSettings | None = None) -> None:
        self._cfg = settings or ComplianceSettings()

    async def check_transaction(
        self,
        origin_country: str,
        destination_country: str,
        entity_names: list[str] | None = None,
    ) -> tuple[list[SanctionMatch], float]:
        """Return (sanction_matches, risk_score 0–1).

        Risk levels:
          OFAC-sanctioned country       → 0.99 (hard block)
          FATF black list (e.g. KP, SY) → 0.95
          FATF grey list                → 0.50
          Entity match in CSL           → 0.90
          No hits                       → 0.02 (base AML risk)
        """
        orig = origin_country.upper()
        dest = destination_country.upper()
        matches: list[SanctionMatch] = []

        # Layer 1: OFAC country sanctions (instant)
        for country in (orig, dest):
            if country in OFAC_SANCTIONED_COUNTRIES:
                program = _OFAC_COUNTRY_PROGRAM.get(country, "OFAC-COMPREHENSIVE")
                matches.append(
                    SanctionMatch(
                        match_type="country_sanction",
                        matched_value=country,
                        program=program,
                        source="ofac_country_list",
                        risk_score=0.99,
                    )
                )
                _log.error(
                    "compliance.ofac_sanction",
                    country=country,
                    program=program,
                    error_code="AEGIS-COMPLY-0008",
                )

        # Layer 2: FATF high-risk check (instant)
        for country in (orig, dest):
            if country in FATF_HIGH_RISK and country not in OFAC_SANCTIONED_COUNTRIES:
                # Black-listed FATF (KP, SY, MM, YE) = very high
                # Grey-listed = medium
                fatf_risk = 0.95 if country in {"KP", "SY", "MM", "YE", "AF"} else 0.50
                matches.append(
                    SanctionMatch(
                        match_type="fatf_high_risk",
                        matched_value=country,
                        program="FATF-HIGH-RISK-2024",
                        source="fatf_grey_list",
                        risk_score=fatf_risk,
                    )
                )
                _log.warning(
                    "compliance.fatf_high_risk",
                    country=country,
                    risk=fatf_risk,
                )

        # Layer 3: Trade.gov CSL entity screening (live, optional)
        if entity_names and self._cfg.trade_gov_api_key:
            entity_matches = await self._screen_entities(entity_names)
            matches.extend(entity_matches)

        # Compute overall AML risk
        if not matches:
            return [], 0.02  # baseline AML risk

        risk = max(m.risk_score for m in matches)
        _log.info(
            "compliance.aml_assessment",
            origin=orig,
            dest=dest,
            matches=len(matches),
            risk_score=round(risk, 3),
        )
        return matches, risk

    # ------------------------------------------------------------------
    # Trade.gov Consolidated Screening List API
    # ------------------------------------------------------------------

    async def _screen_entities(self, entity_names: list[str]) -> list[SanctionMatch]:
        """
        Trade.gov CSL API — https://api.trade.gov/gateway/v1/consolidated_screening_list/search

        Free with registration.  Aggregates:
          SDN (OFAC), DPL (BIS Denied Parties), EL (BIS Entity List),
          Unverified List, FSE (OFAC Foreign Sanctions Evaders),
          and 10+ more lists.
        """
        if not self._cfg.trade_gov_api_key:
            return []

        hits: list[SanctionMatch] = []
        for name in entity_names[:5]:  # limit to 5 names per call
            try:
                async with httpx.AsyncClient(timeout=self._cfg.api_timeout_s) as client:
                    resp = await client.get(
                        TRADE_GOV_CSL_URL,
                        params={"name": name, "sources": "SDN,DPL,EL", "fuzzy_name": "true"},
                        headers={"subscription-key": self._cfg.trade_gov_api_key},
                    )
                if resp.status_code != 200:
                    _log.debug("compliance.csl_non_200", status=resp.status_code)
                    continue

                data: dict[str, Any] = resp.json()
                for result in data.get("results", [])[:3]:
                    hits.append(
                        SanctionMatch(
                            match_type="entity_match",
                            matched_value=str(result.get("name") or name),
                            program=", ".join(result.get("programs") or []),
                            source=f"trade_gov_csl:{result.get('source', 'unknown')}",
                            risk_score=0.90,
                        )
                    )

            except Exception as exc:
                _log.debug("compliance.csl_error", error=str(exc)[:120])

        return hits
