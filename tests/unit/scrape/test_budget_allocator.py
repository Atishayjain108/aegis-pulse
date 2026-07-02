"""ADP-4 / BRAIN-2: UCB1 adaptive scrape-budget allocator tests."""
from __future__ import annotations

import math

from aegis.scrape.budget import UCB1Allocator


def test_unplayed_arms_score_infinite_and_get_base_limit():
    alloc = UCB1Allocator()
    assert math.isinf(alloc.ucb_score("never"))
    out = alloc.allocate(["a", "b"], base_limit=50)
    assert out == {"a": 50, "b": 50}


def test_record_accumulates_normalised_reward():
    alloc = UCB1Allocator(reward_cap=100.0)
    alloc.record("x", 100)  # reward 1.0
    alloc.record("x", 0)    # reward 0.0
    stats = alloc.stats()["x"]
    assert stats["plays"] == 2
    assert stats["mean_reward"] == 0.5


def test_productive_arm_gets_more_budget_than_quiet_arm():
    alloc = UCB1Allocator()
    # Hot arm consistently yields a lot; cold arm yields nothing.
    for _ in range(10):
        alloc.record("hot", 80)
        alloc.record("cold", 0)
    out = alloc.allocate(["hot", "cold"], base_limit=50, min_limit=5, max_limit=100)
    assert out["hot"] > out["cold"]
    assert out["cold"] >= 5  # never starved below min_limit


def test_allocate_clamps_to_min_and_max():
    alloc = UCB1Allocator()
    for _ in range(20):
        alloc.record("hot", 100)
        alloc.record("cold", 0)
    out = alloc.allocate(["hot", "cold"], base_limit=50, min_limit=10, max_limit=60)
    assert 10 <= out["hot"] <= 60
    assert 10 <= out["cold"] <= 60


def test_empty_arms_returns_empty():
    assert UCB1Allocator().allocate([], base_limit=50) == {}


def test_exploration_bonus_revisits_quiet_arm():
    """A quiet arm played few times keeps a non-trivial UCB score via the bonus."""
    alloc = UCB1Allocator(c=1.4)
    for _ in range(50):
        alloc.record("dominant", 50)
    alloc.record("rare", 10)  # played once, modest reward
    # The rarely-played arm's exploration bonus keeps it competitive.
    assert alloc.ucb_score("rare") > alloc.ucb_score("dominant") * 0.5


# ---------------------------------------------------------------------------
# PASS2-2B: composite quality reward
# ---------------------------------------------------------------------------


def test_quality_reward_prefers_novel_high_confidence_source():
    """20 novel high-confidence signals beat 200 duplicate low-confidence ones."""
    alloc = UCB1Allocator()
    for _ in range(10):
        alloc.record_with_quality(
            source="quality",
            yield_count=20,
            elapsed_s=10.0,
            novelty_fraction=1.0,
            avg_confidence=0.9,
        )
        alloc.record_with_quality(
            source="spam",
            yield_count=200,
            elapsed_s=10.0,
            novelty_fraction=0.05,
            avg_confidence=0.2,
        )
    stats = alloc.stats()
    assert stats["quality"]["mean_reward"] > stats["spam"]["mean_reward"]


def test_quality_reward_composite_formula():
    alloc = UCB1Allocator()
    # normalized_yield = min(1, 50 / (1.0 * 100)) = 0.5
    alloc.record_with_quality(
        source="s",
        yield_count=50,
        elapsed_s=1.0,
        novelty_fraction=0.5,
        avg_confidence=0.5,
    )
    expected = 0.5 * 0.5 + 0.3 * 0.5 + 0.2 * 0.5
    assert abs(alloc.stats()["s"]["mean_reward"] - expected) < 1e-6


def test_quality_reward_zero_elapsed_does_not_crash():
    alloc = UCB1Allocator()
    alloc.record_with_quality(
        source="s", yield_count=10, elapsed_s=0.0, novelty_fraction=1.0, avg_confidence=1.0
    )
    assert alloc.stats()["s"]["plays"] == 1


def test_quality_reward_inputs_clamped_to_unit_interval():
    alloc = UCB1Allocator()
    alloc.record_with_quality(
        source="s",
        yield_count=10_000,
        elapsed_s=0.001,
        novelty_fraction=5.0,
        avg_confidence=-3.0,
    )
    # 0.5*1.0 + 0.3*1.0 + 0.2*0.0 = 0.8 max
    assert 0.0 <= alloc.stats()["s"]["mean_reward"] <= 0.8 + 1e-9


def test_quality_reward_counts_as_play_for_allocation():
    alloc = UCB1Allocator()
    alloc.record_with_quality(
        source="seen", yield_count=10, elapsed_s=1.0, novelty_fraction=1.0, avg_confidence=1.0
    )
    out = alloc.allocate(["seen", "unseen"], base_limit=50)
    assert out["unseen"] == 50  # unexplored arm gets baseline
    assert "seen" in out
