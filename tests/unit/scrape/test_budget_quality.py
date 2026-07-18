"""Pass 10 — UCB1 composite-reward (quality, not quantity) calculation.

`record_with_quality` blends normalized yield (0.5), novelty (0.3) and
average confidence (0.2). The § invariant: a source emitting many
duplicate low-confidence signals must score below one emitting fewer
novel, high-confidence ones.
"""

from __future__ import annotations

from aegis.scrape.budget import UCB1Allocator


def test_quality_beats_quantity() -> None:
    """Invariant: 20 novel/high-confidence > 200 duplicate/low-confidence."""
    alloc = UCB1Allocator()
    alloc.record_with_quality(
        "quality_src", yield_count=20, elapsed_s=5.0,
        novelty_fraction=0.95, avg_confidence=0.9,
    )
    alloc.record_with_quality(
        "spam_src", yield_count=200, elapsed_s=5.0,
        novelty_fraction=0.02, avg_confidence=0.1,
    )
    stats = alloc.stats()
    assert stats["quality_src"]["mean_reward"] > stats["spam_src"]["mean_reward"]


def test_reward_bounded_zero_to_one() -> None:
    """Edge case: extreme inputs stay in [0, 1] composite range."""
    alloc = UCB1Allocator()
    alloc.record_with_quality(
        "src", yield_count=10_000, elapsed_s=1.0,
        novelty_fraction=5.0, avg_confidence=5.0,
    )
    assert 0.0 <= alloc.stats()["src"]["mean_reward"] <= 1.0


def test_zero_yield_low_reward() -> None:
    """A source producing nothing earns a near-zero reward."""
    alloc = UCB1Allocator()
    alloc.record_with_quality(
        "empty", yield_count=0, elapsed_s=3.0,
        novelty_fraction=0.0, avg_confidence=0.0,
    )
    assert alloc.stats()["empty"]["mean_reward"] == 0.0


def test_zero_elapsed_does_not_crash() -> None:
    """Failure path: elapsed_s <= 0 is clamped, no division error."""
    alloc = UCB1Allocator()
    alloc.record_with_quality(
        "src", yield_count=5, elapsed_s=0.0,
        novelty_fraction=0.5, avg_confidence=0.5,
    )
    assert 0.0 <= alloc.stats()["src"]["mean_reward"] <= 1.0


def test_novelty_increases_reward() -> None:
    """Holding yield/confidence fixed, higher novelty → higher reward."""
    low = UCB1Allocator()
    low.record_with_quality(
        "a", yield_count=10, elapsed_s=2.0, novelty_fraction=0.1, avg_confidence=0.5,
    )
    high = UCB1Allocator()
    high.record_with_quality(
        "a", yield_count=10, elapsed_s=2.0, novelty_fraction=0.9, avg_confidence=0.5,
    )
    assert high.stats()["a"]["mean_reward"] > low.stats()["a"]["mean_reward"]
