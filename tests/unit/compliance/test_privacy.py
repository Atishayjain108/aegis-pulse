"""Unit tests for Phase 8 Privacy risk assessor."""

from __future__ import annotations

import pytest

from aegis.compliance.privacy import PrivacyRiskAssessor
from aegis.compliance.schemas import PrivacyRisk


@pytest.fixture
def assessor() -> PrivacyRiskAssessor:
    return PrivacyRiskAssessor()


class TestPrivacyRiskAssessor:
    def test_us_to_us_non_data_product_low_risk(self, assessor: PrivacyRiskAssessor) -> None:
        risk_obj, score = assessor.assess("Cotton T-Shirt", "Plain apparel", "US", "US", "apparel")
        assert score < 0.3

    def test_gdpr_triggered_for_eu_destination(self, assessor: PrivacyRiskAssessor) -> None:
        _, score = assessor.assess("Smart Tracker", "IoT device", "US", "DE", "iot")
        assert "GDPR" in _.regulations_triggered
        assert score >= 0.5

    def test_dpdp_triggered_for_india_destination(self, assessor: PrivacyRiskAssessor) -> None:
        risk_obj, score = assessor.assess("Mobile App", "Data analytics app", "US", "IN", "app")
        assert "DPDP-2023" in risk_obj.regulations_triggered
        assert score >= 0.5

    def test_dpdp_triggered_india_origin(self, assessor: PrivacyRiskAssessor) -> None:
        risk_obj, score = assessor.assess("Software", "SaaS platform", "IN", "US", "software")
        assert "DPDP-2023" in risk_obj.regulations_triggered

    def test_dsa_triggered_for_digital_product_eu(self, assessor: PrivacyRiskAssessor) -> None:
        risk_obj, score = assessor.assess("SaaS Platform", "Cloud software", "US", "FR", "software")
        assert "DSA" in risk_obj.regulations_triggered

    def test_gpsr_triggered_for_physical_eu_product(self, assessor: PrivacyRiskAssessor) -> None:
        risk_obj, score = assessor.assess("Children Toy", "Plastic toy", "CN", "DE", "toys")
        assert "GPSR" in risk_obj.regulations_triggered

    def test_ccpa_triggered_for_us_data_product(self, assessor: PrivacyRiskAssessor) -> None:
        risk_obj, score = assessor.assess("Fitness Tracker", "Health wearable", "CN", "US", "wearable")
        assert "CCPA" in risk_obj.regulations_triggered

    def test_children_product_boosts_risk(self, assessor: PrivacyRiskAssessor) -> None:
        _, base_score = assessor.assess("Smart Speaker", "Home device", "US", "DE", "electronics")
        _, child_score = assessor.assess("Children Smart Speaker", "Home device for kids", "US", "DE", "electronics")
        assert child_score > base_score

    def test_coppa_for_children_product_eu(self, assessor: PrivacyRiskAssessor) -> None:
        risk_obj, _ = assessor.assess("Kids App", "Educational app for children", "US", "UK", "app")
        assert "COPPA" in risk_obj.regulations_triggered

    def test_sanctioned_country_no_special_privacy_risk(self, assessor: PrivacyRiskAssessor) -> None:
        # Privacy module doesn't check sanctions — that's AMLChecker's job
        risk_obj, score = assessor.assess("T-Shirt", "Plain apparel", "US", "IR", "apparel")
        # Iran is not in GDPR/DPDP zone — score should be low
        assert score < 0.4

    def test_risk_level_classification(self, assessor: PrivacyRiskAssessor) -> None:
        risk_obj_low, _ = assessor.assess("Shirt", "", "US", "AU", "apparel")
        risk_obj_high, _ = assessor.assess("Smart Tracker", "IoT", "US", "DE", "iot")
        assert risk_obj_low.risk_level in ("low", "medium")
        assert risk_obj_high.risk_level in ("medium", "high")

    def test_privacy_risk_model_fields(self, assessor: PrivacyRiskAssessor) -> None:
        risk_obj, score = assessor.assess("Product", "desc", "US", "DE")
        assert isinstance(risk_obj, PrivacyRisk)
        assert isinstance(risk_obj.regulations_triggered, list)
        assert isinstance(risk_obj.jurisdiction, str)
        assert risk_obj.risk_level in ("low", "medium", "high")

    def test_multiple_regulations_stacked(self, assessor: PrivacyRiskAssessor) -> None:
        # GDPR + DSA + COPPA all apply
        risk_obj, score = assessor.assess(
            "Kids Educational App",
            "Mobile app for children under 13",
            "US", "DE", "app"
        )
        assert len(risk_obj.regulations_triggered) >= 3
        assert score >= 0.7
