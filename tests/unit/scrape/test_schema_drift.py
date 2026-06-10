"""ADP-6: self-healing schema-drift tracker + swarm quarantine tests."""
from __future__ import annotations

import pytest

from aegis.scrape.schema_drift import (
    SchemaDriftTracker,
    get_drift_tracker,
    reset_drift_tracker,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_drift_tracker()
    yield
    reset_drift_tracker()


def test_clean_batches_do_not_drift():
    t = SchemaDriftTracker()
    for _ in range(5):
        t.record_batch("reddit", total=20, valid=20)
    assert t.is_drifting("reddit") is False
    assert t.drop_rate("reddit") == 0.0


def test_total_zero_is_ignored():
    t = SchemaDriftTracker()
    rate = t.record_batch("hn", total=0, valid=0)
    assert rate == 0.0
    assert t.is_drifting("hn") is False


def test_sustained_high_drop_rate_flags_drift():
    t = SchemaDriftTracker()
    # Every batch drops 90% of signals → smoothed rate climbs past 0.5.
    for _ in range(4):
        t.record_batch("flipkart", total=20, valid=2)
    assert t.is_drifting("flipkart") is True
    assert t.drop_rate("flipkart") > 0.5


def test_min_observed_guard_prevents_early_quarantine():
    t = SchemaDriftTracker(min_observed=100)
    # 100% drop but only 10 observed (< 100) → not yet drifting.
    t.record_batch("meesho", total=10, valid=0)
    assert t.is_drifting("meesho") is False
    # Cross the observation floor → now it flags.
    t.record_batch("meesho", total=100, valid=0)
    assert t.is_drifting("meesho") is True


def test_recovery_decays_drop_rate():
    t = SchemaDriftTracker(alpha=0.5)
    for _ in range(3):
        t.record_batch("ajio", total=20, valid=0)  # all bad
    assert t.is_drifting("ajio") is True
    for _ in range(6):
        t.record_batch("ajio", total=20, valid=20)  # fully recovered
    assert t.is_drifting("ajio") is False


def test_reset_clears_state():
    t = SchemaDriftTracker()
    t.record_batch("x", total=20, valid=0)
    t.reset("x")
    assert t.drop_rate("x") == 0.0
    t.record_batch("x", total=20, valid=0)
    t.reset()  # all
    assert t.drop_rate("x") == 0.0


def test_validate_batch_feeds_singleton():
    from aegis.scrape.schema_guard import validate_batch

    # 4 valid + 16 missing-required → 80% drop; repeat to cross observed floor.
    good = {"title": "t", "url": "https://x/a", "platform": "p", "scraped_at": "2025-01-01"}
    bad = {"title": None, "url": None, "platform": "p", "scraped_at": None}
    for _ in range(3):
        batch = [good] * 4 + [bad] * 16
        validate_batch(batch, "driftsource")
    assert get_drift_tracker().is_drifting("driftsource") is True


async def test_swarm_quarantines_drifting_adapter():
    """A 'successful' run on a drifting platform is downgraded + cooled down."""
    from typing import Any

    from aegis.scrape.governor import ConcurrencyGovernor
    from aegis.scrape.result import AdapterCapabilities, AdapterStatus
    from aegis.scrape.swarm_agents import ScraperAgent, SwarmAgentPool

    async def fn(_s: Any, _h: Any, _l: int) -> list[dict[str, Any]]:
        return [{"title": "x", "url": "https://x/a", "platform": "drifty", "scraped_at": "2025"}]

    agent = ScraperAgent(
        name="drifty", platform="drifty", adapter_fn=fn,
        capabilities=AdapterCapabilities(platform="drifty", tier="T3_search"),
    )
    governor = ConcurrencyGovernor(max_concurrent=4, max_flaresolverr=2, jitter_max_ms=0)
    pool = SwarmAgentPool([agent], governor)

    settings = type("S", (), {})()

    # Force the platform into a drifting state.
    tracker = get_drift_tracker()
    tracker.record_batch("drifty", total=40, valid=0)
    assert tracker.is_drifting("drifty")

    run = await pool.run_agent(agent, settings, http=None, limit=10)
    assert run.status == AdapterStatus.SCHEMA_DRIFT
    # One-strike threshold → adapter is now cooling (quarantined).
    assert agent.is_cooling is True
