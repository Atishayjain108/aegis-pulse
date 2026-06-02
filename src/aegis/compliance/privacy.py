"""
Privacy regulation risk assessor — Phase 8.

Regulations covered:
  - GDPR (EU/EEA/UK/CH)                  — Regulation (EU) 2016/679
  - DPDP (India)                          — Digital Personal Data Protection Act 2023
  - DSA (EU Digital Services Act)         — Regulation (EU) 2022/2065
  - GPSR (EU General Product Safety Reg.) — Regulation (EU) 2023/988
  - CCPA (California)                     — California Consumer Privacy Act 2018
  - PIPEDA (Canada)                       — Personal Information Protection Act

Rule engine — no external API calls required.
"""

from __future__ import annotations

import re

import structlog

from aegis.compliance.constants import (
    DATA_COLLECTING_CATEGORIES,
    DPDP_COUNTRIES,
    DSA_GPSR_COUNTRIES,
    GDPR_COUNTRIES,
)
from aegis.compliance.schemas import PrivacyRisk

_log = structlog.get_logger("aegis.compliance.privacy")

# Countries with strong data protection regimes → triggers elevated privacy risk
_CCPA_STATES = frozenset({"US"})  # CCPA applies across US when origin/destination
_PIPEDA_COUNTRIES = frozenset({"CA"})

# Product title / description keywords that indicate data collection
_DATA_SIGNAL_KEYWORDS: frozenset[str] = frozenset({
    "app", "application", "software", "iot", "smart", "connected",
    "tracker", "monitor", "camera", "wearable", "health data",
    "personal data", "location", "gps", "biometric", "fingerprint",
    "child", "children", "kids", "minor",
})

# Product safety regulations that apply to physical goods in EU
_GPSR_CATEGORIES: frozenset[str] = frozenset({
    "electronics", "toys", "children", "kids", "cosmetics", "beauty",
    "clothing", "apparel", "footwear", "furniture", "appliance",
    "electrical", "chemical", "food",
})


class PrivacyRiskAssessor:
    """Assess data-privacy and product-safety regulatory exposure."""

    def assess(
        self,
        product_title: str,
        product_description: str,
        origin_country: str,
        destination_country: str,
        category: str = "general",
    ) -> tuple[PrivacyRisk, float]:
        """Return (PrivacyRisk detail, risk_score 0–1).

        Risk scoring:
          GDPR destination + data-collecting product  → 0.80
          DPDP destination                            → 0.75
          DSA destination + digital product           → 0.70
          GPSR destination + physical product         → 0.50
          CCPA                                        → 0.35
          PIPEDA                                      → 0.30
          No special regulations                      → 0.05
        """
        dest = destination_country.upper()
        orig = origin_country.upper()

        regulations: list[str] = []
        risk = 0.05  # base (minimal privacy exposure everywhere)
        details: list[str] = []

        collects_data = self._product_collects_data(product_title, product_description, category)
        is_physical = not self._is_digital_product(product_title, product_description, category)
        is_eu_physical_category = category.lower() in _GPSR_CATEGORIES

        # --- GDPR / EEA ---
        if dest in GDPR_COUNTRIES:
            gdpr_risk = 0.60 if collects_data else 0.25
            risk = max(gdpr_risk, risk)
            regulations.append("GDPR")
            details.append(
                f"Destination {dest} is a GDPR jurisdiction. "
                + ("Product may collect personal data." if collects_data else "Data rights apply to all EU transactions.")
            )

        # --- India DPDP ---
        if dest in DPDP_COUNTRIES or orig in DPDP_COUNTRIES:
            dpdp_risk = 0.75 if collects_data else 0.30
            risk = max(dpdp_risk, risk)
            regulations.append("DPDP-2023")
            details.append(
                "India DPDP Act 2023 applies. "
                + ("Data processor / fiduciary obligations triggered." if collects_data else "Customer data handling rules apply.")
            )

        # --- EU DSA ---
        if dest in DSA_GPSR_COUNTRIES and not is_physical:
            dsa_risk = 0.55
            risk = max(dsa_risk, risk)
            regulations.append("DSA")
            details.append(
                f"EU Digital Services Act applies to digital/online services sold in {dest}."
            )

        # --- EU GPSR ---
        if dest in DSA_GPSR_COUNTRIES and is_eu_physical_category:
            gpsr_risk = 0.45
            risk = max(gpsr_risk, risk)
            regulations.append("GPSR")
            details.append(
                f"EU General Product Safety Regulation (2023/988) requires conformity marking for {category} sold in {dest}."
            )

        # --- CCPA (California/US) ---
        if dest in _CCPA_STATES and collects_data:
            ccpa_risk = 0.35
            risk = max(ccpa_risk, risk)
            regulations.append("CCPA")
            details.append("California Consumer Privacy Act (CCPA) applies to data-collecting products sold in the US.")

        # --- PIPEDA (Canada) ---
        if dest in _PIPEDA_COUNTRIES and collects_data:
            pipeda_risk = 0.30
            risk = max(pipeda_risk, risk)
            regulations.append("PIPEDA")
            details.append("PIPEDA (Canada) consent requirements apply.")

        # Boost risk for children's products (COPPA / GDPR Article 8)
        if any(k in product_title.lower() or k in product_description.lower()
               for k in ("child", "children", "kids", "toddler", "baby", "infant")):
            risk = min(1.0, risk + 0.25)
            if "GDPR" in regulations or "DPDP-2023" in regulations:
                details.append("GDPR Article 8 / COPPA: enhanced protection for children's data required.")
            regulations.append("COPPA")

        detail_text = " | ".join(details) if details else "No significant privacy regulations identified."
        risk_level = "high" if risk >= 0.65 else "medium" if risk >= 0.35 else "low"

        privacy_risk = PrivacyRisk(
            regulations_triggered=regulations,
            jurisdiction=dest,
            risk_level=risk_level,
            details=detail_text[:500],
        )

        if regulations:
            _log.info(
                "compliance.privacy_assessment",
                product=product_title[:60],
                dest=dest,
                regulations=regulations,
                risk_score=round(risk, 3),
            )

        return privacy_risk, risk

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _product_collects_data(
        self, title: str, description: str, category: str
    ) -> bool:
        # Use word-boundary matching throughout to avoid "app" matching "apparel", etc.
        combined = f"{title} {description} {category}".lower()
        words = set(re.split(r"\W+", combined))
        # Exact word match for single-word keywords; phrase match for multi-word ones
        kw_hit = any(
            (kw in words if " " not in kw else kw in combined)
            for kw in _DATA_SIGNAL_KEYWORDS
        )
        # Word-boundary match for category strings (handles "smart_device" → "smart device")
        cat_hit = any(
            bool(re.search(r"\b" + re.escape(cat.replace("_", " ")) + r"\b", combined))
            for cat in DATA_COLLECTING_CATEGORIES
        )
        return kw_hit or cat_hit

    def _is_digital_product(
        self, title: str, description: str, category: str
    ) -> bool:
        combined = f"{title} {description} {category}".lower()
        words = set(re.split(r"\W+", combined))
        digital_keywords = frozenset({
            "app", "software", "digital", "download", "subscription",
            "saas", "license", "service", "platform", "api",
        })
        return bool(words & digital_keywords)
