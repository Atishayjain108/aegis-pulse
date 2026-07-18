"""
aegis.backup.restic_manager
============================

restic wrapper — encrypted filesystem backups to local repo and optional B2.

Handles daily encrypted snapshots of the AEGIS workspace, ChromaDB data,
model registry, and config. All subprocess calls go through asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import structlog

from aegis.backup.errors import (
    ResticCommandError,
    ResticInitError,
    ResticPasswordMissingError,
    ResticRestoreError,
    ResticTimeoutError,
)
from aegis.backup.settings import BackupSettings

_log = structlog.get_logger("aegis.backup.restic")


@lru_cache(maxsize=1)
def _settings() -> BackupSettings:
    return BackupSettings()


# ---------------------------------------------------------------------------
# Paths that are backed up by default
# ---------------------------------------------------------------------------

_DEFAULT_BACKUP_PATHS = [
    Path.home() / "code" / "aegis-pulse",
    Path.home() / ".aegis",
    Path.home() / ".cache" / "aegis",
]

_DEFAULT_EXCLUDES = [
    "**/.git",
    "**/node_modules",
    "**/__pycache__",
    "**/.pytest_cache",
    "**/venv",
    "**/.env.local",
    "**/.env",
    "**/secrets",
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ResticSnapshot:
    """Represents a single restic snapshot."""

    snapshot_id: str
    time: datetime
    hostname: str
    paths: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    short_id: str = ""

    @classmethod
    def from_json(cls, data: dict) -> ResticSnapshot:
        return cls(
            snapshot_id=data.get("id", ""),
            short_id=data.get("short_id", ""),
            time=datetime.fromisoformat(data["time"].replace("Z", "+00:00")),
            hostname=data.get("hostname", "unknown"),
            paths=data.get("paths", []),
            tags=data.get("tags", []),
        )

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "short_id": self.short_id,
            "time": self.time.isoformat(),
            "hostname": self.hostname,
            "paths": self.paths,
            "tags": self.tags,
        }


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ResticBackup:
    """Manage restic-based encrypted filesystem backups.

    Usage::

        backup = ResticBackup()
        snapshot_id = await backup.backup(label="pre-deploy")
        snapshots = await backup.list_snapshots()
        ok = await backup.restore("latest", Path("/tmp/restore"))
    """

    def __init__(self, settings: BackupSettings | None = None) -> None:
        cfg = settings or _settings()
        if not cfg.restic_password:
            raise ResticPasswordMissingError(
                "AEGIS_BACKUP_RESTIC_PASSWORD is not set; restic cannot start",
                code="AEGIS-BACKUP-0014",
            )
        self._password = cfg.restic_password
        self._repo = cfg.restic_repository
        self._b2_bucket = cfg.restic_b2_bucket
        self._retention_days = cfg.restic_retention_days
        self._exclude_paths = _DEFAULT_EXCLUDES
        self._backup_paths = _DEFAULT_BACKUP_PATHS

    # ------------------------------------------------------------------
    # Internal helper: build env dict for restic
    # ------------------------------------------------------------------

    def _env(self) -> dict[str, str]:
        return {**os.environ, "RESTIC_PASSWORD": self._password}

    # ------------------------------------------------------------------
    # Repository init
    # ------------------------------------------------------------------

    async def init_repo(self) -> bool:
        """Initialise the restic repository (idempotent)."""
        cmd = ["restic", "-r", self._repo, "init"]
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env=self._env(),
            )
            if result.returncode == 0 or "already exists" in result.stderr:
                _log.info("restic.repo_ready", repo=self._repo)
                return True
            raise ResticInitError(
                f"restic init failed: {result.stderr}",
                code="AEGIS-BACKUP-0005",
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise ResticInitError(
                f"restic binary not found or not executable: {exc}",
                code="AEGIS-BACKUP-0005",
            ) from exc

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------

    async def backup(
        self,
        label: str | None = None,
        paths: list[Path] | None = None,
    ) -> str | None:
        """Create a new snapshot.

        Args:
            label: Optional tag stored with the snapshot.
            paths: Paths to snapshot; defaults to ``_DEFAULT_BACKUP_PATHS``.

        Returns:
            Snapshot ID string, or ``None`` on failure.
        """
        await self.init_repo()

        target_paths = paths or self._backup_paths
        existing = [str(p) for p in target_paths if p.exists()]
        if not existing:
            _log.warning("restic.backup_no_paths", paths=[str(p) for p in target_paths])
            return None

        cmd = [
            "restic", "-r", self._repo,
            "backup",
            "--json",
        ]
        for exc_pat in self._exclude_paths:
            cmd.extend(["--exclude", exc_pat])
        if label:
            cmd.extend(["--tag", label])
        cmd.extend(existing)

        _log.info("restic.backup_start", paths=existing)
        start = time.monotonic()

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,
                check=True,
                env=self._env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise ResticTimeoutError(
                "restic backup timed out after 3600s",
                code="AEGIS-BACKUP-0006",
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise ResticCommandError(
                f"restic backup failed: {exc.stderr}",
                code="AEGIS-BACKUP-0007",
            ) from exc

        duration_s = time.monotonic() - start
        snapshot_id = self._parse_snapshot_id(result.stdout)

        _log.info(
            "restic.backup_complete",
            snapshot_id=snapshot_id,
            duration_s=round(duration_s, 2),
        )

        if self._b2_bucket:
            await self._sync_to_b2()

        return snapshot_id

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    async def restore(
        self,
        snapshot_id: str,
        target_path: Path,
        *,
        host: str | None = None,
    ) -> bool:
        """Restore a snapshot to *target_path*.

        Args:
            snapshot_id: Snapshot ID or ``"latest"``.
            target_path: Directory to restore into.
            host: If set, only restore files matching this hostname.

        Returns:
            ``True`` on success.
        """
        target_path.mkdir(parents=True, exist_ok=True)

        cmd = [
            "restic", "-r", self._repo,
            "restore", snapshot_id,
            "--target", str(target_path),
            "--json",
        ]
        if host:
            cmd.extend(["--host", host])

        _log.warning("restic.restore_start", snapshot_id=snapshot_id, target=str(target_path))

        try:
            await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,
                check=True,
                env=self._env(),
            )
        except subprocess.CalledProcessError as exc:
            raise ResticRestoreError(
                f"restic restore failed: {exc.stderr}",
                code="AEGIS-BACKUP-0008",
            ) from exc

        _log.info("restic.restore_complete", snapshot_id=snapshot_id)
        return True

    # ------------------------------------------------------------------
    # Listing + pruning
    # ------------------------------------------------------------------

    async def list_snapshots(self) -> list[ResticSnapshot]:
        """Return all snapshots sorted oldest-first."""
        cmd = ["restic", "-r", self._repo, "snapshots", "--json"]
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
                env=self._env(),
            )
            data = json.loads(result.stdout)
            snapshots = []
            for item in data:
                try:
                    snapshots.append(ResticSnapshot.from_json(item))
                except (KeyError, ValueError):
                    continue
            return sorted(snapshots, key=lambda s: s.time)
        except Exception as exc:
            _log.error("restic.list_failed", error=str(exc))
            return []

    async def prune(self) -> int:
        """Forget + prune old snapshots per retention policy."""
        cmd = [
            "restic", "-r", self._repo,
            "forget",
            f"--keep-daily={self._retention_days}",
            "--prune",
            "--json",
        ]
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                check=True,
                env=self._env(),
            )
            output = json.loads(result.stdout)
            deleted = len(output.get("remove", [])) if isinstance(output, dict) else 0
            _log.info("restic.pruned", count=deleted)
            return deleted
        except Exception as exc:
            _log.error("restic.prune_failed", error=str(exc))
            return 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_snapshot_id(self, stdout: str) -> str | None:
        """Extract snapshot_id from the last JSON line of restic output."""
        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        for line in reversed(lines):
            try:
                data = json.loads(line)
                sid = data.get("snapshot_id") or data.get("short_id")
                if sid:
                    return sid
            except json.JSONDecodeError:
                continue
        return None

    async def _sync_to_b2(self) -> None:
        """Sync local restic repo to Backblaze B2 via rclone (best-effort)."""
        try:
            cmd = [
                "rclone", "sync",
                self._repo.removeprefix("local:"),
                f"b2:{self._b2_bucket}",
                "--transfers=4",
            ]
            await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,
                check=True,
            )
            _log.info("restic.b2_sync_complete")
        except Exception as exc:
            _log.warning("restic.b2_sync_failed", error=str(exc))
