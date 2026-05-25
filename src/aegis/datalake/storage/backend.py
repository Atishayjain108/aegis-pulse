"""Storage backends for the Phase 10 data lake.

Two implementations, single interface:
  - LocalStorageBackend : direct ext4/NTFS filesystem (tests, offline dev)
  - S3StorageBackend    : MinIO / AWS S3 (production)

Both back the medallion layout::

    {root}/{layer}/{table}/dt=YYYY-MM-DD/tenant_id=<uuid>/<batch_id>.parquet
    {root}/{layer}/{table}/dt=YYYY-MM-DD/tenant_id=<uuid>/_manifest.json

The backend is the *only* layer that knows whether we're talking to S3.
Everything above uses the Protocol.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from aegis.datalake._logging import get_logger
from aegis.datalake.errors import (
    BucketNotFoundError,
    MissingDependencyError,
    ParquetReadError,
    ParquetWriteError,
)

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------
@runtime_checkable
class StorageBackend(Protocol):
    """The contract every storage backend must satisfy.

    All paths are *URI-style* without a scheme — backend chooses the
    transport. Implementations MUST be thread-safe for read; write
    serialisation is the caller's responsibility (Bronze writer holds a lock).
    """

    backend_name: str

    def put_bytes(self, path: str, data: bytes) -> int:
        """Write ``data`` to ``path``. Returns bytes written. Overwrites."""
        ...

    def get_bytes(self, path: str) -> bytes:
        """Read ``path`` as bytes. Raises StorageError if missing."""
        ...

    def exists(self, path: str) -> bool: ...

    def delete(self, path: str) -> bool:
        """Returns True if deleted, False if didn't exist."""
        ...

    def list_prefix(self, prefix: str) -> Iterator[str]:
        """Yield object paths under ``prefix`` (recursive)."""
        ...

    def uri_for(self, path: str) -> str:
        """Return a fully-qualified URI suitable for DuckDB ``read_parquet``."""
        ...


# ---------------------------------------------------------------------------
# Local filesystem backend
# ---------------------------------------------------------------------------
class LocalStorageBackend:
    """File-system-backed storage. Ideal for tests + offline dev."""

    backend_name = "local"

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    # ---- private --------------------------------------------------------
    def _resolve(self, path: str) -> Path:
        # Defensive: forbid absolute paths and `..` traversal.
        path = path.lstrip("/")
        if ".." in Path(path).parts:
            raise ValueError(f"path traversal denied: {path!r}")
        return self._root / path

    # ---- protocol -------------------------------------------------------
    def put_bytes(self, path: str, data: bytes) -> int:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write — write to a sibling temp then rename.
        tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}.{time.time_ns()}")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, target)  # noqa: PTH105 — test monkeypatches os.replace directly
        except OSError as exc:
            raise ParquetWriteError(
                f"failed to write {path}: {exc}",
                hint="check disk free space and filesystem permissions",
            ) from exc
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        return len(data)

    def get_bytes(self, path: str) -> bytes:
        target = self._resolve(path)
        try:
            return target.read_bytes()
        except FileNotFoundError as exc:
            raise ParquetReadError(f"missing: {path}") from exc
        except OSError as exc:
            raise ParquetReadError(f"failed to read {path}: {exc}") from exc

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def delete(self, path: str) -> bool:
        target = self._resolve(path)
        if target.is_file():
            target.unlink()
            return True
        if target.is_dir():
            shutil.rmtree(target)
            return True
        return False

    def list_prefix(self, prefix: str) -> Iterator[str]:
        base = self._resolve(prefix) if prefix else self._root
        if not base.exists():
            return
        if base.is_file():
            yield str(base.relative_to(self._root))
            return
        for p in sorted(base.rglob("*")):
            if p.is_file():
                yield str(p.relative_to(self._root))

    def uri_for(self, path: str) -> str:
        return str(self._resolve(path))


