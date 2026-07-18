"""Parquet I/O — the workhorse of the data lake.

Pure functions over a :class:`StorageBackend`. No state, no globals.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from aegis.datalake import constants as C
from aegis.datalake._logging import get_logger
from aegis.datalake.errors import (
    ManifestCorruptError,
    MissingDependencyError,
    ParquetReadError,
    ParquetWriteError,
)
from aegis.datalake.schemas import IngestBatch, WriteManifest
from aegis.datalake.storage import StorageBackend

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# pyarrow lazy import — the lake degrades to JSONL if pyarrow is unavailable
# (only used by the absolute lowest-resource fallback path; main path requires arrow).
# ---------------------------------------------------------------------------
def _require_pyarrow() -> tuple[Any, Any]:
    """Return the pyarrow module; raise MissingDependencyError if missing."""
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        return pa, pq
    except ImportError as exc:
        raise MissingDependencyError(
            "pyarrow is required for Parquet I/O",
            hint="pip install pyarrow>=15.0.0",
        ) from exc


# ---------------------------------------------------------------------------
# Path planning
# ---------------------------------------------------------------------------
def plan_partition_path(
    *,
    layer: str,
    table: str,
    partition_key: str,
    tenant_id: str,
) -> str:
    """Compute the relative directory for a partition.

    Layout::

        {layer}/{table}/{partition_key}/tenant_id={tenant}/

    ``partition_key`` is expected to already be a Hive-style ``key=value``
    string such as ``dt=2026-05-20``.
    """
    if layer not in C.VALID_LAYERS:
        raise ValueError(f"invalid layer: {layer!r}")
    if "=" not in partition_key:
        raise ValueError(f"partition_key must be Hive-style 'key=value'; got {partition_key!r}")
    return f"{layer}/{table}/{partition_key}/{C.TENANT_PARTITION_KEY}={tenant_id}"


def plan_file_path(
    *,
    layer: str,
    table: str,
    partition_key: str,
    tenant_id: str,
    batch_id: str,
) -> str:
    """Compute the full relative file path for a Parquet file."""
    directory = plan_partition_path(
        layer=layer, table=table, partition_key=partition_key, tenant_id=tenant_id
    )
    return f"{directory}/{batch_id}.parquet"


def plan_manifest_path(
    *,
    layer: str,
    table: str,
    partition_key: str,
    tenant_id: str,
    batch_id: str,
) -> str:
    """Compute the manifest path. Manifests are per-file (not per-partition)."""
    directory = plan_partition_path(
        layer=layer, table=table, partition_key=partition_key, tenant_id=tenant_id
    )
    return f"{directory}/{batch_id}{C.MANIFEST_FILENAME}"


# ---------------------------------------------------------------------------
# Encoders
# ---------------------------------------------------------------------------
def rows_to_parquet_bytes(
    rows: Sequence[dict[str, Any]],
    *,
    compression: str = C.PARQUET_COMPRESSION,
    row_group_size: int = C.PARQUET_ROW_GROUP_SIZE,
    compression_level: int = C.PARQUET_COMPRESSION_LEVEL,
) -> bytes:
    """Encode ``rows`` as a Parquet file payload.

    Empty input is valid and returns the bytes of an empty Parquet file.
    All datetime fields are coerced to microsecond UTC.
    """
    pa, pq = _require_pyarrow()
    sink = io.BytesIO()
    try:
        if not rows:
            # Build an empty table with no columns rather than crash on a
            # zero-row write — keeps the manifest pipeline uniform.
            table = pa.table({})
        else:
            # pyarrow infers schema from rows; we normalise datetimes first.
            normalised = [_normalise_row_for_arrow(r) for r in rows]
            table = pa.Table.from_pylist(normalised)

        pq.write_table(
            table,
            sink,
            compression=compression,
            compression_level=compression_level,
            row_group_size=max(row_group_size, 1),
            use_dictionary=True,
            write_statistics=True,
        )
    except Exception as exc:
        raise ParquetWriteError(
            f"pyarrow failed to encode {len(rows)} rows: {exc}",
            hint="check that row dicts contain only JSON-serialisable values",
        ) from exc
    return sink.getvalue()


def _normalise_row_for_arrow(row: dict[str, Any]) -> dict[str, Any]:
    """Normalise a single row dict for pyarrow inference.

    - datetime → ISO-8601 string (we keep timestamps as strings to dodge
      timezone-handling rough edges across Arrow versions and DuckDB
      builds; downstream silver casts them back).
    - Pydantic models → ``.model_dump()``.
    - dict / list values → JSON-string. Arrow's schema inference can fail
      on heterogeneous or empty structs (e.g. ``raw_json={}``); by storing
      them as strings we keep Bronze schema-on-read tolerant and reuniform
      across batches. Silver re-parses these on the way out.
    """
    import json as _json

    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=UTC)
            out[k] = v.astimezone(UTC).isoformat()
        elif hasattr(v, "model_dump"):
            dumped = v.model_dump(mode="json")
            out[k] = _json.dumps(dumped, default=str) if isinstance(dumped, dict | list) else dumped
        elif isinstance(v, dict | list):
            out[k] = _json.dumps(v, default=str)
        else:
            out[k] = v
    return out


def parquet_bytes_to_rows(data: bytes) -> list[dict[str, Any]]:
    """Decode Parquet bytes back to a list of row dicts.

    Primarily used by tests + the catalog `peek` API. Production
    queries should use DuckDB instead.
    """
    _pa, pq = _require_pyarrow()
    try:
        table = pq.read_table(io.BytesIO(data))
    except Exception as exc:
        raise ParquetReadError(f"failed to decode Parquet: {exc}") from exc
    return table.to_pylist()


# ---------------------------------------------------------------------------
# High-level write
# ---------------------------------------------------------------------------
def write_batch(
    *,
    backend: StorageBackend,
    batch: IngestBatch,
    source: str,
) -> WriteManifest:
    """Encode → write → manifest. Idempotent by batch_id.

    If the target Parquet file already exists with the same batch_id,
    the existing manifest is returned untouched (idempotency guarantee).

    Args:
        backend: Where to write (local or S3).
        batch: The :class:`IngestBatch` to persist.
        source: Upstream system label, recorded in the manifest.

    Returns:
        The :class:`WriteManifest` describing what was written.
    """
    batch_id = batch.batch_id
    file_path = plan_file_path(
        layer=batch.layer,
        table=batch.table_name,
        partition_key=batch.partition_key,
        tenant_id=batch.tenant_id,
        batch_id=batch_id,
    )
    manifest_path = plan_manifest_path(
        layer=batch.layer,
        table=batch.table_name,
        partition_key=batch.partition_key,
        tenant_id=batch.tenant_id,
        batch_id=batch_id,
    )

    # Idempotency: same batch already written?
    if backend.exists(file_path) and backend.exists(manifest_path):
        try:
            existing = WriteManifest.from_json(
                backend.get_bytes(manifest_path).decode("utf-8")
            )
        except Exception as exc:  # pragma: no cover — corrupt manifest
            raise ManifestCorruptError(
                f"existing manifest at {manifest_path} is invalid: {exc}",
                hint="delete the manifest + parquet pair manually",
            ) from exc
        _log.info(
            "write.idempotent.skip",
            table=batch.table_name,
            batch_id=batch_id,
            row_count=existing.row_count,
        )
        return existing

    data = rows_to_parquet_bytes(list(batch.rows))
    byte_size = backend.put_bytes(file_path, data)

    manifest = WriteManifest(
        schema_version=1,
        table_name=batch.table_name,
        layer=batch.layer,
        partition_key=batch.partition_key,
        tenant_id=batch.tenant_id,
        row_count=batch.row_count,
        byte_size=byte_size,
        sha256=hashlib.sha256(data).hexdigest(),
        written_at=datetime.now(UTC),
        file_paths=(file_path,),
        source=source,
        batch_id=batch_id,
    )
    backend.put_bytes(manifest_path, manifest.to_json().encode("utf-8"))
    _log.info(
        "write.ok",
        table=batch.table_name,
        layer=batch.layer,
        partition=batch.partition_key,
        rows=batch.row_count,
        bytes=byte_size,
        batch_id=batch_id,
    )
    return manifest


def read_manifest(
    *,
    backend: StorageBackend,
    manifest_path: str,
) -> WriteManifest:
    """Load and validate a manifest from storage."""
    try:
        text = backend.get_bytes(manifest_path).decode("utf-8")
    except ParquetReadError:
        raise
    try:
        return WriteManifest.from_json(text)
    except Exception as exc:
        raise ManifestCorruptError(
            f"invalid manifest at {manifest_path}: {exc}"
        ) from exc


def list_manifests(
    *,
    backend: StorageBackend,
    layer: str,
    table: str,
) -> list[WriteManifest]:
    """Enumerate all manifests under a (layer, table) tree.

    Use sparingly on S3 — paginated LIST is the slow path.
    """
    prefix = f"{layer}/{table}/"
    manifests: list[WriteManifest] = []
    for path in backend.list_prefix(prefix):
        if not path.endswith(C.MANIFEST_FILENAME):
            continue
        try:
            manifests.append(read_manifest(backend=backend, manifest_path=path))
        except ManifestCorruptError:
            _log.warning("manifest.corrupt.skip", path=path)
            continue
    return manifests


__all__ = [
    "list_manifests",
    "parquet_bytes_to_rows",
    "plan_file_path",
    "plan_manifest_path",
    "plan_partition_path",
    "read_manifest",
    "rows_to_parquet_bytes",
    "write_batch",
]
