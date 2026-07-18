"""
tests/unit/scrape/test_dedup.py — Unit tests for aegis.scrape.dedup.

Tests cover:
  - Token-based deduplication (exact + near-duplicate)
  - Sequence-level deduplication with threshold
  - Content hash consistency (must match aegis.scrape.dedup, not local reimpl)
  - Edge cases: empty batch, single item, all-duplicate batch
  - Property tests: dedup output is always a subset of input

Architecture: Phase 0 (Scrape) → dedup.py
"""

from __future__ import annotations

import hashlib
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from aegis.testing import assert_dict_subset

# ---------------------------------------------------------------------------
# Helpers / imports (graceful skip if scrape not installed)
# ---------------------------------------------------------------------------

def _import_dedup() -> Any:
    """Import dedup module; skip test if not available."""
    try:
        from aegis.scrape import dedup  # type: ignore[import-untyped]
        return dedup
    except ImportError:
        pytest.skip("aegis.scrape.dedup not available")


# ---------------------------------------------------------------------------
# Content hash consistency
# ---------------------------------------------------------------------------

class TestContentHash:
    """The content hash must be stable and consistent with the canonical impl."""

    def test_hash_is_sha256_hex(self) -> None:
        dedup = _import_dedup()
        signal = {"url": "https://news.ycombinator.com/item?id=12345", "title": "Test"}
        result = dedup.compute_content_hash(signal)
        # Must be a 64-char hex string (SHA-256)
        assert isinstance(result, str)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_is_deterministic(self) -> None:
        dedup = _import_dedup()
        signal = {"url": "https://example.com/post/1", "title": "My Post"}
        h1 = dedup.compute_content_hash(signal)
        h2 = dedup.compute_content_hash(signal)
        assert h1 == h2

    def test_different_urls_produce_different_hashes(self) -> None:
        dedup = _import_dedup()
        s1 = {"url": "https://a.com/1", "title": "Foo"}
        s2 = {"url": "https://a.com/2", "title": "Foo"}
        assert dedup.compute_content_hash(s1) != dedup.compute_content_hash(s2)

    def test_hash_ignores_scraped_at(self) -> None:
        """scraped_at must NOT be part of the hash — it changes every run."""
        dedup = _import_dedup()
        s1 = {"url": "https://x.com/1", "title": "T", "scraped_at": "2026-01-01T00:00:00Z"}
        s2 = {"url": "https://x.com/1", "title": "T", "scraped_at": "2026-06-15T12:30:00Z"}
        assert dedup.compute_content_hash(s1) == dedup.compute_content_hash(s2)


# ---------------------------------------------------------------------------
# Token-based dedup
# ---------------------------------------------------------------------------

class TestTokenDedup:
    """Two signals with the same content_hash must be deduplicated."""

    def test_exact_duplicate_removed(self) -> None:
        dedup = _import_dedup()
        base = {
            "url": "https://hn.com/item?id=999",
            "title": "AI chip shortage worsens",
            "content_hash": "abc123" * 10 + "abcd",  # 64-char stub
        }
        signals = [dict(base), dict(base, id="different-id")]
        result = dedup.dedup_signals(signals)
        assert len(result) == 1

    def test_unique_signals_preserved(self) -> None:
        dedup = _import_dedup()
        signals = [
            {"url": f"https://x.com/{i}", "title": f"Title {i}", "content_hash": f"{i:064d}"}
            for i in range(10)
        ]
        result = dedup.dedup_signals(signals)
        assert len(result) == 10

    def test_empty_input_returns_empty(self) -> None:
        dedup = _import_dedup()
        assert dedup.dedup_signals([]) == []

    def test_single_item_returned_unchanged(self) -> None:
        dedup = _import_dedup()
        signal = {"url": "https://x.com/1", "title": "Solo", "content_hash": "a" * 64}
        result = dedup.dedup_signals([signal])
        assert len(result) == 1
        assert_dict_subset(result[0], {"url": "https://x.com/1"})

    def test_output_is_subset_of_input(
        self,
        raw_signal_batch: list[dict[str, Any]],
    ) -> None:
        dedup = _import_dedup()
        # Add duplicate
        raw_signal_batch.append(dict(raw_signal_batch[0]))
        result = dedup.dedup_signals(raw_signal_batch)
        input_hashes = {
        s.get("content_hash", s.get("url"))
        for s in raw_signal_batch
    }
        output_hashes = {s.get("content_hash", s.get("url")) for s in result}
        assert output_hashes.issubset(input_hashes)


# ---------------------------------------------------------------------------
# Sequence-level near-dedup
# ---------------------------------------------------------------------------

class TestSequenceDedup:
    """Near-duplicate detection via sequence similarity."""

    def test_high_similarity_signals_merged(self) -> None:
        dedup = _import_dedup()
        s1 = {
            "url": "https://a.com/1",
            "title": "AI startup raises $50M Series B",
            "content_hash": "a" * 64,
        }
        s2 = {
            "url": "https://b.com/1",
            "title": "AI startup raises $50M in Series B round",
            "content_hash": "b" * 64,
        }
        result = dedup.dedup_signals([s1, s2], threshold=0.85)
        # Near-duplicates should collapse to one
        assert len(result) == 1

    def test_low_similarity_signals_kept(self) -> None:
        dedup = _import_dedup()
        s1 = {"url": "https://a.com/1", "title": "AI chip breakthrough", "content_hash": "a" * 64}
        s2 = {
            "url": "https://b.com/1",
            "title": "Fashion trend: Y2K revival",
            "content_hash": "b" * 64,
        }
        result = dedup.dedup_signals([s1, s2], threshold=0.85)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

@given(
    titles=st.lists(
        st.text(min_size=5, max_size=100),
        min_size=1,
        max_size=30,
    )
)
@settings(max_examples=30)
def test_dedup_output_never_larger_than_input(titles: list[str]) -> None:
    """Property: dedup output length ≤ input length."""
    dedup = _import_dedup()
    signals = [
        {
            "url": f"https://x.com/{i}",
            "title": t,
            "content_hash": hashlib.sha256(t.encode()).hexdigest(),
        }
        for i, t in enumerate(titles)
    ]
    result = dedup.dedup_signals(signals)
    assert len(result) <= len(signals)


@given(
    n=st.integers(min_value=1, max_value=50)
)
@settings(max_examples=20)
def test_dedup_idempotent(n: int) -> None:
    """Property: running dedup twice gives the same result as running it once."""
    dedup = _import_dedup()
    signals = [
        {"url": f"https://x.com/{i}", "title": f"Title {i}", "content_hash": f"{i:064d}"}
        for i in range(n)
    ]
    once = dedup.dedup_signals(signals)
    twice = dedup.dedup_signals(once)
    assert len(once) == len(twice)
