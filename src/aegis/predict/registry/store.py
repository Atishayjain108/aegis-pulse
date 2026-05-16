"""
ModelStore: filesystem-backed model registry.

Layout
------
    <root>/
        manifests/<name>/<version>.json    — JSON ModelManifest
        artifacts/<name>/<version>.bin     — opaque weight blob
        index.json                         — name → current production version

Why JSON manifests on disk and not a database?
----------------------------------------------
* Phase 1 already owns the SQL surface (`signals` table). Adding a
  second SQL surface for model metadata couples promotion to DB
  availability, which is the wrong direction — we want inference to
  survive a DB outage.
* JSON files round-trip cleanly through MinIO (Phase 2's blob layer)
  with a single config flip — see `docs/registry.md`.
* sha256 checks remove the need for the store itself to be trusted:
  even if an attacker swaps the artifact, the manifest hash still
  binds the bytes the deployment was promoted on.

Concurrency
-----------
Writes are atomic-rename (`tempfile + os.replace`). Reads are
copy-on-read. Two workers can promote concurrently and the index file
is updated under an advisory lock; we use a tiny .lock sentinel file
because we don't want to depend on `fcntl` (not portable to Windows
dev boxes).

Schema mapping
--------------
The on-disk manifest is the canonical `aegis.predict.schemas.ModelManifest`
pydantic v2 model — the same one Phase 4 / Phase 9 read. We never invent
a parallel schema.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from ..errors import (
    ModelHashMismatchError,
    ModelLoadError,
    ModelNotFoundError,
)
from ..schemas import ModelManifest

logger = logging.getLogger(__name__)


_PLACEHOLDER_SHA = "0" * 64
"""Sentinel sha256 meaning 'compute this on store'.

Callers may construct a `ModelManifest` before they have the sha256
of the artifact (e.g. if they're streaming weights to disk in chunks
and don't want to buffer them just to hash). They set sha256 to
`_PLACEHOLDER_SHA` and the store overwrites it after computing the
actual hash from the bytes written to disk.

Real sha256 strings are 64 hex chars and never all-zero in practice.
"""


def _sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """Streaming sha256 — never load the whole artifact into memory."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        Path(tmp).replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


class _AdvisoryLock:
    """Cooperative lock via a sentinel file. Best-effort, not crash-safe.

    For a production deployment, swap this for a redis SETNX or a
    real fcntl lock — the interface is intentionally simple so that
    swap is a one-line change.
    """

    def __init__(self, path: Path, timeout_s: float = 30.0) -> None:
        self.path = path
        self.timeout_s = timeout_s
        self._held = False

    def __enter__(self) -> _AdvisoryLock:
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.close(fd)
                self._held = True
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"lock contention on {self.path}") from None
                time.sleep(0.05)

    def __exit__(self, *exc: object) -> None:
        if self._held:
            with contextlib.suppress(OSError):
                self.path.unlink()
            self._held = False


