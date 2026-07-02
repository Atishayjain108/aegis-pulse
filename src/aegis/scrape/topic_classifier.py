"""Zero-cost topic classifier for AEGIS adapter routing.

Phase 0 scrape layer. Classifies user queries into a :class:`TopicType` enum
using keyword heuristics (zero latency, zero network calls). The resulting
topic type indexes :data:`ADAPTER_AFFINITY`, which tells the
``AdapterRouter`` which adapters carry the strongest expected signal for a
given query. India-first coverage (NSE, Flipkart, IndiaMART) with global
sources as secondary.
"""

from __future__ import annotations

import re
from enum import Enum

import structlog

_log = structlog.get_logger("aegis.scrape.topic_classifier")


class TopicType(str, Enum):
    """High-level intent categories a scrape query can fall into."""

    ECOMMERCE_PRODUCT = "ecommerce_product"  # "nike shoes", "earbuds"
    FINANCIAL_TREND = "financial_trend"  # "HDFC", "NSE", "Nifty"
    TECH_NEWS = "tech_news"  # "AI chips", "GPT"
    STARTUP_SIGNAL = "startup_signal"  # "YC batch", "product launch"
    CONSUMER_TREND = "consumer_trend"  # "viral", "trending"
    REGULATORY = "regulatory"  # "FDA recall", "FTC"
    COMPETITOR_INTEL = "competitor_intel"  # "vs competitor", "compare"
    SUPPLIER_DISCOVERY = "supplier_discovery"  # "wholesale", "manufacturer"
    GLOBAL_ARBITRAGE = "global_arbitrage"  # multi-region price opportunity
    MARKET_RESEARCH = "market_research"  # general research


# ── Keyword → TopicType mapping (heuristic fast path) ─────────────────────
# Single words and bigrams; bigrams score double in TopicClassifier.classify.
_KW: dict[str, TopicType] = {
    # Financial
    "nse": TopicType.FINANCIAL_TREND,
    "bse": TopicType.FINANCIAL_TREND,
    "nifty": TopicType.FINANCIAL_TREND,
    "sensex": TopicType.FINANCIAL_TREND,
    "stock": TopicType.FINANCIAL_TREND,
    "stocks": TopicType.FINANCIAL_TREND,
    "equity": TopicType.FINANCIAL_TREND,
    "ipo": TopicType.FINANCIAL_TREND,
    "mutual fund": TopicType.FINANCIAL_TREND,
    "sebi": TopicType.FINANCIAL_TREND,
    "rbi": TopicType.FINANCIAL_TREND,
    "demat": TopicType.FINANCIAL_TREND,
    "screener": TopicType.FINANCIAL_TREND,
    "dividend": TopicType.FINANCIAL_TREND,
    "earnings": TopicType.FINANCIAL_TREND,
    "bank": TopicType.FINANCIAL_TREND,
    "banking": TopicType.FINANCIAL_TREND,
    "finance": TopicType.FINANCIAL_TREND,
    "shares": TopicType.FINANCIAL_TREND,
    # E-commerce
    "product": TopicType.ECOMMERCE_PRODUCT,
    "price": TopicType.ECOMMERCE_PRODUCT,
    "buy": TopicType.ECOMMERCE_PRODUCT,
    "sell": TopicType.ECOMMERCE_PRODUCT,
    "amazon": TopicType.ECOMMERCE_PRODUCT,
    "flipkart": TopicType.ECOMMERCE_PRODUCT,
    "meesho": TopicType.ECOMMERCE_PRODUCT,
    "myntra": TopicType.ECOMMERCE_PRODUCT,
    "dropship": TopicType.ECOMMERCE_PRODUCT,
    "ecommerce": TopicType.ECOMMERCE_PRODUCT,
    "earbuds": TopicType.ECOMMERCE_PRODUCT,
    "sneakers": TopicType.ECOMMERCE_PRODUCT,
    # Supplier discovery
    "wholesale": TopicType.SUPPLIER_DISCOVERY,
    "manufacturer": TopicType.SUPPLIER_DISCOVERY,
    "b2b": TopicType.SUPPLIER_DISCOVERY,
    "supplier": TopicType.SUPPLIER_DISCOVERY,
    "indiamart": TopicType.SUPPLIER_DISCOVERY,
    "factory": TopicType.SUPPLIER_DISCOVERY,
    "oem": TopicType.SUPPLIER_DISCOVERY,
    "bulk": TopicType.SUPPLIER_DISCOVERY,
    # Tech
    "ai": TopicType.TECH_NEWS,
    "llm": TopicType.TECH_NEWS,
    "gpu": TopicType.TECH_NEWS,
    "openai": TopicType.TECH_NEWS,
    "github": TopicType.TECH_NEWS,
    "api": TopicType.TECH_NEWS,
    "software": TopicType.TECH_NEWS,
    "chip": TopicType.TECH_NEWS,
    "chips": TopicType.TECH_NEWS,
    # Startup
    "startup": TopicType.STARTUP_SIGNAL,
    "launch": TopicType.STARTUP_SIGNAL,
    "yc": TopicType.STARTUP_SIGNAL,
    "funding": TopicType.STARTUP_SIGNAL,
    # Regulatory
    "recall": TopicType.REGULATORY,
    "ban": TopicType.REGULATORY,
    "fda": TopicType.REGULATORY,
    "ftc": TopicType.REGULATORY,
    "compliance": TopicType.REGULATORY,
    "lawsuit": TopicType.REGULATORY,
    "regulation": TopicType.REGULATORY,
    # Competitor intel
    "competitor": TopicType.COMPETITOR_INTEL,
    "compare": TopicType.COMPETITOR_INTEL,
    "vs": TopicType.COMPETITOR_INTEL,
    "alternative": TopicType.COMPETITOR_INTEL,
    # Global arbitrage
    "arbitrage": TopicType.GLOBAL_ARBITRAGE,
    "import": TopicType.GLOBAL_ARBITRAGE,
    "export": TopicType.GLOBAL_ARBITRAGE,
    "tariff": TopicType.GLOBAL_ARBITRAGE,
    "cross border": TopicType.GLOBAL_ARBITRAGE,
}

