"""Tests for the counterfeit-risk detector."""

from __future__ import annotations

from aegis.comply.counterfeit.detector import CounterfeitDetector, price_zscore
from aegis.comply.schemas import ComplianceRequest, TrademarkMatch


def _req(**kw):
    return ComplianceRequest(trend_id="t", title=kw.pop("title", ""), **kw)


def _tm(mark, similarity, *, owner="x", category="fashion"):
    return TrademarkMatch(mark=mark, owner=owner, similarity=similarity)


def test_price_zscore_helper():
    assert price_zscore(50.0, (100.0, 25.0)) == (50.0 - 100.0) / 25.0
    assert price_zscore(100.0, (100.0, 0.0)) is None  # zero std -> undefined


def test_typosquat_match_produces_signal():
    det = CounterfeitDetector()
    matches = [_tm("adidas", 0.92)]
    signals = det.detect(_req(title="Addidas shoes", category="footwear", price=20.0), matches)
    assert signals
    assert signals[0].brand == "adidas"
    assert signals[0].risk >= 0.85


def test_cheap_exact_brand_flags_counterfeit():
    det = CounterfeitDetector()
    matches = [_tm("gucci", 1.0)]
    signals = det.detect(
        _req(title="Gucci bag", category="fashion", price=5.0, price_baseline=(500.0, 100.0)),
        matches,
    )
    assert signals
    assert signals[0].price_zscore is not None and signals[0].price_zscore < 0


def test_typosquat_plus_cheap_is_near_certain():
    det = CounterfeitDetector()
    matches = [_tm("adidas", 0.92)]
    signals = det.detect(
        _req(title="Addidas", category="footwear", price=3.0, price_baseline=(120.0, 20.0)),
        matches,
    )
    assert signals and signals[0].risk >= 0.90


def test_no_trademark_no_counterfeit_signal():
    det = CounterfeitDetector()
    assert det.detect(_req(title="generic mug", price=10.0), []) == []


def test_signals_sorted_by_risk_desc():
    det = CounterfeitDetector()
    matches = [_tm("gucci", 1.0), _tm("adidas", 0.92)]
    signals = det.detect(
        _req(title="Gucci Addidas", category="fashion", price=2.0, price_baseline=(300.0, 50.0)),
        matches,
    )
    risks = [s.risk for s in signals]
    assert risks == sorted(risks, reverse=True)
