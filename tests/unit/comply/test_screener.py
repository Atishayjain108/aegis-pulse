"""Tests for the deterministic trademark screener."""

from __future__ import annotations

from aegis.comply.schemas import ComplianceRequest
from aegis.comply.trademark.screener import TrademarkScreener


def _req(**kw):
    return ComplianceRequest(trend_id="t", title=kw.pop("title", ""), **kw)


def test_exact_brand_mention_matches_with_full_similarity():
    sc = TrademarkScreener()
    matches = sc.screen(_req(title="Gucci handbag", brand_mentions=("Gucci",)))
    assert any(m.mark == "gucci" and m.similarity == 1.0 for m in matches)


def test_typosquat_lands_in_fuzzy_band():
    sc = TrademarkScreener()
    matches = sc.screen(_req(title="Addidas trainers", brand_mentions=("Addidas",)))
    assert matches, "typosquat 'Addidas' should match 'adidas'"
    top = matches[0]
    assert top.mark == "adidas"
    assert 0.82 <= top.similarity < 1.0


def test_unrelated_text_yields_no_matches():
    sc = TrademarkScreener()
    assert sc.screen(_req(title="Handmade ceramic mug", description="kiln fired")) == []


def test_screener_is_network_free_and_local_source():
    sc = TrademarkScreener()
    matches = sc.screen(_req(title="Rolex watch", brand_mentions=("Rolex",)))
    assert matches and all(m.source == "local" for m in matches)


def test_threshold_is_configurable():
    strict = TrademarkScreener(threshold=0.999)
    # A typosquat below the strict threshold should now be filtered out.
    assert strict.screen(_req(title="Addidas", brand_mentions=("Addidas",))) == []
