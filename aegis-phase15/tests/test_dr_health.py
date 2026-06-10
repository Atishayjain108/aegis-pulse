"""
tests/test_dr_health.py
=======================
Unit tests for aegis.dr.health — Phase 15.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from aegis.dr.config import DisasterRecoverySettings
from aegis.dr.health import DrHealthChecker
from aegis.dr.schemas import (
    BackupManifest,
    BackupStatus,
    BackupTarget,
    SlaStatus,
)


def _settings(**kwargs: object) -> DisasterRecoverySettings:
    return DisasterRecoverySettings(
        rpo_target_s=900,
        rto_target_s=3600,
        **kwargs,
    )


def _manifest(target: BackupTarget, age_s: float) -> BackupManifest:
    now = datetime.now(UTC)
    finished = now - timedelta(seconds=age_s)
    return BackupManifest(
        target=target,
        status=BackupStatus.SUCCESS,
        started_at=finished - timedelta(seconds=10),
        finished_at=finished,
        size_bytes=512,
        checksum_sha256="00" * 32,
        minio_path=f"{target.value}/test.dump",
    )


def _minio_with_manifest(manifest: BackupManifest | None) -> MagicMock:
    m = MagicMock()

    if manifest:
        manifest_json = manifest.model_dump_json().encode()

        class _FakeObj:
            def __init__(self) -> None:
                self.object_name = (
                    f"{manifest.target.value}/dt=2026-01-01/test_manifest.json"
                )
                self.last_modified = datetime.now(UTC)

        class _FakeResp:
            def read(self) -> bytes:
                return manifest_json

        m.list_objects.return_value = [_FakeObj()]
        m.get_object.return_value = _FakeResp()
    else:
        m.list_objects.return_value = []

    return m


@pytest.mark.asyncio()
async def test_health_ok_when_recent_backup() -> None:
    """When last backup is <15 min old, status should be OK."""
    settings = _settings()
    pg_manifest = _manifest(BackupTarget.POSTGRES, age_s=300)  # 5 min old
    minio = _minio_with_manifest(pg_manifest)

    checker = DrHealthChecker(settings=settings, minio_client=minio)
    snap = await checker.check()

    assert snap.overall_status == SlaStatus.OK
    assert snap.last_backup_ages_s.get("postgres", float("inf")) < 900


@pytest.mark.asyncio()
async def test_health_warning_when_backup_stale() -> None:
    """When backup is >30 min (WARN threshold) old, status=WARNING or CRITICAL."""
    settings = _settings()
    pg_manifest = _manifest(BackupTarget.POSTGRES, age_s=2000)  # 33 min — above WARN=1800
    minio = _minio_with_manifest(pg_manifest)

    checker = DrHealthChecker(settings=settings, minio_client=minio)
    snap = await checker.check()

    assert snap.rpo_status in (SlaStatus.WARNING, SlaStatus.CRITICAL)


@pytest.mark.asyncio()
async def test_health_critical_when_no_backup() -> None:
    """When no backup exists, status should be CRITICAL."""
    settings = _settings()
    minio = _minio_with_manifest(None)

    checker = DrHealthChecker(settings=settings, minio_client=minio)
    snap = await checker.check()

    assert snap.overall_status == SlaStatus.CRITICAL
    assert len(snap.active_alerts) > 0


@pytest.mark.asyncio()
async def test_health_publishes_to_redis() -> None:
    """Health check should publish snapshot to Redis."""
    settings = _settings()
    pg_manifest = _manifest(BackupTarget.POSTGRES, age_s=60)
    minio = _minio_with_manifest(pg_manifest)
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    checker = DrHealthChecker(
        settings=settings, minio_client=minio, redis_client=redis
    )
    await checker.check()

    redis.set.assert_called_once()
    key_arg = redis.set.call_args[0][0]
    assert key_arg == "aegis:dr:health"


@pytest.mark.asyncio()
async def test_health_tolerates_redis_failure() -> None:
    """If Redis is unavailable, health check should still return a snapshot."""
    settings = _settings()
    pg_manifest = _manifest(BackupTarget.POSTGRES, age_s=60)
    minio = _minio_with_manifest(pg_manifest)
    redis = AsyncMock()
    redis.set = AsyncMock(side_effect=ConnectionError("Redis down"))

    checker = DrHealthChecker(
        settings=settings, minio_client=minio, redis_client=redis
    )
    # Should not raise
    snap = await checker.check()
    assert snap is not None


@pytest.mark.asyncio()
async def test_health_tolerates_minio_failure() -> None:
    """If MinIO list_objects fails, health should return CRITICAL gracefully."""
    settings = _settings()
    minio = MagicMock()
    minio.list_objects.side_effect = Exception("MinIO unreachable")

    checker = DrHealthChecker(settings=settings, minio_client=minio)
    snap = await checker.check()

    assert snap.overall_status == SlaStatus.CRITICAL
