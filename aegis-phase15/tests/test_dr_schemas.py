"""
tests/test_dr_schemas.py
========================
Unit tests for aegis.dr.schemas — Phase 15.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from aegis.dr.schemas import (
    BackupManifest,
    BackupStatus,
    BackupTarget,
    DrillOutcome,
    DrillResult,
    FailureModeCategory,
    RecoveryPlan,
    RestoreResult,
    RestoreStatus,
    SlaSnapshot,
    SlaStatus,
)

# ---------------------------------------------------------------------------
# BackupManifest
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)
_LATER = _NOW + timedelta(seconds=30)


def _valid_manifest(**kwargs: object) -> BackupManifest:
    defaults = {
        "target": BackupTarget.POSTGRES,
        "status": BackupStatus.SUCCESS,
        "started_at": _NOW,
        "finished_at": _LATER,
        "size_bytes": 1024,
        "checksum_sha256": "abc123",
        "minio_path": "postgres/dt=2026-01-01/foo.dump",
    }
    defaults.update(kwargs)
    return BackupManifest(**defaults)  # type: ignore[arg-type]


def test_backup_manifest_creates_successfully() -> None:
    m = _valid_manifest()
    assert m.target == BackupTarget.POSTGRES
    assert m.status == BackupStatus.SUCCESS
    assert m.duration_s == pytest.approx(30.0, abs=1.0)


def test_backup_manifest_is_frozen() -> None:
    m = _valid_manifest()
    with pytest.raises(Exception):
        m.status = BackupStatus.FAILED  # type: ignore[misc]


def test_backup_manifest_naive_datetime_rejected() -> None:
    with pytest.raises(Exception):
        _valid_manifest(started_at=datetime(2026, 1, 1))  # naive — no tzinfo


def test_backup_manifest_finished_before_started_rejected() -> None:
    with pytest.raises(Exception):
        _valid_manifest(started_at=_LATER, finished_at=_NOW)


def test_backup_manifest_content_hash_deterministic() -> None:
    m1 = _valid_manifest()
    m2 = _valid_manifest()
    assert m1.content_hash() == m2.content_hash()


def test_backup_manifest_content_hash_differs_on_path() -> None:
    m1 = _valid_manifest(minio_path="foo/bar.dump")
    m2 = _valid_manifest(minio_path="foo/baz.dump")
    assert m1.content_hash() != m2.content_hash()


def test_backup_manifest_size_bytes_non_negative() -> None:
    with pytest.raises(Exception):
        _valid_manifest(size_bytes=-1)


# ---------------------------------------------------------------------------
# RestoreResult
# ---------------------------------------------------------------------------


def test_restore_result_creates_successfully() -> None:
    r = RestoreResult(
        target=BackupTarget.POSTGRES,
        status=RestoreStatus.SUCCESS,
        manifest_id=uuid4(),
        started_at=_NOW,
        finished_at=_LATER,
        rows_restored=1000,
        verification_passed=True,
    )
    assert r.verification_passed is True
    assert r.duration_s == pytest.approx(30.0, abs=1.0)


def test_restore_result_frozen() -> None:
    r = RestoreResult(
        target=BackupTarget.POSTGRES,
        status=RestoreStatus.SUCCESS,
        manifest_id=uuid4(),
        started_at=_NOW,
        finished_at=_LATER,
    )
    with pytest.raises(Exception):
        r.status = RestoreStatus.FAILED  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DrillResult
# ---------------------------------------------------------------------------


def _restore_result() -> RestoreResult:
    return RestoreResult(
        target=BackupTarget.POSTGRES,
        status=RestoreStatus.SUCCESS,
        manifest_id=uuid4(),
        started_at=_NOW,
        finished_at=_LATER,
        verification_passed=True,
    )


def test_drill_result_pass() -> None:
    d = DrillResult(
        triggered_at=_NOW,
        completed_at=_LATER,
        outcome=DrillOutcome.PASS,
        rto_actual_s=120.0,
        rpo_actual_s=450.0,
        targets_tested=[BackupTarget.POSTGRES],
        restore_results=[_restore_result()],
        rto_target_s=3600,
        rpo_target_s=900,
        rto_met=True,
        rpo_met=True,
    )
    assert d.passed is True


def test_drill_result_fail_when_rto_not_met() -> None:
    d = DrillResult(
        triggered_at=_NOW,
        completed_at=_LATER,
        outcome=DrillOutcome.PASS,
        rto_actual_s=7200.0,
        rpo_actual_s=450.0,
        targets_tested=[BackupTarget.POSTGRES],
        restore_results=[_restore_result()],
        rto_target_s=3600,
        rpo_target_s=900,
        rto_met=False,
        rpo_met=True,
    )
    assert d.passed is False


def test_drill_result_fail_on_outcome_fail() -> None:
    d = DrillResult(
        triggered_at=_NOW,
        completed_at=_LATER,
        outcome=DrillOutcome.FAIL,
        rto_actual_s=120.0,
        rpo_actual_s=450.0,
        targets_tested=[BackupTarget.POSTGRES],
        restore_results=[_restore_result()],
        rto_target_s=3600,
        rpo_target_s=900,
        rto_met=True,
        rpo_met=True,
    )
    assert d.passed is False


# ---------------------------------------------------------------------------
# SlaSnapshot
# ---------------------------------------------------------------------------


def test_sla_snapshot_ok_state() -> None:
    snap = SlaSnapshot(
        captured_at=_NOW,
        overall_status=SlaStatus.OK,
        rpo_status=SlaStatus.OK,
        rto_status=SlaStatus.OK,
        last_backup_ages_s={"postgres": 300.0, "redis": 120.0},
        last_drill_outcome=DrillOutcome.PASS,
        last_drill_at=_NOW - timedelta(days=2),
        next_drill_due_at=_NOW + timedelta(days=5),
    )
    assert snap.overall_status == SlaStatus.OK
    assert len(snap.active_alerts) == 0


def test_sla_snapshot_alerts_populated() -> None:
    snap = SlaSnapshot(
        captured_at=_NOW,
        overall_status=SlaStatus.CRITICAL,
        rpo_status=SlaStatus.CRITICAL,
        rto_status=SlaStatus.OK,
        last_backup_ages_s={"postgres": 7200.0},
        last_drill_outcome=None,
        last_drill_at=None,
        next_drill_due_at=None,
        active_alerts=["Backup age for postgres is 7200s (target ≤ 900s)"],
    )
    assert len(snap.active_alerts) == 1


# ---------------------------------------------------------------------------
# RecoveryPlan
# ---------------------------------------------------------------------------


def test_recovery_plan_creates_successfully() -> None:
    plan = RecoveryPlan(
        created_at=_NOW,
        failure_mode=FailureModeCategory.PG_CORRUPTION,
        steps=["stop container", "restore dump", "verify"],
        estimated_rto_s=1800,
        requires_human=False,
    )
    assert plan.requires_human is False
    assert len(plan.steps) == 3


def test_recovery_plan_frozen() -> None:
    plan = RecoveryPlan(
        created_at=_NOW,
        failure_mode=FailureModeCategory.PG_CORRUPTION,
        steps=[],
        estimated_rto_s=900,
        requires_human=False,
    )
    with pytest.raises(Exception):
        plan.requires_human = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Serialization round-trips
# ---------------------------------------------------------------------------


def test_backup_manifest_json_roundtrip() -> None:
    m = _valid_manifest(row_count=5000, pg_lsn="0/1A2B3C4")
    json_str = m.model_dump_json()
    m2 = BackupManifest.model_validate_json(json_str)
    assert m2.manifest_id == m.manifest_id
    assert m2.row_count == 5000
    assert m2.pg_lsn == "0/1A2B3C4"


def test_drill_result_json_roundtrip() -> None:
    d = DrillResult(
        triggered_at=_NOW,
        completed_at=_LATER,
        outcome=DrillOutcome.PASS,
        rto_actual_s=60.0,
        rpo_actual_s=120.0,
        targets_tested=[BackupTarget.POSTGRES, BackupTarget.REDIS],
        restore_results=[_restore_result()],
        rto_target_s=3600,
        rpo_target_s=900,
        rto_met=True,
        rpo_met=True,
    )
    d2 = DrillResult.model_validate_json(d.model_dump_json())
    assert d2.drill_id == d.drill_id
    assert d2.passed is True
