"""
FTC advertising compliance rule engine — Phase 8.

Pure rule engine (no external API calls).  Applies 30+ compiled regex
patterns derived from FTC Enforcement Policies:
  - FTC Act Section 5 (unfair or deceptive acts/practices)
  - FTC Health Claims Guide (16 CFR Part 362)
  - FTC Endorsement Guides (16 CFR Part 255)
  - FTC Made in USA Standard
  - FTC Negative Option Rule (subscription traps)
"""

from __future__ import annotations

import structlog

from aegis.compliance.constants import FTC_VIOLATION_PATTERNS
from aegis.compliance.schemas import FTCViolation

_log = structlog.get_logger("aegis.compliance.ftc")

# FTC regulatory reference URLs (for audit trail)
_FTC_REFS: dict[str, str] = {
    "unsubstantiated_health_claim":  "16 CFR 362 / FTC Health Claims Guide",
    "false_fda_claim":               "FTC Act § 5 — deceptive advertising",
    "unsubstantiated_endorsement":   "16 CFR 255 — Endorsement Guides",
    "unsubstantiated_clinical_claim":"FTC Act § 5 — substantiation requirement",
    "deceptive_guarantee":           "FTC Act § 5 — deceptive claims",
    "absolute_safety_claim":         "FTC Act § 5 — false/misleading claims",
    "guaranteed_investment_return":  "FTC Act § 5 — investment fraud",
    "risk_free_investment_claim":    "FTC Act § 5 — deceptive financial claims",
    "unrealistic_income_claim":      "FTC Act § 5 — business opportunity rule",
    "deceptive_weight_loss_claim":   "FTC Weight-Loss Advertising Report 2022",
    "false_media_endorsement":       "16 CFR 255.5 — disclosure of connections",
    "made_in_usa_claim":             "FTC Made in USA Standard 2021",
}


class FTCRuleEngine:
    """FTC advertising rule engine — stateless, zero external calls."""

    def assess(
        self,
        product_title: str,
        product_description: str = "",
    ) -> tuple[list[FTCViolation], float]:
        """Return (violations, risk_score 0–1).

        Each pattern fires independently.  Risk = max severity across all hits.
        Multiple violations increase risk cumulatively up to 1.0.
        """
        combined = f"{product_title} {product_description}".strip()
        violations: list[FTCViolation] = []
        cumulative_risk = 0.0

        for pattern, violation_type, severity in FTC_VIOLATION_PATTERNS:
            match = pattern.search(combined)
            if match:
                matched_text = match.group(0)[:120]
                violations.append(
                    FTCViolation(
                        violation_type=violation_type,
                        matched_text=matched_text,
                        rule_reference=_FTC_REFS.get(violation_type, "FTC Act § 5"),
                        severity=severity,
                    )
                )
                # Additive risk with diminishing returns
                cumulative_risk = min(1.0, cumulative_risk + severity * (1.0 - cumulative_risk * 0.5))

        if violations:
            _log.info(
                "compliance.ftc_violations",
                product=product_title[:60],
                count=len(violations),
                risk_score=round(cumulative_risk, 3),
                types=[v.violation_type for v in violations],
            )

        return violations, cumulative_risk
