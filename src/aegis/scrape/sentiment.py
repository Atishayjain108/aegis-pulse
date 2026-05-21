"""
Keyword-based sentiment classifier for market intelligence.

Finance sentiment ≠ social sentiment. We use separate word lists.
Returns a float in [-1.0, +1.0].
"""
from __future__ import annotations

import statistics
from typing import Any

# Finance-specific sentiment lexicons
_BULLISH_FINANCE = frozenset([
    "surge", "rally", "breakout", "outperform", "beat", "record", "all-time high",
    "upgraded", "buy", "strong buy", "overweight", "bullish", "growth", "profit",
    "revenue beat", "expansion", "acquisition", "ipo", "upside", "momentum",
    "gains", "recovery", "upturn", "positive", "opportunity", "invest",
])
_BEARISH_FINANCE = frozenset([
    "crash", "plunge", "decline", "selloff", "downgrade", "miss", "loss",
    "recession", "inflation", "rate hike", "layoffs", "bankruptcy", "fraud",
    "lawsuit", "regulatory", "underperform", "sell", "bearish", "risk",
    "correction", "volatility", "write-down", "impairment", "default",
])

# E-commerce / trend sentiment
_BULLISH_ECOMMERCE = frozenset([
    "trending", "bestseller", "viral", "popular", "hot", "demand", "sold out",
    "top rated", "most reviewed", "winner", "deal", "discount", "launch",
])
_BEARISH_ECOMMERCE = frozenset([
    "discontinued", "recalled", "unavailable", "out of stock", "refund",
    "complaint", "scam", "fake", "counterfeit", "banned",
])

_ALL_BULLISH = _BULLISH_FINANCE | _BULLISH_ECOMMERCE
_ALL_BEARISH = _BEARISH_FINANCE | _BEARISH_ECOMMERCE


def score_text(text: str) -> float:
    """
    Returns sentiment score in [-1.0, 1.0].
    Uses weighted term counting — finance terms carry 1.5x weight.
    """
    if not text:
        return 0.0
    text_lower = text.lower()
    bull = sum(1.5 if t in _BULLISH_FINANCE else 1.0
               for t in _ALL_BULLISH if t in text_lower)
    bear = sum(1.5 if t in _BEARISH_FINANCE else 1.0
               for t in _ALL_BEARISH if t in text_lower)
    total = bull + bear
    if total == 0:
        return 0.0
    return round((bull - bear) / total, 4)


def classify_pulse(signals: list[dict[str, Any]]) -> str:
    """
    Compute market pulse from a batch of signals.
    Returns: "bullish" | "bearish" | "neutral" | "volatile"

    Volatile = high variance in sentiment scores even if mean ≈ 0.
    """
    sentiments = [
        float(s.get("sentiment") or 0)
        for s in signals
        if s.get("sentiment") is not None
    ]
    if not sentiments:
        return "neutral"
    mean = statistics.mean(sentiments)
    try:
        stdev = statistics.stdev(sentiments)
    except statistics.StatisticsError:
        stdev = 0.0

    if stdev > 0.4:
        return "volatile"
    if mean > 0.15:
        return "bullish"
    if mean < -0.15:
        return "bearish"
    return "neutral"


__all__ = ["score_text", "classify_pulse"]
