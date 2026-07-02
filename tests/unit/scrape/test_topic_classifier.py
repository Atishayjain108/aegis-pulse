"""Pass 10 — TopicClassifier accuracy per topic type.

The classifier is a zero-I/O keyword + bigram heuristic. Default class is
CONSUMER_TREND when no keyword matches (the § invariant), so generic
queries never raise — they fall through to the consumer bucket.
"""

from __future__ import annotations

import pytest

from aegis.scrape.topic_classifier import TopicClassifier, TopicType


@pytest.fixture
def clf() -> TopicClassifier:
    return TopicClassifier()


@pytest.mark.parametrize(
    "query",
    ["NSE Nifty close", "HDFC stock price", "SEBI IPO listing", "RBI dividend payout"],
)
def test_financial_queries(clf: TopicClassifier, query: str) -> None:
    assert clf.classify(query) is TopicType.FINANCIAL_TREND


@pytest.mark.parametrize(
    "query",
    ["new AI chips from OpenAI", "LLM GPU benchmark", "github software API"],
)
def test_tech_queries(clf: TopicClassifier, query: str) -> None:
    assert clf.classify(query) is TopicType.TECH_NEWS


def test_consumer_default_for_unknown(clf: TopicClassifier) -> None:
    """Invariant: no keyword match → CONSUMER_TREND default, never an error."""
    assert clf.classify("xyzzy frobnicate") is TopicType.CONSUMER_TREND
    assert clf.classify("qwerty asdfgh zxcvb") is TopicType.CONSUMER_TREND


def test_empty_query_defaults_to_consumer(clf: TopicClassifier) -> None:
    """Edge case: empty string returns the default, no exception."""
    assert clf.classify("") is TopicType.CONSUMER_TREND


def test_bigram_outweighs_single_token(clf: TopicClassifier) -> None:
    """'mutual fund' is a financial bigram (weight 2) — must win."""
    assert clf.classify("mutual fund returns") is TopicType.FINANCIAL_TREND


def test_case_insensitive(clf: TopicClassifier) -> None:
    assert clf.classify("nse SENSEX") is TopicType.FINANCIAL_TREND


def test_bank_keyword_is_financial(clf: TopicClassifier) -> None:
    assert clf.classify("banking sector earnings") is TopicType.FINANCIAL_TREND


def test_classify_is_deterministic(clf: TopicClassifier) -> None:
    """Same query → same class on repeated calls (pure heuristic)."""
    q = "AI chips stock"
    assert clf.classify(q) is clf.classify(q)
