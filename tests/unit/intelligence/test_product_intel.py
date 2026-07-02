"""Unit tests for the ProductIntelligenceEngine (synthesis pipeline + analyze).

The engine is a pure-Python, heuristic-first market analyst. These tests drive
the full synthesis path with synthetic harvested signals (no network) and assert
the grounded report fields, then exercise the helper math directly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aegis.intelligence.product_intel import (
    MarketReport,
    ProductIntelligenceEngine,
    ProductRecord,
)


def _signal(platform: str, title: str, price: float, *, rating=4.2, reviews=120,
            discount=10.0, brand=None, conf=0.8) -> dict:
    """A harvested signal dict in the shape _to_product expects."""
    ps: dict = {
        "rating": rating,
        "review_count": reviews,
        "discount_pct": discount,
        "currency": "INR",
        "image_url": "http://img/x.jpg",
    }
    if brand is not None:
        ps["brand"] = brand
    return {
        "platform": platform,
        "title": title,
        "url": f"http://{platform}/p",
        "price_amount": price,
        "source_confidence": conf,
        "platform_specific": ps,
    }


class _FakeHarvest:
    def __init__(self, signals: list) -> None:
        self.signals = signals


def _records() -> list[ProductRecord]:
    eng = ProductIntelligenceEngine()
    signals = [
        _signal("amazon_in", "boAt Wireless Earbuds Pro", 1999, rating=4.3, reviews=5000, brand="boAt"),
        _signal("flipkart", "boAt Wireless Earbuds Pro", 1499, rating=4.1, reviews=2000, brand="boAt"),
        _signal("myntra", "Noise Wireless Earbuds Air", 2499, rating=4.0, reviews=800, brand="Noise"),
        _signal("amazon_in", "Sony Wireless Earbuds WF", 8999, rating=4.6, reviews=300, brand="Sony"),
    ]
    products = eng._extract_products(signals, "wireless earbuds")
    # Mirror production: _normalize_currencies runs before _synthesize. All four
    # listings are INR (single currency) so price_usd == price (a valid, monotonic
    # comparison key — the same fallback the engine uses when FX is unreachable).
    for p in products:
        p.price_usd = p.price
    return products


class TestSynthesis:
    def test_synthesize_full_report(self) -> None:
        eng = ProductIntelligenceEngine()
        products = _records()
        assert len(products) == 4
        report = eng._synthesize("wireless earbuds", "ecommerce", [], products)

        assert report.product_count == 4
        assert "amazon_in" in report.platforms
        assert report.price_summary["available"] is True
        assert report.price_summary["min"] == 1499
        assert report.price_summary["max"] == 8999
        # boAt has the most listings → leads the competitor table.
        assert report.competitors[0]["brand"] == "boat"
        assert report.competitors[0]["listings"] == 2
        # Cross-platform arbitrage on the identical boAt product (1499 vs 1999).
        assert report.arbitrage
        assert report.arbitrage[0]["gap_pct"] > 8
        # Picks present.
        assert report.picks["budget"]["price"] == 1499
        assert report.picks["premium"]["price"] == 8999
        assert report.best_value
        assert report.evidence
        assert report.data_quality > 0.9
        assert report.executive_summary
        assert report.recommended_actions
        # to_dict is serializable.
        d = report.to_dict()
        assert d["query"] == "wireless earbuds"
        assert d["product_count"] == 4

    def test_price_bands_and_momentum(self) -> None:
        eng = ProductIntelligenceEngine()
        products = _records()
        bands = eng._price_bands([p for p in products if p.price])
        assert bands["available"] is True
        assert bands["budget_max"] <= bands["premium_min"]
        momentum = eng._momentum(products)
        assert momentum["total_reviews"] == 8100
        assert momentum["label"] in {"hot", "warming", "cool"}

    def test_opportunity_verdicts(self) -> None:
        eng = ProductIntelligenceEngine()
        report = eng._synthesize("wireless earbuds", "ecommerce", [], _records())
        report.demand = {"demand_score": 0.8, "level": "hot"}
        opp = eng._opportunity(report)
        assert opp["verdict"] in {"OPENING", "CONTESTED", "SATURATED_OR_COLD"}
        assert 0.0 <= opp["gap_score"] <= 1.0

        empty = MarketReport(
            query="x", topic_type="t", generated_at=report.generated_at,
            status="NO_DATA", status_reason="", product_count=0,
            platforms=[], sources_consulted=[], price_summary={"available": False},
            price_bands={}, competitors=[], top_rated=[], most_popular=[], best_value=[],
            biggest_discounts=[], arbitrage=[], momentum={}, picks={}, executive_summary="",
            recommended_actions=[], data_quality=0.0,
        )
        empty.demand = {"demand_score": 0.0}
        assert eng._opportunity(empty)["verdict"] == "INSUFFICIENT_DATA"

    def test_demand_snapshot(self) -> None:
        eng = ProductIntelligenceEngine()
        signals = [
            {"platform": "google_news", "title": "Wireless earbuds sales surge in India"},
            {"platform": "reddit", "title": "Best wireless earbuds under 2000?"},
            {"platform": "reddit", "title": "totally unrelated cooking post"},
        ]
        snap = eng._demand_snapshot("wireless earbuds", signals)
        assert snap["mention_count"] == 2  # the cooking post is filtered out
        assert snap["level"] in {"hot", "warm", "cool"}
        assert "reddit" in snap["sources"]

    def test_empty_products_summary_and_actions(self) -> None:
        eng = ProductIntelligenceEngine()
        report = eng._synthesize("nothing here", "ecommerce", [], [])
        assert report.product_count == 0
        assert "No product listings" in report.executive_summary
        assert report.recommended_actions


class TestFieldHelpers:
    def test_value_score(self) -> None:
        # value_score ranks on USD price; price_usd must be set (as it is after
        # _normalize_currencies). A raw price with no price_usd cannot be ranked.
        p = ProductRecord("amazon_in", "x", "b", 1000.0, "INR", 4.0, 100, None, None, None, 0.8,
                          price_usd=12.0)
        assert p.value_score() > 0
        no_price = ProductRecord("a", "x", "b", None, "INR", 4.0, 10, None, None, None, 0.8)
        assert no_price.value_score() == 0.0

    def test_brand_normalization_and_fuzzy(self) -> None:
        eng = ProductIntelligenceEngine()
        assert eng._normalize_brand("BOAT") == "boat"
        assert eng._normalize_brand("") == "unbranded"
        # Fuzzy typo correction folds onto a known brand.
        assert eng._normalize_brand("phillips") == "philips"

    def test_coerce_helpers(self) -> None:
        eng = ProductIntelligenceEngine()
        assert eng._coerce_float("12.5") == 12.5
        assert eng._coerce_float("nope") is None
        assert eng._coerce_float(None) is None
        assert eng._coerce_int("7.9") == 7
        assert eng._coerce_int("x") is None

    def test_query_tokens_and_title_match(self) -> None:
        eng = ProductIntelligenceEngine()
        tokens = eng._query_tokens("Best Wireless Earbuds online")
        assert "wireless" in tokens and "best" not in tokens
        assert eng._title_matches("Noise Wireless Buds", tokens)
        assert not eng._title_matches("Random Shoe", tokens)

    def test_infer_brand_from_title_and_explicit(self) -> None:
        eng = ProductIntelligenceEngine()
        # Known brand found among leading title tokens.
        assert eng._infer_brand("Philips BT3221 Trimmer", {}) == "philips"
        # Explicit ps brand wins.
        assert eng._infer_brand("some title", {"brand": "Sony"}) == "sony"
        # No recognizable brand → first meaningful token.
        assert eng._infer_brand("zzytx widget", {}) == "zzytx"

    def test_to_product_from_model_object(self) -> None:
        eng = ProductIntelligenceEngine()
        signal = SimpleNamespace(
            platform=SimpleNamespace(value="amazon_in"),
            title="boAt Wireless Earbuds",
            url="http://x/p",
            price=SimpleNamespace(amount=1599.0),
            confidence=SimpleNamespace(source_confidence=0.9),
            platform_specific={"rating": 4.4, "review_count": 50, "currency": "INR"},
        )
        rec = eng._to_product(signal)
        assert rec is not None
        assert rec.platform == "amazon_in"
        assert rec.price == 1599.0
        assert rec.confidence == 0.9
        assert rec.rating == 4.4


class TestAnalyzeWithPool:
    @pytest.mark.asyncio
    async def test_analyze_with_pool_uses_tenant(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict = {}

        async def fake_scrape_topic(query, **kwargs):
            seen.setdefault("tenants", []).append(kwargs.get("tenant_id"))
            return _FakeHarvest([_signal("amazon_in", "boAt Wireless Earbuds", 1999, brand="boAt")])

        monkeypatch.setattr("aegis.scrape.topic.scrape_topic", fake_scrape_topic)
        engine = ProductIntelligenceEngine(pool=object())
        report = await engine.analyze("wireless earbuds", use_llm=False, gate=False)
        assert report.product_count == 1
        # The market harvest received a resolved tenant UUID (pool path).
        assert any(t is not None for t in seen["tenants"])


class TestAnalyze:
    @pytest.mark.asyncio
    async def test_analyze_happy_path_no_llm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        market_signals = [
            _signal("amazon_in", "boAt Wireless Earbuds Pro", 1999, brand="boAt"),
            _signal("flipkart", "boAt Wireless Earbuds Pro", 1499, brand="boAt"),
            _signal("myntra", "Noise Wireless Earbuds Air", 2499, brand="Noise"),
        ]
        demand_signals = [
            {"platform": "google_news", "title": "Wireless earbuds demand booms"},
        ]

        async def fake_scrape_topic(query, **kwargs):
            # Demand harvest uses pool=None + the demand adapters; route by that.
            if kwargs.get("pool") is None and kwargs.get("dry_run") and kwargs.get(
                "adapter_override"
            ) and "google_news" in str(kwargs["adapter_override"]):
                return _FakeHarvest(demand_signals)
            return _FakeHarvest(market_signals)

        monkeypatch.setattr("aegis.scrape.topic.scrape_topic", fake_scrape_topic)

        engine = ProductIntelligenceEngine()
        report = await engine.analyze("wireless earbuds", use_llm=False, gate=False)

        assert report.product_count == 3
        assert report.llm_narrative == ""  # use_llm=False
        assert report.gated_pick == {}
        assert report.opportunity  # computed from demand + supply
        assert report.demand.get("mention_count", 0) >= 1

    @pytest.mark.asyncio
    async def test_analyze_llm_failure_is_silent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_scrape_topic(query, **kwargs):
            # Enough listings to clear the evidence floor so the LLM is actually
            # invoked (below the floor the verdict is deterministic, not LLM-driven).
            return _FakeHarvest([
                _signal("amazon_in", "boAt Wireless Earbuds", 1999, brand="boAt"),
                _signal("flipkart", "Noise Wireless Earbuds", 1499, brand="Noise"),
                _signal("myntra", "Sony Wireless Earbuds", 8999, brand="Sony"),
            ])

        async def boom(*a, **k):
            raise RuntimeError("gateway down")

        monkeypatch.setattr("aegis.scrape.topic.scrape_topic", fake_scrape_topic)
        monkeypatch.setattr("aegis.llm.bridge.agents_bridge.complete_for_agent", boom)

        engine = ProductIntelligenceEngine()
        report = await engine.analyze("wireless earbuds", use_llm=True, gate=False)
        # Grounded-or-silent: a failed LLM yields an empty narrative, never a crash.
        assert report.llm_narrative == ""
        assert report.product_count == 3


class TestStatusContract:
    """The data-status contract: never project confidence from empty/thin data."""

    def test_no_data_status_and_no_synthesis(self) -> None:
        eng = ProductIntelligenceEngine()
        report = eng._synthesize("ghost query", "ecommerce", [], [])
        report.demand = {"mention_count": 0}
        eng._apply_status(report)
        assert report.status == "NO_DATA"
        assert report.status_reason
        assert report.picks == {}
        assert "No marketplace listings" in report.executive_summary

    def test_insufficient_evidence_below_floor(self) -> None:
        eng = ProductIntelligenceEngine()
        products = eng._extract_products(
            [_signal("amazon_in", "boAt Wireless Earbuds", 1999, brand="boAt")],
            "wireless earbuds",
        )
        for p in products:
            p.price_usd = p.price
        report = eng._synthesize("wireless earbuds", "ecommerce", [], products)
        report.demand = {"mention_count": 5}
        eng._apply_status(report)
        assert report.status == "INSUFFICIENT_EVIDENCE"
        assert "evidence floor" in report.status_reason

    def test_ok_status_when_above_floor(self) -> None:
        eng = ProductIntelligenceEngine()
        products = _records()
        report = eng._synthesize("wireless earbuds", "ecommerce", [], products)
        report.demand = {"mention_count": 10}
        eng._apply_status(report)
        assert report.status == "OK"


class TestCurrencyNormalization:
    @pytest.mark.asyncio
    async def test_normalize_sets_price_usd(self) -> None:
        eng = ProductIntelligenceEngine()
        recs = [
            ProductRecord("amazon_in", "x", "b", 8300.0, "INR", 4.0, 10, None, None, None, 0.8),
            ProductRecord("amazon", "y", "b", 100.0, "USD", 4.0, 10, None, None, None, 0.8),
        ]
        await eng._normalize_currencies(recs)
        # USD record is unchanged; INR record is converted to a much smaller USD value.
        usd_rec = next(r for r in recs if r.currency == "USD")
        inr_rec = next(r for r in recs if r.currency == "INR")
        assert usd_rec.price_usd == 100.0
        assert inr_rec.price_usd is not None
        assert inr_rec.price_usd < inr_rec.price  # ₹8300 ≈ $100, not $8300

    @pytest.mark.asyncio
    async def test_mixed_currency_arbitrage_is_not_false(self) -> None:
        """A ₹ price and a $ price of similar real value must NOT show the
        absurd ~8000% gap raw cross-currency comparison would produce."""
        eng = ProductIntelligenceEngine()
        recs = [
            ProductRecord("flipkart", "boAt Buds", "boat", 8300.0, "INR", 4.0, 10, None, None, None, 0.8),
            ProductRecord("amazon", "boAt Buds", "boat", 100.0, "USD", 4.0, 10, None, None, None, 0.8),
        ]
        await eng._normalize_currencies(recs)
        arb = eng._arbitrage(recs)
        # Raw cross-currency math would report gap_pct ~= 8200%. USD-normalized,
        # the gap is a sane number (real FX spread only).
        assert all(r["gap_pct"] < 50 for r in arb)
        if arb:
            assert arb[0]["cheapest"]["price_usd"] < arb[0]["dearest"]["price_usd"]


class TestNarrativeGrounding:
    def test_ungrounded_figures_are_flagged(self) -> None:
        eng = ProductIntelligenceEngine()
        report = eng._synthesize("wireless earbuds", "ecommerce", [], _records())
        text = "Market is huge with 99999 units sold and a 73% margin. RECOMMENDATION: ENTER"
        grounded = eng._ground_narrative(text, report)
        assert "GROUNDING WARNING" in grounded
        assert "99999" in grounded

    def test_grounded_text_passes_through(self) -> None:
        eng = ProductIntelligenceEngine()
        report = eng._synthesize("wireless earbuds", "ecommerce", [], _records())
        # Use only figures that exist in the report.
        n = report.product_count
        text = f"Saw {n} listings. RECOMMENDATION: WATCH"
        grounded = eng._ground_narrative(text, report)
        assert "GROUNDING WARNING" not in grounded