class ModelStore:
    """Read/write API over the on-disk registry layout."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.manifests_dir = self.root / "manifests"
        self.artifacts_dir = self.root / "artifacts"
        self.index_path = self.root / "index.json"
        self.lock_path = self.root / ".registry.lock"

        self.root.mkdir(parents=True, exist_ok=True)
        self.manifests_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            _atomic_write_text(self.index_path, json.dumps({}))

    # ------------------------------------------------------------------
    # write path
    # ------------------------------------------------------------------
    def register(
        self,
        manifest: ModelManifest,
        artifact_bytes: bytes,
    ) -> ModelManifest:
        """Atomically register a (manifest, artifact) pair.

        Returns the manifest with the `sha256` field populated to
        whatever was actually written, so the caller can verify their
        local hash matches.
        """
        artifact_path = self.artifacts_dir / manifest.name / f"{manifest.version}.bin"
        _atomic_write_bytes(artifact_path, artifact_bytes)
        actual_sha = _sha256_file(artifact_path)

        if manifest.sha256 and manifest.sha256 not in {_PLACEHOLDER_SHA, actual_sha}:
            raise ModelHashMismatchError(
                f"sha256 mismatch on register: claimed={manifest.sha256[:12]}, "
                f"actual={actual_sha[:12]}"
            )

        manifest = manifest.model_copy(update={"sha256": actual_sha})
        manifest_path = self.manifests_dir / manifest.name / f"{manifest.version}.json"
        _atomic_write_text(manifest_path, manifest.model_dump_json(indent=2))
        logger.info(
            "registered %s@%s sha=%s stage=%s",
            manifest.name,
            manifest.version,
            actual_sha[:12],
            manifest.stage,
        )
        return manifest

    def promote(self, name: str, version: str) -> ModelManifest:
        """Mark `version` as production. Updates the index atomically."""
        manifest = self.get_manifest(name, version)
        if manifest.stage == "archived":
            raise ModelLoadError(f"refusing to promote archived version {name}@{version}")

        new_manifest = manifest.model_copy(update={"stage": "production"})
        manifest_path = self.manifests_dir / name / f"{version}.json"
        _atomic_write_text(manifest_path, new_manifest.model_dump_json(indent=2))

        with _AdvisoryLock(self.lock_path):
            index = self._read_index()
            previous = index.get(name)
            if previous and previous != version:
                try:
                    prev_manifest = self.get_manifest(name, previous)
                    prev_archived = prev_manifest.model_copy(update={"stage": "archived"})
                    prev_path = self.manifests_dir / name / f"{previous}.json"
                    _atomic_write_text(prev_path, prev_archived.model_dump_json(indent=2))
                except ModelNotFoundError:
                    pass
            index[name] = version
            _atomic_write_text(self.index_path, json.dumps(index, indent=2))

        logger.info("promoted %s@%s to production", name, version)
        return new_manifest

    def archive(self, name: str, version: str) -> ModelManifest:
        """Force a version into 'archived' stage. Used by auto-rollback."""
        manifest = self.get_manifest(name, version)
        archived = manifest.model_copy(update={"stage": "archived"})
        manifest_path = self.manifests_dir / name / f"{version}.json"
        _atomic_write_text(manifest_path, archived.model_dump_json(indent=2))

        with _AdvisoryLock(self.lock_path):
            index = self._read_index()
            if index.get(name) == version:
                del index[name]
                _atomic_write_text(self.index_path, json.dumps(index, indent=2))
        logger.warning("archived %s@%s", name, version)
        return archived

    # ------------------------------------------------------------------
    # read path
    # ------------------------------------------------------------------
    def get_manifest(self, name: str, version: str) -> ModelManifest:
        manifest_path = self.manifests_dir / name / f"{version}.json"
        if not manifest_path.exists():
            raise ModelNotFoundError(f"no manifest for {name}@{version}")
        with manifest_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return ModelManifest.model_validate(data)

    def get_production(self, name: str) -> ModelManifest | None:
        index = self._read_index()
        version = index.get(name)
        if not version:
            return None
        try:
            return self.get_manifest(name, version)
        except ModelNotFoundError:
            return None

    def load_artifact(self, name: str, version: str) -> bytes:
        manifest = self.get_manifest(name, version)
        artifact_path = self.artifacts_dir / name / f"{version}.bin"
        if not artifact_path.exists():
            raise ModelNotFoundError(f"manifest exists but artifact missing: {name}@{version}")
        actual_sha = _sha256_file(artifact_path)
        if actual_sha != manifest.sha256:
            raise ModelHashMismatchError(
                f"artifact tampered: {name}@{version} "
                f"manifest_sha={manifest.sha256[:12]} actual_sha={actual_sha[:12]}"
            )
        return artifact_path.read_bytes()

    def list_versions(self, name: str) -> tuple[str, ...]:
        d = self.manifests_dir / name
        if not d.exists():
            return ()
        return tuple(sorted(p.stem for p in d.glob("*.json")))

    def list_models(self) -> tuple[str, ...]:
        if not self.manifests_dir.exists():
            return ()
        return tuple(sorted(p.name for p in self.manifests_dir.iterdir() if p.is_dir()))

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------
    def _read_index(self) -> dict:
        if not self.index_path.exists():
            return {}
        with self.index_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def wipe(self) -> None:  # pragma: no cover — destructive, test-only
        if self.root.exists():
            shutil.rmtree(self.root)
        self.__init__(self.root)

    @staticmethod
    def now_iso() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
