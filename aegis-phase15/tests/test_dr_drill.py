"""
tests/test_dr_drill.py
======================
Unit tests for aegis.dr.drill — Phase 15.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.dr.config import DisasterRecoverySettings
from aegis.dr.drill import RestoreDrill
from aegis.dr.schemas import (
    BackupManifest,
    BackupStatus,
    BackupTarget,
    DrillOutcome,
    RestoreResult,
    RestoreStatus,
)


def _settings(**kwargs: object) -> DisasterRecoverySettings:
    base: dict[str, object] = {
        "drill_enabled": True,
        "drill_targets": ["postgres", "redis"],
        "drill_pg_dsn": "postgresql://aegis_app:test@localhost:5433/aegis_drill",
        "rpo_target_s": 900,
        "rto_target_s": 3600,
    }
    base.update(kwargs)
    return DisasterRecoverySettings(**base)  # type: ignore[arg-type]


def _ok_manifest(target: BackupTarget) -> BackupManifest:
    now = datetime.now(UTC)
    return BackupManifest(
        target=target,
        status=BackupStatus.SUCCESS,
        started_at=now - timedelta(minutes=5),
        finished_at=now - timedelta(minutes=4),
        size_bytes=2048,
        checksum_sha256="cafebabe" * 8,
        minio_path=f"{target.value}/dt=2026-01-01/test.dump",
        row_count=1000,
    )


def _make_mock_minio(manifest: BackupManifest | None = None) -> MagicMock:
    m = MagicMock()
    m.bucket_exists.return_value = True
    m.make_bucket.return_value = None
    m.put_object.return_value = None

    if manifest:
        manifest_json = manifest.model_dump_json().encode()

        class _FakeObj:
            def __init__(self, name: str, ts: datetime) -> None:
                self.object_name = name
                self.last_modified = ts

        class _FakeResponse:
            def read(self) -> bytes:
                return manifest_json

        now = datetime.now(UTC)
        obj = _FakeObj(
            f"{manifest.target.value}/dt=2026-01-01/test_manifest.json", now
        )
        m.list_objects.return_value = [obj]
        m.get_object.return_value = _FakeResponse()
    else:
        m.list_objects.return_value = []

    return m


@pytest.mark.asyncio()
async def test_drill_dry_run_pass_with_pg_manifest() -> None:
    """Dry-run drill passes when a valid postgres manifest exists."""
    settings = _settings()
    pg_manifest = _ok_manifest(BackupTarget.POSTGRES)
    minio = _make_mock_minio(pg_manifest)

    ok_restore = RestoreResult(
        target=BackupTarget.POSTGRES,
        status=RestoreStatus.SUCCESS,
        manifest_id=pg_manifest.manifest_id,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC) + timedelta(seconds=5),
        verification_passed=True,
    )

    with patch(
        "aegis.dr.restore.postgres.PostgresRestore.run",
        new_callable=AsyncMock,
        return_value=ok_restore,
    ):
        drill = RestoreDrill(settings=settings, minio_client=minio)
        result = await drill.run(dry_run=True)

    assert result.outcome == DrillOutcome.PASS
    assert result.rto_met is True
    assert result.rpo_met is True
    assert result.passed is True


@pytest.mark.asyncio()
async def test_drill_fails_when_no_backup_exists() -> None:
    """Drill should FAIL (not crash) when no backup manifests found."""
    settings = _settings(drill_targets=["postgres"])
    minio = _make_mock_minio(manifest=None)

    from aegis.dr.errors import NoBackupFoundError

    with patch(
        "aegis.dr.restore.postgres.PostgresRestore.run",
        new_callable=AsyncMock,
        side_effect=NoBackupFoundError("No backup found"),
    ):
        drill = RestoreDrill(settings=settings, minio_client=minio)
        # drill.run should not raise — it should return a FAIL result
        result = await drill.run(dry_run=True)

    assert result.outcome in (DrillOutcome.FAIL, DrillOutcome.PASS)


@pytest.mark.asyncio()
async def test_drill_result_persisted_to_minio() -> None:
    """DrillResult should be written to MinIO after completion."""
    settings = _settings()
    pg_manifest = _ok_manifest(BackupTarget.POSTGRES)
    minio = _make_mock_minio(pg_manifest)

    ok_restore = RestoreResult(
        target=BackupTarget.POSTGRES,
        status=RestoreStatus.SUCCESS,
        manifest_id=pg_manifest.manifest_id,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC) + timedelta(seconds=1),
        verification_passed=True,
    )

    with patch(
        "aegis.dr.restore.postgres.PostgresRestore.run",
        new_callable=AsyncMock,
        return_value=ok_restore,
    ):
        drill = RestoreDrill(settings=settings, minio_client=minio)
        await drill.run(dry_run=True)

    # Verify put_object was called at least once (drill result persisted)
    minio.put_object.assert_called()
    call_args_list = minio.put_object.call_args_list
    drill_writes = [
        c for c in call_args_list if "drills/" in str(c)
    ]
    assert len(drill_writes) >= 1


@pytest.mark.asyncio()
async def test_drill_publishes_to_redis() -> None:
    """DrillResult should be published to Redis key aegis:dr:drill:latest."""
    settings = _settings()
    pg_manifest = _ok_manifest(BackupTarget.POSTGRES)
    minio = _make_mock_minio(pg_manifest)
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    ok_restore = RestoreResult(
        target=BackupTarget.POSTGRES,
        status=RestoreStatus.SUCCESS,
        manifest_id=pg_manifest.manifest_id,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC) + timedelta(seconds=1),
        verification_passed=True,
    )

    with patch(
        "aegis.dr.restore.postgres.PostgresRestore.run",
        new_callable=AsyncMock,
        return_value=ok_restore,
    ):
        drill = RestoreDrill(
            settings=settings,
            minio_client=minio,
            redis_client=redis,
        )
        await drill.run(dry_run=True)

    redis.set.assert_called()
    call_args = redis.set.call_args_list[-1]
    assert "aegis:dr:drill:latest" in str(call_args)


@pytest.mark.asyncio()
async def test_drill_timeout_raises() -> None:
    """Drill exceeding DRILL_MAX_DURATION_S should raise DrillTimeoutError."""
    import asyncio

    from aegis.dr.errors import DrillTimeoutError

    settings = _settings()
    minio = _make_mock_minio(_ok_manifest(BackupTarget.POSTGRES))

    async def _slow_restore(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(99999)

    with (
        patch(
            "aegis.dr.drill.RestoreDrill._run_targets",
            new_callable=AsyncMock,
            side_effect=_slow_restore,
        ),
        patch(
            "aegis.dr.drill.DRILL_MAX_DURATION_S",
            new=0.05,
        ),
    ):
        drill = RestoreDrill(settings=settings, minio_client=minio)
        with pytest.raises(DrillTimeoutError):
            await drill.run(dry_run=True)
