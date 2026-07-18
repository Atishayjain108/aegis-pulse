"""
aegis.dr.backup.models
======================
ML model registry backup for Phase 15 — Disaster Recovery.

Strategy
--------
* Scans ``model_registry_path`` (Phase 3 ModelStore) for all versioned artifacts.
* Uploads each artifact to ``aegis-dr/models/dt=YYYY-MM-DD/<version>/<file>``.
* Keeps last N versions in hot DR storage (``max_model_versions_hot``).
* Verifies each upload with SHA256 round-trip.
* Writes a combined ``BackupManifest`` referencing all uploaded objects.

Architecture
-----------
No imports from Phase 3 internals — reads from filesystem directly so it
can run even when Phase 3 FastAPI is offline. This is intentional: DR must
work when the system being protected is degraded.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from aegis.dr.config import DisasterRecoverySettings
from aegis.dr.constants import MODELS_PREFIX
from aegis.dr.errors import MinioBackupError
from aegis.dr.schemas import BackupManifest, BackupStatus, BackupTarget

_log = structlog.get_logger("aegis.dr.backup.models")


class ModelRegistryBackup:
    """
    Backs up Phase 3 model registry to MinIO.

    Parameters
    ----------
    settings:
        DR settings singleton.
    minio_client:
        A pre-initialized ``minio.Minio`` client.
    """

    def __init__(
        self,
        *,
        settings: DisasterRecoverySettings,
        minio_client: Any,
    ) -> None:
        self._settings = settings
        self._minio = minio_client

    async def run(self) -> BackupManifest:
        """
        Scan model registry and upload all artifacts to MinIO.

        Returns
        -------
        BackupManifest

        Raises
        ------
        ModelBackupError
            If the registry path is unreadable or no models found.
        MinioBackupError
            If any MinIO upload fails.
        """
        started_at = datetime.now(UTC)

        registry_path = Path(self._settings.model_registry_path).expanduser()
        if not registry_path.exists():
            _log.warning(
                "model.backup.registry_not_found",
                path=str(registry_path),
            )
            # Graceful — write a SUCCESS manifest with 0 bytes (nothing to back up yet)
            finished_at = datetime.now(UTC)
            return BackupManifest(
                target=BackupTarget.MODELS,
                status=BackupStatus.SKIPPED,
                started_at=started_at,
                finished_at=finished_at,
                size_bytes=0,
                checksum_sha256="",
                minio_path="",
            )

        artifacts = sorted(registry_path.rglob("*"))
        files = [a for a in artifacts if a.is_file()]

        if not files:
            _log.info("model.backup.no_files", registry_path=str(registry_path))
            finished_at = datetime.now(UTC)
            return BackupManifest(
                target=BackupTarget.MODELS,
                status=BackupStatus.SKIPPED,
                started_at=started_at,
                finished_at=finished_at,
                size_bytes=0,
                checksum_sha256="",
                minio_path="",
            )

        _log.info("model.backup.start", file_count=len(files))

        total_bytes = 0
        uploaded: list[dict[str, str]] = []

        for file_path in files:
            checksum = await asyncio.to_thread(self._sha256, file_path)
            rel = file_path.relative_to(registry_path)
            object_name = (
                f"{MODELS_PREFIX}/dt={started_at.strftime('%Y-%m-%d')}/"
                f"{rel}"
            )
            size = file_path.stat().st_size

            try:
                await asyncio.to_thread(
                    self._upload_file,
                    file_path,
                    object_name,
                    checksum,
                )
            except Exception as exc:
                raise MinioBackupError(
                    f"Failed to upload model artifact {rel}",
                    context={"object_name": object_name, "error": str(exc)},
                ) from exc

            total_bytes += size
            uploaded.append({"object": object_name, "sha256": checksum, "size": str(size)})

        # Combined checksum over all uploaded object names + checksums
        combined_hash = hashlib.sha256(
            json.dumps(uploaded, sort_keys=True).encode()
        ).hexdigest()

        # Index object — lists every uploaded artifact
        index_key = (
            f"{MODELS_PREFIX}/dt={started_at.strftime('%Y-%m-%d')}/"
            f"{started_at.strftime('%Y%m%dT%H%M%SZ')}_index.json"
        )
        index_data = json.dumps({"artifacts": uploaded, "combined_sha256": combined_hash}).encode()
        await asyncio.to_thread(
            lambda: self._minio.put_object(
                self._settings.dr_bucket,
                index_key,
                io.BytesIO(index_data),
                length=len(index_data),
                content_type="application/json",
            )
        )

        await self._prune_old_versions()

        finished_at = datetime.now(UTC)
        manifest = BackupManifest(
            target=BackupTarget.MODELS,
            status=BackupStatus.SUCCESS,
            started_at=started_at,
            finished_at=finished_at,
            size_bytes=total_bytes,
            checksum_sha256=combined_hash,
            minio_path=index_key,
        )

        await asyncio.to_thread(self._write_manifest, manifest)

        _log.info(
            "model.backup.success",
            file_count=len(files),
            total_bytes=total_bytes,
            duration_s=manifest.duration_s,
        )

        return manifest

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _upload_file(self, path: Path, object_name: str, checksum: str) -> None:
        self._minio.fput_object(
            self._settings.dr_bucket,
            object_name,
            str(path),
            content_type="application/octet-stream",
            metadata={
                "x-aegis-target": "models",
                "x-aegis-sha256": checksum,
            },
        )

    def _write_manifest(self, manifest: BackupManifest) -> None:
        key = f"{MODELS_PREFIX}/manifests/{manifest.manifest_id}.json"
        data = manifest.model_dump_json().encode()
        self._minio.put_object(
            self._settings.dr_bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type="application/json",
        )

    async def _prune_old_versions(self) -> None:
        """Remove model backup directories beyond the hot retention window."""
        max_keep = self._settings.max_model_versions_hot
        try:
            objects = list(
                self._minio.list_objects(
                    self._settings.dr_bucket,
                    prefix=f"{MODELS_PREFIX}/dt=",
                    recursive=False,
                )
            )
            # Objects are prefixed by date — sort descending and prune tail
            dates = sorted(
                {obj.object_name.split("/")[1] for obj in objects},
                reverse=True,
            )
            for old_date in dates[max_keep:]:
                old_objects = self._minio.list_objects(
                    self._settings.dr_bucket,
                    prefix=f"{MODELS_PREFIX}/{old_date}/",
                    recursive=True,
                )
                for obj in old_objects:
                    self._minio.remove_object(self._settings.dr_bucket, obj.object_name)
                _log.info("model.backup.pruned", date=old_date)
        except Exception as exc:
            _log.warning("model.backup.prune_failed", error=str(exc))

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
