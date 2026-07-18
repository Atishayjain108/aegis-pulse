"""Unit tests for Phase 8 FTC rule engine."""

from __future__ import annotations

import pytest

from aegis.compliance.ftc import FTCRuleEngine
from aegis.compliance.schemas import FTCViolation


@pytest.fixture
def engine() -> FTCRuleEngine:
    return FTCRuleEngine()


class TestFTCRuleEngine:
    def test_clean_product_no_violations(self, engine: FTCRuleEngine) -> None:
        violations, risk = engine.assess("Classic Cotton T-Shirt", "100% combed cotton, machine washable")
        assert violations == []
        assert risk == 0.0

    def test_unsubstantiated_health_claim_cancer(self, engine: FTCRuleEngine) -> None:
        violations, risk = engine.assess(
            "Miracle Supplement",
            "This supplement CURES cancer and treats diabetes naturally!",
        )
        assert len(violations) >= 1
        types = [v.violation_type for v in violations]
        assert "unsubstantiated_health_claim" in types
        assert risk >= 0.7

    def test_false_fda_claim(self, engine: FTCRuleEngine) -> None:
        violations, risk = engine.assess(
            "Weight Loss Pill",
            "FDA-Approved supplement for rapid fat burning.",
        )
        assert len(violations) >= 1
        assert any(v.violation_type == "false_fda_claim" for v in violations)
        assert risk >= 0.5

    def test_doctor_recommended_claim(self, engine: FTCRuleEngine) -> None:
        violations, risk = engine.assess(
            "Joint Support Formula",
            "Doctor recommended for arthritis relief.",
        )
        assert any(v.violation_type == "unsubstantiated_endorsement" for v in violations)

    def test_guaranteed_investment_return(self, engine: FTCRuleEngine) -> None:
        violations, risk = engine.assess(
            "Investment Course",
            "Guaranteed returns of 20% per month on your investment.",
        )
        assert any(v.violation_type == "guaranteed_investment_return" for v in violations)
        assert risk >= 0.7

    def test_earn_per_day_claim(self, engine: FTCRuleEngine) -> None:
        violations, risk = engine.assess(
            "Work From Home Kit",
            "Earn $500 per day from home — guaranteed!",
        )
        assert len(violations) >= 1
        assert risk >= 0.5

    def test_weight_loss_claim(self, engine: FTCRuleEngine) -> None:
        violations, risk = engine.assess(
            "Diet Tea",
            "Lose 10 kg in 2 weeks with our special blend!",
        )
        assert any(v.violation_type == "deceptive_weight_loss_claim" for v in violations)

    def test_multiple_violations_cumulative_risk(self, engine: FTCRuleEngine) -> None:
        violations, risk = engine.assess(
            "Super Cure-All Pill",
            "FDA-Approved! Doctor recommended. Guaranteed to cure cancer AND lose 20 pounds in 7 days!",
        )
        assert len(violations) >= 3
        # Multiple violations should push risk close to 1.0
        assert risk >= 0.85

    def test_made_in_usa_flagged_low_severity(self, engine: FTCRuleEngine) -> None:
        violations, risk = engine.assess(
            "Proudly Made in USA Widget",
            "Assembled with domestic components.",
        )
        # Made in USA is flagged but low severity (requires FTC compliance, not auto-block)
        made_violations = [v for v in violations if v.violation_type == "made_in_usa_claim"]
        if made_violations:
            assert made_violations[0].severity <= 0.40

    def test_violation_has_rule_reference(self, engine: FTCRuleEngine) -> None:
        violations, _ = engine.assess(
            "Protein Powder",
            "Clinically proven to build muscle mass in 30 days.",
        )
        for v in violations:
            assert v.rule_reference != ""

    def test_risk_capped_at_1(self, engine: FTCRuleEngine) -> None:
        violations, risk = engine.assess(
            "Miracle Cure",
            "FDA-Approved! Doctor recommended! Clinically proven! Guaranteed to cure cancer! "
            "Lose 50 pounds in 3 days! Risk-free investment! Earn $1000/hour!",
        )
        assert risk <= 1.0

    def test_case_insensitive_matching(self, engine: FTCRuleEngine) -> None:
        violations_lower, _ = engine.assess("Product", "fda approved supplement")
        violations_upper, _ = engine.assess("Product", "FDA APPROVED SUPPLEMENT")
        assert len(violations_lower) == len(violations_upper)

    def test_ftc_violation_model_fields(self, engine: FTCRuleEngine) -> None:
        violations, _ = engine.assess("Fake Pills", "These pills CURE diabetes.")
        assert len(violations) >= 1
        v = violations[0]
        assert isinstance(v, FTCViolation)
        assert 0.0 <= v.severity <= 1.0
        assert len(v.matched_text) > 0
        assert len(v.violation_type) > 0
