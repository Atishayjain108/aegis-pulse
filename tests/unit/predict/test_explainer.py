"""Unit tests for aegis.predict.causal.explainer."""
from __future__ import annotations

from aegis.predict.causal.explainer import generate_explanation


def _gen(**kw):
    defaults: dict = {
        "trend_id": "t1",
        "verdict": "ENTER",
        "score": 0.80,
        "confidence": 0.75,
        "velocity_1h": 6.0,
        "velocity_6h": 2.5,
        "velocity_24h": 1.0,
        "sentiment": 0.72,
        "commercial_intent": 0.80,
        "novelty": 0.50,
        "coordination_risk": 0.10,
        "signal_count": 100,
        "unique_authors": 30,
        "platforms": ["hacker_news", "reddit_rss"],
    }
    defaults.update(kw)
    return generate_explanation(**defaults)


class TestGenerateExplanation:
    def test_enter_verdict_returns_nonempty(self):
        exp, cf, drivers = _gen(verdict="ENTER")
        assert exp
        assert cf
        assert drivers

    def test_hold_verdict_counterfactual_says_enter(self):
        _, cf, _ = _gen(verdict="HOLD", velocity_1h=1.0)
        assert "ENTER" in cf or "shift" in cf.lower()

    def test_block_verdict_counterfactual_says_hold(self):
        _, cf, _ = _gen(verdict="BLOCK", coordination_risk=0.60)
        assert "HOLD" in cf or "coordination_risk" in cf

    def test_high_coordination_risk_is_primary_driver(self):
        _, _, drivers = _gen(verdict="ENTER", coordination_risk=0.80, velocity_1h=0.1)
        assert "coordination_risk" in drivers

    def test_high_velocity_1h_detected(self):
        exp, _, drivers = _gen(velocity_1h=12.0, velocity_6h=0.1, commercial_intent=0.1)
        assert "velocity_1h" in drivers
        assert "12.0" in exp

    def test_no_drivers_produces_fallback_explanation(self):
        exp, cf, drivers = _gen(
            velocity_1h=2.0,
            velocity_6h=1.0,
            commercial_intent=0.5,
            coordination_risk=0.1,
            novelty=0.3,
            sentiment=0.5,
        )
        assert exp
        assert cf

    def test_explanation_under_300_words(self):
        exp, _, _ = _gen()
        assert len(exp.split()) < 300

    def test_multiple_platforms_included_in_explanation(self):
        exp, _, _ = _gen(
            platforms=["hacker_news", "reddit_rss", "google_news"],
            signal_count=50,
            unique_authors=40,
        )
        assert "3 platforms" in exp or "hacker_news" in exp

    def test_low_velocity_detected_as_driver(self):
        _, _, drivers = _gen(velocity_1h=0.1, commercial_intent=0.1, sentiment=0.2)
        assert "velocity_1h" in drivers

    def test_negative_sentiment_detected(self):
        _, _, drivers = _gen(sentiment=0.20, velocity_1h=2.0, commercial_intent=0.5)
        assert "sentiment" in drivers

    def test_concentrated_authors_detected(self):
        _, _, drivers = _gen(signal_count=100, unique_authors=5)
        assert "author_diversity" in drivers

    def test_organic_spread_detected(self):
        _, _, drivers = _gen(signal_count=100, unique_authors=80, velocity_1h=2.0)
        assert "author_diversity" in drivers
