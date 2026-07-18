"""Bronze layer writer — raw, immutable ingestion.

Bronze is the audit trail. We accept whatever upstream sends us, normalise
datetimes to UTC, encode as Parquet, and stamp a manifest. No schema
enforcement here — schema-on-read is the bronze contract.

Idempotency is by ``batch_id`` (content-addressable). Re-running the same
batch is a guaranteed no-op.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from aegis.datalake import constants as C
from aegis.datalake._logging import get_logger
from aegis.datalake.catalog import LakeCatalog, PartitionInfo
from aegis.datalake.schemas import IngestBatch, WriteManifest
from aegis.datalake.storage import StorageBackend
from aegis.datalake.storage.parquet import (
    plan_file_path,
    plan_manifest_path,
    write_batch,
)

_log = get_logger(__name__)


class BronzeWriter:
    """Write raw rows to the Bronze layer.

    The writer is stateless across calls — instantiate once per process
    and reuse. Each :meth:`write` call is a complete batch transaction.
    """

    def __init__(
        self,
        *,
        backend: StorageBackend,
        catalog: LakeCatalog,
        tenant_id: str = C.DEFAULT_TENANT_ID,
    ) -> None:
        self._backend = backend
        self._catalog = catalog
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ---- core API -------------------------------------------------------
    def write(
        self,
        *,
        table: str,
        rows: Iterable[Mapping[str, Any]],
        source: str,
        partition_date: str | datetime | None = None,
        tenant_id: str | None = None,
    ) -> WriteManifest:
        """Persist a single batch.

        Args:
            table: Logical table name (e.g. ``"signals"``).
            rows: Iterable of row dicts.
            source: Upstream label (e.g. ``"postgres.signals"``).
            partition_date: Date for the ``dt=`` partition. Defaults to UTC today.
            tenant_id: Override the default tenant.

        Returns:
            The :class:`WriteManifest` describing the write.
        """
        rows_tuple = tuple(dict(r) for r in rows)
        tenant = tenant_id or self._tenant_id
        partition_key = _date_to_partition_key(partition_date)

        batch = IngestBatch(
            table_name=table,
            layer=C.BRONZE,  # type: ignore[arg-type]
            tenant_id=tenant,
            partition_key=partition_key,
            source=source,
            rows=rows_tuple,
        )

        # Auto-register the table the first time it's written. Subsequent
        # writes skip silently thanks to ``if_exists="skip"`` semantics in
        # the catalog. Bronze is schema-on-read tolerant so we register
        # without a column-level schema_json.
        self._catalog.register_table(
            name=table,
            layer=C.BRONZE,
            schema_json="{}",
            description=f"Bronze table {table} (auto-registered by BronzeWriter)",
            if_exists="skip",
        )

        manifest = write_batch(
            backend=self._backend,
            batch=batch,
            source=source,
        )

        # Record partition in catalog
        file_path = plan_file_path(
            layer=C.BRONZE,
            table=table,
            partition_key=partition_key,
            tenant_id=tenant,
            batch_id=manifest.batch_id,
        )
        manifest_path = plan_manifest_path(
            layer=C.BRONZE,
            table=table,
            partition_key=partition_key,
            tenant_id=tenant,
            batch_id=manifest.batch_id,
        )
        self._catalog.record_partition(
            PartitionInfo(
                table_name=table,
                layer=C.BRONZE,
                partition_key=partition_key,
                tenant_id=tenant,
                batch_id=manifest.batch_id,
                file_path=file_path,
                manifest_path=manifest_path,
                row_count=manifest.row_count,
                byte_size=manifest.byte_size,
                sha256=manifest.sha256,
                written_at=manifest.written_at,
            )
        )

        _log.info(
            "bronze.write.ok",
            table=table,
            partition=partition_key,
            rows=manifest.row_count,
            bytes=manifest.byte_size,
        )
        return manifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _date_to_partition_key(value: str | datetime | None) -> str:
    """Coerce a date-ish value to Hive-style ``dt=YYYY-MM-DD`` (UTC)."""
    if value is None:
        d = datetime.now(UTC).date()
    elif isinstance(value, datetime):
        d = value.astimezone(UTC).date() if value.tzinfo else value.date()
    else:
        # string — accept either YYYY-MM-DD or full ISO
        try:
            d = datetime.fromisoformat(value).date()
        except ValueError:
            # last-ditch: try YYYY-MM-DD literal
            d = datetime.strptime(value, "%Y-%m-%d").date()
    return f"{C.TIME_PARTITION_KEY}={d.isoformat()}"


__all__ = ["BronzeWriter"]
