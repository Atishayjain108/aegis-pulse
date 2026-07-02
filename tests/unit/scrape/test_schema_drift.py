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


# ---------------------------------------------------------------------------
# PASS2-2F: QuarantineReason + clean-batch auto-recovery
# ---------------------------------------------------------------------------


def test_quarantine_reason_stored_and_retrievable():
    from aegis.scrape.schema_drift import QuarantineReason

    t = SchemaDriftTracker()
    t.quarantine("meesho", QuarantineReason.SCHEMA_DRIFT, "shape changed")
    assert t.is_quarantined("meesho") is True
    assert t.quarantine_reason("meesho") is QuarantineReason.SCHEMA_DRIFT
    # Unknown platform → no reason
    assert t.quarantine_reason("hn") is None
    assert t.is_quarantined("hn") is False


def test_credentials_missing_reason():
    from aegis.scrape.schema_drift import QuarantineReason

    t = SchemaDriftTracker()
    t.quarantine("reddit", QuarantineReason.CREDENTIALS_MISSING, "no client_id")
    assert t.quarantine_reason("reddit") is QuarantineReason.CREDENTIALS_MISSING


def test_clean_batches_incremented_on_each_clean_call():
    t = SchemaDriftTracker()
    t.record_clean_batch("hn", 20)
    t.record_clean_batch("hn", 20)
    assert t._state["hn"].clean_batches == 2


def test_should_unquarantine_false_with_two_clean_batches():
    from aegis.scrape.schema_drift import QuarantineReason

    t = SchemaDriftTracker()
    t.quarantine("ajio", QuarantineReason.SCHEMA_DRIFT)
    t.record_clean_batch("ajio", 20)
    t.record_clean_batch("ajio", 20)
    assert t.should_unquarantine("ajio") is False
    assert t.is_quarantined("ajio") is True


def test_should_unquarantine_true_after_three_clean_batches():
    from aegis.scrape.schema_drift import QuarantineReason

    t = SchemaDriftTracker()
    t.quarantine("ajio", QuarantineReason.SCHEMA_DRIFT)
    for _ in range(3):
        t.record_clean_batch("ajio", 20)
    # Third clean batch auto-lifts the quarantine.
    assert t.is_quarantined("ajio") is False
    assert t.quarantine_reason("ajio") is None


def test_quarantine_lifted_log_event_emitted():
    from structlog.testing import capture_logs

    from aegis.scrape.schema_drift import QuarantineReason

    t = SchemaDriftTracker()
    t.quarantine("nykaa", QuarantineReason.ERROR_RATE)
    with capture_logs() as logs:
        for _ in range(3):
            t.record_clean_batch("nykaa", 20)
    assert any(e["event"] == "schema_drift.quarantine_lifted" for e in logs)


def test_ewma_decays_toward_clean_on_consecutive_clean_batches():
    t = SchemaDriftTracker()
    # Start drifting hard.
    for _ in range(4):
        t.record_batch("flipkart", total=20, valid=2)
    dirty_rate = t.drop_rate("flipkart")
    assert dirty_rate > 0.5
    # Clean batches decay the EWMA monotonically toward 0.
    rates = []
    for _ in range(6):
        t.record_clean_batch("flipkart", 20)
        rates.append(t.drop_rate("flipkart"))
    assert all(rates[i] > rates[i + 1] for i in range(len(rates) - 1))
    assert rates[-1] < dirty_rate


def test_record_batch_clean_path_also_recovers():
    """validate_batch() feeds record_batch — clean batches there must recover too."""
    from aegis.scrape.schema_drift import QuarantineReason

    t = SchemaDriftTracker()
    t.quarantine("snapdeal", QuarantineReason.SCHEMA_DRIFT)
    for _ in range(3):
        t.record_batch("snapdeal", total=20, valid=20)
    assert t.is_quarantined("snapdeal") is False


def test_dirty_batch_resets_clean_counter():
    t = SchemaDriftTracker()
    t.record_clean_batch("mint", 20)
    t.record_clean_batch("mint", 20)
    # 50% drop → dirty → counter resets.
    t.record_batch("mint", total=20, valid=10)
    assert t._state["mint"].clean_batches == 0
