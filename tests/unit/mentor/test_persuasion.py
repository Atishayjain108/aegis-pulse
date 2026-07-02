"""Tests for the M4 Persuader (conviction engine — evidence-first, never hype)."""

from __future__ import annotations

from aegis.mentor.persuasion import Evidence, Persuader, PersuasionCase

_HYPE_WORDS = ("guaranteed", "guarantee", "huge", "amazing", "best ", "massive", "explode")


def _persuader() -> Persuader:
    return Persuader()


class TestRefusesWithoutEvidence:
    def test_no_evidence_refuses_and_stays_silent(self) -> None:
        case = _persuader().build_case([])
        assert isinstance(case, PersuasionCase)
        assert case.refused is True
        assert case.points == []
        assert case.objections == []
        assert not case.has_case
        assert "grounded" in case.reason.lower()


class TestObjectionModeling:
    def test_each_objection_answered_with_a_real_number(self) -> None:
        evidence = [
            Evidence(label="min viable gross margin", value=40.0, unit="%",
                     source="sector_pack:d2c_india", kind="margin"),
            Evidence(label="benchmark CAC", value=250.0, unit=" INR",
                     source="sector_pack:d2c_india", kind="cost"),
            Evidence(label="demand-intensity proxy", value=0.62,
                     source="execution_intel:buyer_demand_proxy", kind="demand"),
        ]
        case = _persuader().build_case(
            evidence, free_channels=["Organic marketplace listing"]
        )
        assert case.has_case
        concerns = {o.concern for o in case.objections}
        # All three classic objections are modeled and answered here.
        assert concerns == {"too saturated", "no capital", "won't sell"}
        # Every rebuttal cites a real number from the supplied evidence.
        for o in case.objections:
            assert any(str(int(e.value)) in o.rebuttal or f"{e.value:g}" in o.rebuttal
                       for e in evidence)
            assert o.source  # provenance always present
        # The "no capital" objection lands on the free channel as its next step.
        cap = next(o for o in case.objections if o.concern == "no capital")
        assert "Organic marketplace listing" in cap.next_step

    def test_objection_dropped_when_no_matching_number(self) -> None:
        # Only an audience number → "won't sell" can be answered, but
        # "too saturated" (needs margin/growth/demand) and "no capital"
        # (needs capital/cost) have NO matching evidence → dropped, not guessed.
        evidence = [
            Evidence(label="audience signals", value=12.0, unit=" signals",
                     source="signals:audience_proxy", kind="audience"),
        ]
        case = _persuader().build_case(evidence)
        concerns = {o.concern for o in case.objections}
        assert concerns == {"won't sell"}
        assert "too saturated" not in concerns
        assert "no capital" not in concerns

    def test_never_uses_hype_language(self) -> None:
        evidence = [
            Evidence(label="gross margin", value=55.0, unit="%",
                     source="sector_pack:d2c_india", kind="margin"),
            Evidence(label="demand proxy", value=0.7,
                     source="execution_intel:buyer_demand_proxy", kind="demand"),
        ]
        case = _persuader().build_case(evidence)
        blob = " ".join(case.render_lines()).lower()
        assert blob  # there is a case
        for word in _HYPE_WORDS:
            assert word not in blob


class TestEvidenceFirst:
    def test_points_carry_number_and_source(self) -> None:
        ev = Evidence(label="gross margin", value=42.5, unit="%",
                      source="sector_pack:d2c_india", kind="margin")
        case = _persuader().build_case([ev])
        assert case.points
        assert "42.5%" in case.points[0]
        assert "sector_pack:d2c_india" in case.points[0]

    def test_render_lines_includes_points_then_objections(self) -> None:
        ev = Evidence(label="demand proxy", value=0.5,
                      source="execution_intel:buyer_demand_proxy", kind="demand")
        case = _persuader().build_case([ev])
        lines = case.render_lines()
        assert lines[0].startswith("Evidence:")
        assert any(line.startswith("Objection —") for line in lines)
