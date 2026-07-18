"""Tests for the risk-matrix scorer (fail-closed verdict logic)."""

from __future__ import annotations

from aegis.comply.matrix.scorer import RiskMatrixScorer
from aegis.comply.schemas import (
    ComplianceVerdict,
    CounterfeitSignal,
    Jurisdiction,
    RiskCategory,
    RuleHit,
    Severity,
)


def _hit(severity, category=RiskCategory.ADVERTISING, rid="R-1"):
    return RuleHit(
        rule_id=rid,
        name="test rule",
        severity=severity,
        category=category,
        jurisdiction=Jurisdiction.US,
    )


def test_no_signals_is_clear():
    scorer = RiskMatrixScorer()
    cats, agg, verdict, conf, reasons = scorer.score(
        rule_hits=[], trademark_matches=[], counterfeit_signals=[]
    )
    assert verdict is ComplianceVerdict.CLEAR
    assert agg == 0.0
    assert reasons == ()


def test_block_severity_forces_block_regardless_of_aggregate():
    scorer = RiskMatrixScorer()
    _, agg, verdict, _, reasons = scorer.score(
        rule_hits=[_hit(Severity.BLOCK, rid="FTC-HEALTH-001")],
        trademark_matches=[],
        counterfeit_signals=[],
    )
    assert verdict is ComplianceVerdict.BLOCK
    assert any("FTC-HEALTH-001" in r for r in reasons)


def test_warn_severity_flags():
    scorer = RiskMatrixScorer()
    _, _, verdict, _, _ = scorer.score(
        rule_hits=[_hit(Severity.WARN)], trademark_matches=[], counterfeit_signals=[]
    )
    assert verdict is ComplianceVerdict.FLAG


def test_strong_counterfeit_forces_block():
    scorer = RiskMatrixScorer()
    cf = CounterfeitSignal(brand="adidas", similarity=0.92, risk=0.95, reason="both")
    _, _, verdict, _, reasons = scorer.score(
        rule_hits=[], trademark_matches=[], counterfeit_signals=[cf]
    )
    assert verdict is ComplianceVerdict.BLOCK
    assert any("counterfeit:adidas" in r for r in reasons)


def test_weights_are_normalised():
    scorer = RiskMatrixScorer(weights={"trademark": 2.0, "counterfeit": 2.0})
    cats, _, _, _, _ = scorer.score(
        rule_hits=[], trademark_matches=[], counterfeit_signals=[]
    )
    total = sum(cs.weight for cs in cats)
    assert abs(total - 1.0) < 1e-6


def test_confidence_within_bounds():
    scorer = RiskMatrixScorer()
    _, _, _, conf, _ = scorer.score(
        rule_hits=[_hit(Severity.BLOCK)], trademark_matches=[], counterfeit_signals=[]
    )
    assert 0.30 <= conf <= 0.95
