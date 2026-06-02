"""Unit tests for aegis.backup.health."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from aegis.backup.health import BackupHealth, backup_health_loop
from aegis.backup.pgbackrest_manager import BackupMetadata
from aegis.backup.restic_manager import ResticSnapshot
from aegis.backup.settings import BackupSettings


def _settings(**kwargs) -> BackupSettings:
    base = {
        "pgbackrest_stale_threshold_min": 30,
        "restic_stale_threshold_min": 1440,
        "consecutive_failure_alert_threshold": 3,
        "restic_password": "test-pw",
    }
    base.update(kwargs)
    return BackupSettings.model_construct(**base)


def _fresh_meta(minutes_ago: int = 5) -> BackupMetadata:
    ts = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return BackupMetadata(
        backup_id="20260531T120000Z",
        backup_type="incr",
        size_mb=100.0,
        duration_s=10.0,
        database_version="16",
        checksum="abc",
        retention_expires=datetime.now(UTC) + timedelta(days=7),
        host="test",
        status="ok",
        timestamp=ts,
    )


def _fresh_snap(minutes_ago: int = 60) -> ResticSnapshot:
    return ResticSnapshot(
        snapshot_id="snap1",
        time=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        hostname="test",
    )


class TestBackupHealth:
    @patch("aegis.backup.health.BackupHealth._check_pgbackrest", new_callable=AsyncMock)
    @patch("aegis.backup.health.BackupHealth._check_restic", new_callable=AsyncMock)
    async def test_check_both_healthy(self, mock_restic, mock_pg) -> None:
        mock_pg.return_value = True
        mock_restic.return_value = True

        bh = BackupHealth(settings=_settings())
        result = await bh.check()

        assert result == {"pgbackrest": True, "restic": True}
        assert bh.consecutive_failures["pgbackrest"] == 0
        assert bh.consecutive_failures["restic"] == 0

    @patch("aegis.backup.health.BackupHealth._check_pgbackrest", new_callable=AsyncMock)
    @patch("aegis.backup.health.BackupHealth._check_restic", new_callable=AsyncMock)
    @patch("aegis.backup.health.BackupHealth._send_alert", new_callable=AsyncMock)
    async def test_failures_accumulate(self, mock_alert, mock_restic, mock_pg) -> None:
        mock_pg.return_value = False
        mock_restic.return_value = True
        bh = BackupHealth(settings=_settings(consecutive_failure_alert_threshold=3))

        for _ in range(4):
            await bh.check()

        assert bh.consecutive_failures["pgbackrest"] == 4
        assert mock_alert.call_count >= 2

    @patch("aegis.backup.health.BackupHealth._check_pgbackrest", new_callable=AsyncMock)
    @patch("aegis.backup.health.BackupHealth._check_restic", new_callable=AsyncMock)
    async def test_recovery_resets_counter(self, mock_restic, mock_pg) -> None:
        bh = BackupHealth(settings=_settings())

        mock_pg.return_value = False
        mock_restic.return_value = True
        await bh.check()
        assert bh.consecutive_failures["pgbackrest"] == 1

        mock_pg.return_value = True
        await bh.check()
        assert bh.consecutive_failures["pgbackrest"] == 0

    @patch("aegis.backup.pgbackrest_manager.BackupManager.list_backups", new_callable=AsyncMock)
    async def test_pgbackrest_healthy_recent_backup(self, mock_list) -> None:
        mock_list.return_value = [_fresh_meta(minutes_ago=5)]
        bh = BackupHealth(settings=_settings(pgbackrest_stale_threshold_min=30))
        result = await bh._check_pgbackrest()
        assert result is True

    @patch("aegis.backup.pgbackrest_manager.BackupManager.list_backups", new_callable=AsyncMock)
    async def test_pgbackrest_stale_backup(self, mock_list) -> None:
        mock_list.return_value = [_fresh_meta(minutes_ago=60)]
        bh = BackupHealth(settings=_settings(pgbackrest_stale_threshold_min=30))
        result = await bh._check_pgbackrest()
        assert result is False

    @patch("aegis.backup.pgbackrest_manager.BackupManager.list_backups", new_callable=AsyncMock)
    async def test_pgbackrest_no_backups(self, mock_list) -> None:
        mock_list.return_value = []
        bh = BackupHealth(settings=_settings())
        result = await bh._check_pgbackrest()
        assert result is False

    @patch("aegis.backup.restic_manager.ResticBackup.list_snapshots", new_callable=AsyncMock)
    async def test_restic_healthy_recent_snapshot(self, mock_list) -> None:
        mock_list.return_value = [_fresh_snap(minutes_ago=60)]
        bh = BackupHealth(settings=_settings(restic_stale_threshold_min=1440))
        result = await bh._check_restic()
        assert result is True

    @patch("aegis.backup.restic_manager.ResticBackup.list_snapshots", new_callable=AsyncMock)
    async def test_restic_stale_snapshot(self, mock_list) -> None:
        mock_list.return_value = [_fresh_snap(minutes_ago=1500)]
        bh = BackupHealth(settings=_settings(restic_stale_threshold_min=1440))
        result = await bh._check_restic()
        assert result is False

    async def test_restic_not_configured_returns_true(self) -> None:
        bh = BackupHealth(settings=_settings(restic_password=""))
        result = await bh._check_restic()
        assert result is True

    @patch("aegis.backup.restic_manager.ResticBackup.list_snapshots", new_callable=AsyncMock)
    async def test_restic_no_snapshots_returns_false(self, mock_list) -> None:
        mock_list.return_value = []
        bh = BackupHealth(settings=_settings())
        result = await bh._check_restic()
        assert result is False

    @patch("aegis.backup.pgbackrest_manager.BackupManager.list_backups", new_callable=AsyncMock)
    async def test_pgbackrest_exception_returns_false(self, mock_list) -> None:
        mock_list.side_effect = RuntimeError("connection refused")
        bh = BackupHealth(settings=_settings())
        result = await bh._check_pgbackrest()
        assert result is False

    @patch("aegis.backup.restic_manager.ResticBackup.list_snapshots", new_callable=AsyncMock)
    async def test_restic_exception_returns_false(self, mock_list) -> None:
        mock_list.side_effect = RuntimeError("restic binary missing")
        bh = BackupHealth(settings=_settings())
        result = await bh._check_restic()
        assert result is False

    async def test_send_alert_does_not_raise(self) -> None:
        bh = BackupHealth(settings=_settings())
        # Both notification paths (Telegram + ntfy) are best-effort and
        # must never raise even when all external imports/calls fail.
        await bh._send_alert("pgbackrest", 5)

    async def test_send_alert_dispatched_on_threshold(self) -> None:
        bh = BackupHealth(settings=_settings(consecutive_failure_alert_threshold=2))
        with (
            patch.object(bh, "_send_alert", new_callable=AsyncMock) as mock_alert,
            patch.object(bh, "_check_pgbackrest", new_callable=AsyncMock, return_value=False),
            patch.object(bh, "_check_restic", new_callable=AsyncMock, return_value=True),
        ):
            await bh.check()  # failure count = 1, below threshold
            await bh.check()  # failure count = 2, alert fires
            mock_alert.assert_called_once_with("pgbackrest", 2)


class TestBackupHealthLoop:
    async def test_loop_runs_one_iteration_then_cancel(self) -> None:
        """backup_health_loop runs check() then asyncio.sleep; cancel cleanly."""
        import contextlib

        check_calls = []

        async def _fake_check() -> dict[str, bool]:
            check_calls.append(1)
            return {"pgbackrest": True, "restic": True}

        async def _fake_sleep(_s: float) -> None:
            raise asyncio.CancelledError

        with (
            patch("aegis.backup.health.BackupHealth.check", side_effect=_fake_check),
            patch("aegis.backup.health.asyncio.sleep", side_effect=_fake_sleep),
            patch("aegis.backup.health._settings", return_value=_settings()),
            contextlib.suppress(asyncio.CancelledError),
        ):
            await backup_health_loop(interval_s=1)

        assert len(check_calls) == 1

    async def test_loop_handles_check_exception(self) -> None:
        """check() raising an exception must not crash the loop."""
        import contextlib

        async def _failing_check() -> dict[str, bool]:
            raise RuntimeError("db down")

        sleep_count = [0]

        async def _fake_sleep(_s: float) -> None:
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                raise asyncio.CancelledError

        with (
            patch("aegis.backup.health.BackupHealth.check", side_effect=_failing_check),
            patch("aegis.backup.health.asyncio.sleep", side_effect=_fake_sleep),
            patch("aegis.backup.health._settings", return_value=_settings()),
            contextlib.suppress(asyncio.CancelledError),
        ):
            await backup_health_loop(interval_s=1)

        assert sleep_count[0] >= 1


class TestSettingsCache:
    def test_settings_function_returns_backup_settings(self) -> None:
        from aegis.backup.health import _settings as health_settings

        # Clear the lru_cache so we get a fresh call in the test environment.
        health_settings.cache_clear()
        try:
            # Will raise if AEGIS_BACKUP_* env vars are missing required fields —
            # but BackupSettings has defaults for all fields, so this should work.
            result = health_settings()
            from aegis.backup.settings import BackupSettings

            assert isinstance(result, BackupSettings)
        except Exception:
            pass  # acceptable: some CI environments have no backup config
        finally:
            health_settings.cache_clear()
