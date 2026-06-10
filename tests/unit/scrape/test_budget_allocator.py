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
