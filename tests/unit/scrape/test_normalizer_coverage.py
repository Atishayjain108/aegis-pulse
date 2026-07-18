"""Cover aegis.scrape.normalizer signal-normalization pipeline."""

from __future__ import annotations

from aegis.scrape import normalizer as nz


def test_z_score_batch_normalizes() -> None:
    sigs = [{"score": 10}, {"score": 20}, {"score": 30}]
    out = nz.z_score_batch(sigs)
    assert all("score_normalized" in s for s in out)
    # middle value is the mean → ~0
    assert abs(out[1]["score_normalized"]) < 1e-6


def test_z_score_batch_too_few_returns_input() -> None:
    sigs = [{"score": 10}]
    assert nz.z_score_batch(sigs) is sigs


def test_z_score_batch_zero_variance() -> None:
    sigs = [{"score": 5}, {"score": 5}, {"score": 5}]
    out = nz.z_score_batch(sigs)
    # stdev 0 → returned unchanged, no normalized field
    assert all("score_normalized" not in s for s in out)


def test_percentile_rank_batch() -> None:
    sigs = [{"score": 1}, {"score": 2}, {"score": 3}]
    out = nz.percentile_rank_batch(sigs)
    assert out[0]["percentile_rank"] == 0.0
    assert out[-1]["percentile_rank"] == 100.0


def test_apply_tier_weight_known_and_unknown() -> None:
    sigs = [{"score": 10, "tier": "T1_intent"}, {"score": 10, "tier": "mystery"}]
    out = nz.apply_tier_weight(sigs)
    assert out[0]["weighted_score"] == 10.0          # weight 1.0
    assert out[1]["weighted_score"] == 7.0           # default 0.7


def test_normalize_swarm_batch_full_pipeline() -> None:
    sigs = [
        {"platform": "reddit", "score": 10, "tier": "T4_cultural"},
        {"platform": "reddit", "score": 20, "tier": "T4_cultural"},
        {"platform": "github", "score": 100, "tier": "T3_search"},
        {"platform": "github", "score": 200, "tier": "T3_search"},
    ]
    out = nz.normalize_swarm_batch(sigs)
    assert len(out) == 4
    assert all("weighted_score" in s for s in out)
    assert all("percentile_rank" in s for s in out)
