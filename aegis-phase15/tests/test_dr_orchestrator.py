"""
tests/test_dr_orchestrator.py
==============================
Unit tests for aegis.dr.orchestrator — Phase 15.

Uses unittest.mock to avoid real MinIO / Redis / pg_dump calls.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.dr.config import DisasterRecoverySettings
from aegis.dr.orchestrator import DrOrchestrator
from aegis.dr.schemas import BackupManifest, BackupStatus, BackupTarget


def _settings(**kwargs: object) -> DisasterRecoverySettings:
    base: dict[str, object] = {
        "pg_backup_interval_s": 1,
        "redis_backup_interval_s": 1,
        "model_backup_interval_s": 1,
        "drill_enabled": False,
        "alert_on_backup_failure": False,
    }
    base.update(kwargs)
    return DisasterRecoverySettings(**base)  # type: ignore[arg-type]


def _ok_manifest(target: BackupTarget) -> BackupManifest:
    now = datetime.now(UTC)
    return BackupManifest(
        target=target,
        status=BackupStatus.SUCCESS,
        started_at=now,
        finished_at=now + timedelta(seconds=1),
        size_bytes=1024,
        checksum_sha256="deadbeef" * 8,
        minio_path=f"{target.value}/test.dump",
    )


@pytest.fixture()
def mock_minio() -> MagicMock:
    m = MagicMock()
    m.bucket_exists.return_value = True
    return m


@pytest.fixture()
def mock_redis() -> AsyncMock:
    r = AsyncMock()
    r.set = AsyncMock(return_value=True)
    return r


@pytest.mark.asyncio()
async def test_orchestrator_starts_and_stops(
    mock_minio: MagicMock, mock_redis: AsyncMock
) -> None:
    """Orchestrator should start background tasks and stop cleanly."""
    settings = _settings()

    with (
        patch(
            "aegis.dr.backup.postgres.PostgresBackup.run",
            new_callable=AsyncMock,
            return_value=_ok_manifest(BackupTarget.POSTGRES),
        ),
        patch(
            "aegis.dr.backup.redis.RedisBackup.run",
            new_callable=AsyncMock,
            return_value=_ok_manifest(BackupTarget.REDIS),
        ),
        patch(
            "aegis.dr.backup.models.ModelRegistryBackup.run",
            new_callable=AsyncMock,
            return_value=_ok_manifest(BackupTarget.MODELS),
        ),
        patch(
            "aegis.dr.backup.restic.ResticBackup.run",
            new_callable=AsyncMock,
            return_value=_ok_manifest(BackupTarget.RESTIC),
        ),
        patch(
            "aegis.dr.health.DrHealthChecker.check",
            new_callable=AsyncMock,
        ),
    ):
        orch = DrOrchestrator(
            settings=settings,
            minio_client=mock_minio,
            redis_client=mock_redis,
        )
        await orch.start()
        assert len(orch._tasks) > 0

        await asyncio.sleep(0.1)

        await orch.stop()
        # All tasks should be done (cancelled or completed)
        for task in orch._tasks:
            assert task.done()


@pytest.mark.asyncio()
async def test_orchestrator_publishes_to_redis_on_success(
    mock_minio: MagicMock, mock_redis: AsyncMock
) -> None:
    """After a successful backup, the orchestrator publishes to Redis."""
    settings = _settings(pg_backup_interval_s=9999)  # long interval — run only once

    manifest = _ok_manifest(BackupTarget.POSTGRES)

    with patch(
        "aegis.dr.backup.postgres.PostgresBackup.run",
        new_callable=AsyncMock,
        return_value=manifest,
    ):
        orch = DrOrchestrator(
            settings=settings,
            minio_client=mock_minio,
            redis_client=mock_redis,
        )
        # Call _publish_last_backup directly
        await orch._publish_last_backup("postgres", manifest)

    mock_redis.set.assert_called_once()
    call_args = mock_redis.set.call_args
    assert "aegis:dr:last_backup:postgres" in str(call_args)


@pytest.mark.asyncio()
async def test_orchestrator_handles_backup_failure_gracefully(
    mock_minio: MagicMock, mock_redis: AsyncMock
) -> None:
    """A backup failure should not crash the orchestrator loop."""
    settings = _settings(alert_on_backup_failure=False)

    from aegis.dr.errors import PostgresBackupError

    with patch(
        "aegis.dr.backup.postgres.PostgresBackup.run",
        new_callable=AsyncMock,
        side_effect=PostgresBackupError("pg_dump failed"),
    ):
        orch = DrOrchestrator(
            settings=settings,
            minio_client=mock_minio,
            redis_client=mock_redis,
        )
        # Run one loop iteration directly without starting full background tasks
        results = []
        try:
            manifest = await orch._run_pg_backup()
            results.append(manifest)
        except PostgresBackupError:
            results.append("error")

        assert results[-1] == "error"  # Error raised, not swallowed silently


@pytest.mark.asyncio()
async def test_orchestrator_sends_alert_on_failure(
    mock_minio: MagicMock, mock_redis: AsyncMock
) -> None:
    """When alert_on_backup_failure=True and ntfy configured, alert is sent."""
    settings = _settings(
        alert_on_backup_failure=True,
        ntfy_topic="test-topic",
    )

    orch = DrOrchestrator(
        settings=settings,
        minio_client=mock_minio,
        redis_client=mock_redis,
    )

    import httpx

    with patch.object(
        httpx.AsyncClient, "post", new_callable=AsyncMock
    ) as mock_post:
        await orch._send_alert("test alert message", priority="high")
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "test-topic" in str(call_kwargs)
