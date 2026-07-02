"""Pass 10 — schema-drift EWMA recovery + QuarantineReason.

A quarantined platform must auto-recover after 3 consecutive clean batches
drag its smoothed drop-rate below 0.2 (the § PASS2-2F recovery invariant).
A dirty batch resets the consecutive-clean counter.
"""

from __future__ import annotations

from aegis.scrape.schema_drift import (
    QuarantineReason,
    SchemaDriftTracker,
)


def test_clean_batches_lift_quarantine() -> None:
    """Invariant: 3 clean batches after quarantine → auto-recovery."""
    t = SchemaDriftTracker()
    t.quarantine("reddit", QuarantineReason.SCHEMA_DRIFT, "title field gone")
    assert t.is_quarantined("reddit")
    for _ in range(3):
        t.record_clean_batch("reddit", 50)
    assert not t.is_quarantined("reddit")
    assert t.quarantine_reason("reddit") is None


def test_two_clean_batches_insufficient() -> None:
    """Edge case: only 2 clean batches must NOT lift quarantine."""
    t = SchemaDriftTracker()
    t.quarantine("hn", QuarantineReason.ERROR_RATE)
    t.record_clean_batch("hn", 30)
    t.record_clean_batch("hn", 30)
    assert t.is_quarantined("hn")


def test_dirty_batch_resets_clean_counter() -> None:
    """A high-drop batch resets clean_batches so recovery restarts."""
    t = SchemaDriftTracker()
    t.quarantine("amazon", QuarantineReason.SCHEMA_DRIFT)
    t.record_clean_batch("amazon", 40)
    t.record_clean_batch("amazon", 40)
    t.record_batch("amazon", total=10, valid=2)  # 80% drop — dirty
    t.record_clean_batch("amazon", 40)
    assert t.is_quarantined("amazon")


def test_record_batch_returns_smoothed_drop_rate() -> None:
    """Happy path: EWMA drop-rate is returned and bounded [0, 1]."""
    t = SchemaDriftTracker()
    rate = t.record_batch("github", total=100, valid=50)
    assert 0.0 <= rate <= 1.0
    assert rate == 0.5  # first batch seeds EWMA directly


def test_empty_batch_ignored() -> None:
    """Failure path: total=0 carries no schema info, state untouched."""
    t = SchemaDriftTracker()
    rate = t.record_batch("medium", total=0, valid=0)
    assert rate == 0.0
    assert not t.is_quarantined("medium")


def test_quarantine_reason_typed() -> None:
    """QuarantineReason is a typed enum so the dashboard can distinguish causes."""
    t = SchemaDriftTracker()
    t.quarantine("flipkart", QuarantineReason.CREDENTIALS_MISSING, "no key")
    assert t.quarantine_reason("flipkart") is QuarantineReason.CREDENTIALS_MISSING
    assert t.quarantine_reason("unknown_platform") is None