# ── Adapter affinity matrix ────────────────────────────────────────────────
# Higher weight = stronger expected signal for this topic type. Adapter names
# match the swarm registry (``aegis.scrape.swarm._REGISTRY``) so every routed
# name is directly runnable. Weights are multiplied by the live health score
# for final ranking.
ADAPTER_AFFINITY: dict[TopicType, dict[str, float]] = {
    TopicType.ECOMMERCE_PRODUCT: {
        "amazon": 0.95, "amazon_in": 0.95, "flipkart": 0.92, "meesho": 0.88,
        "myntra": 0.82, "nykaa": 0.78, "snapdeal": 0.72, "ajio": 0.70,
        "google_trends_india": 0.80, "reddit_ecommerce": 0.85,
        "producthunt": 0.60,
    },
    TopicType.FINANCIAL_TREND: {
        "nse_bse": 0.98, "moneycontrol": 0.96, "economic_times": 0.92,
        "screener_in": 0.88, "yahoo_finance": 0.85, "investing_com": 0.82,
        "ndtv_profit": 0.80, "mint": 0.78, "business_standard": 0.75,
        "reddit_finance": 0.70,
    },
    TopicType.TECH_NEWS: {
        "hacker-news": 0.95, "github-trending": 0.90, "techcrunch": 0.88,
        "wired": 0.75, "devto": 0.82, "npm_trends": 0.72,
        "producthunt": 0.85, "reddit_ecommerce": 0.50,
    },
    TopicType.STARTUP_SIGNAL: {
        "hacker-news": 0.92, "producthunt": 0.95, "github-trending": 0.80,
        "techcrunch": 0.85, "devto": 0.75, "reddit_ecommerce": 0.60,
    },
    TopicType.SUPPLIER_DISCOVERY: {
        "indiamart": 0.96, "amazon_in": 0.75, "meesho": 0.70,
        "google-news": 0.65, "reddit_ecommerce": 0.60,
    },
    TopicType.REGULATORY: {
        "google-news": 0.90, "bing-news": 0.88, "reuters": 0.85,
        "techcrunch": 0.70, "bbc_news": 0.75, "economic_times": 0.72,
    },
    TopicType.COMPETITOR_INTEL: {
        "google-news": 0.92, "bing-news": 0.88, "techcrunch": 0.82,
        "reuters": 0.78, "hacker-news": 0.65, "reddit_ecommerce": 0.62,
    },
    TopicType.GLOBAL_ARBITRAGE: {
        "amazon": 0.85, "amazon_in": 0.90, "flipkart": 0.88,
        "yahoo_finance": 0.72, "investing_com": 0.70,
        "google_trends_india": 0.85, "indiamart": 0.75,
    },
    TopicType.CONSUMER_TREND: {
        "reddit_ecommerce": 0.88, "producthunt": 0.80,
        "google_trends_india": 0.92, "amazon": 0.80, "amazon_in": 0.82,
        "youtube_rss": 0.70, "hacker-news": 0.60,
    },
    TopicType.MARKET_RESEARCH: {  # fallback: broad coverage
        "google-news": 0.80, "bing-news": 0.78, "hacker-news": 0.75,
        "reddit_ecommerce": 0.75, "reddit_finance": 0.72,
        "techcrunch": 0.70, "reuters": 0.70,
    },
}


class TopicClassifier:
    """Classify queries into :class:`TopicType` using keyword heuristics.

    Zero latency, zero cost, zero network calls. Covers India-first use
    cases with INR/Indian platform awareness.
    """

    def classify(self, query: str) -> TopicType:
        """Classify topic. Returns highest-scoring type, defaulting to CONSUMER_TREND."""
        words = re.findall(r"\b\w+\b", query.lower())
        scores: dict[TopicType, float] = {}

        for word in words:
            if word in _KW:
                tt = _KW[word]
                scores[tt] = scores.get(tt, 0.0) + 1.0

        # Bigram check for compound terms — bigrams score higher.
        for i in range(len(words) - 1):
            bigram = f"{words[i]} {words[i + 1]}"
            if bigram in _KW:
                tt = _KW[bigram]
                scores[tt] = scores.get(tt, 0.0) + 2.0

        if not scores:
            return TopicType.CONSUMER_TREND

        best = max(scores, key=lambda k: scores[k])
        _log.debug(
            "topic_classifier.result",
            query=query,
            topic_type=best.value,
            scores={k.value: v for k, v in scores.items()},
        )
        return best


__all__ = ["ADAPTER_AFFINITY", "TopicClassifier", "TopicType"]