# ---------------------------------------------------------------------------
# S3 / MinIO backend
# ---------------------------------------------------------------------------
class S3StorageBackend:
    """S3 (or MinIO) backend.

    Uses boto3 (lazy-imported). When boto3 is missing, instantiation raises
    a typed :class:`MissingDependencyError` — the caller can detect and
    swap to LocalStorageBackend.
    """

    backend_name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        use_ssl: bool = False,
    ) -> None:
        try:
            import boto3  # type: ignore[import-untyped]
            from botocore.config import Config  # type: ignore[import-untyped]
            from botocore.exceptions import ClientError  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover
            raise MissingDependencyError(
                "boto3 is required for S3StorageBackend",
                hint="pip install boto3, or set use_local_filesystem=True",
            ) from exc

        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._ClientError = ClientError

        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            use_ssl=use_ssl,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 5, "mode": "standard"},
                connect_timeout=5,
                read_timeout=30,
            ),
        )
        self._ensure_bucket()

    # ---- private --------------------------------------------------------
    def _ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except self._ClientError as exc:
            err = getattr(exc, "response", {}).get("Error", {})
            code = err.get("Code")
            if code in {"404", "NoSuchBucket", "NotFound"}:
                try:
                    self._client.create_bucket(Bucket=self._bucket)
                    _log.info("s3.bucket.created", bucket=self._bucket)
                except self._ClientError as create_exc:  # pragma: no cover
                    raise BucketNotFoundError(
                        f"cannot create bucket {self._bucket}: {create_exc}",
                        hint="check S3 credentials and endpoint",
                    ) from create_exc
            else:  # pragma: no cover
                raise BucketNotFoundError(
                    f"head_bucket failed for {self._bucket}: {exc}",
                    hint="check S3 endpoint and credentials",
                ) from exc

    # ---- protocol -------------------------------------------------------
    def put_bytes(self, path: str, data: bytes) -> int:
        try:
            self._client.put_object(Bucket=self._bucket, Key=path, Body=data)
        except self._ClientError as exc:  # pragma: no cover — network path
            raise ParquetWriteError(
                f"S3 put_object failed for {path}: {exc}",
                hint="check S3 endpoint, credentials, and quota",
            ) from exc
        return len(data)

    def get_bytes(self, path: str) -> bytes:
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=path)
            return resp["Body"].read()
        except self._ClientError as exc:
            raise ParquetReadError(f"S3 get_object failed for {path}: {exc}") from exc

    def exists(self, path: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=path)
            return True
        except self._ClientError:
            return False

    def delete(self, path: str) -> bool:
        if not self.exists(path):
            return False
        try:
            self._client.delete_object(Bucket=self._bucket, Key=path)
        except self._ClientError as exc:  # pragma: no cover
            raise ParquetWriteError(f"S3 delete failed for {path}: {exc}") from exc
        return True

    def list_prefix(self, prefix: str) -> Iterator[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", ()):
                    yield obj["Key"]
        except self._ClientError as exc:  # pragma: no cover
            raise ParquetReadError(
                f"S3 list_objects_v2 failed for {prefix}: {exc}"
            ) from exc

    def uri_for(self, path: str) -> str:
        return f"s3://{self._bucket}/{path}"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_backend(settings: Any) -> StorageBackend:
    """Instantiate the right backend from settings.

    Args:
        settings: A :class:`aegis.datalake.settings.DataLakeSettings` instance.

    Returns:
        A storage backend honouring the :class:`StorageBackend` protocol.
    """
    if settings.use_local_filesystem:
        settings.ensure_local_dirs()
        return LocalStorageBackend(settings.local_root)
    return S3StorageBackend(
        bucket=settings.bucket,
        endpoint_url=settings.s3_endpoint,
        access_key=settings.s3_access_key.get_secret_value(),
        secret_key=settings.s3_secret_key.get_secret_value(),
        region=settings.s3_region,
        use_ssl=settings.s3_use_ssl,
    )


__all__ = [
    "LocalStorageBackend",
    "S3StorageBackend",
    "StorageBackend",
    "build_backend",
]
