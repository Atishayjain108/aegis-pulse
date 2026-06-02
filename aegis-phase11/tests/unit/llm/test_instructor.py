"""tests/unit/llm/test_instructor.py — InstructorAdapter + schemas tests"""
from __future__ import annotations

import pytest


class TestInstructorSchemas:
    def test_scout_output_verdict_values(self):
        from aegis.llm.instructor.schemas import Priority, ScoutOutput, Verdict
        out = ScoutOutput(
            verdict=Verdict.PROCEED,
            confidence=0.85,
            priority=Priority.P0,
            rationale="Strong signal",
            opportunity_window_hours=48,
            primary_risk="Competition",
        )
        assert out.verdict == Verdict.PROCEED
        assert out.confidence == pytest.approx(0.85)

    def test_confidence_validation_bounds(self):
        from pydantic import ValidationError

        from aegis.llm.instructor.schemas import Priority, ScoutOutput, Verdict
        with pytest.raises(ValidationError):
            ScoutOutput(
                verdict=Verdict.PROCEED, confidence=1.5,  # out of bounds
                priority=Priority.P1, rationale="x",
                opportunity_window_hours=10, primary_risk="x",
            )

    def test_auditor_output_schema(self):
        from aegis.llm.instructor.schemas import AuditorOutput, RiskLevel, Verdict
        out = AuditorOutput(
            verdict=Verdict.HOLD,
            estimated_margin_pct=22.5,
            break_even_units=150,
            max_recommended_inventory=500,
            risk_level=RiskLevel.MEDIUM,
            monte_carlo_p10_margin=10.0,
            monte_carlo_p90_margin=35.0,
            rationale="Moderate opportunity",
        )
        assert out.estimated_margin_pct == 22.5

    def test_red_team_confidence_adjustment_bounds(self):
        from pydantic import ValidationError

        from aegis.llm.instructor.schemas import RedTeamOutput, Verdict
        with pytest.raises(ValidationError):
            RedTeamOutput(
                thesis_survives=True,
                falsification_attempts=[],
                strongest_counter="none",
                confidence_adjustment=0.5,  # Must be <= 0
                verdict=Verdict.PROCEED,
            )

    def test_sourcer_output_with_suppliers(self):
        from aegis.llm.instructor.schemas import SourcerOutput, SupplierInfo, Verdict
        out = SourcerOutput(
            verdict=Verdict.PROCEED,
            suppliers=[SupplierInfo(
                name="Alibaba Supplier", platform="alibaba",
                moq=100, unit_cost_usd=4.50,
                lead_time_days=14, quality_score=0.8,
            )],
            best_supplier_index=0,
            total_landed_cost_usd=7.50,
            rationale="Good margin",
        )
        assert len(out.suppliers) == 1
