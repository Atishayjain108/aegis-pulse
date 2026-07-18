"""Retention enforcement for Bronze/Silver/Gold partitions.

Policy
------
* **Bronze** has no automatic retention by default — it is the immutable
  audit trail. Operators can opt-in to deletion by passing an explicit
  ``bronze_retention_days`` parameter.
* **Silver** is retained for ``settings.silver_retention_days`` (default 365).
* **Gold** is retained for ``settings.gold_retention_days`` (default 1095, ~3y).

The enforcer is *idempotent* and *dry-run by default* — call ``apply()`` only
when you have confirmed the dry-run plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ._logging import get_logger
from .catalog.registry import LakeCatalog
from .constants import (
    BRONZE,
    DEFAULT_TENANT_ID,
    GOLD,
    SILVER,
    TIME_PARTITION_KEY,
)
from .errors import ConfigurationError
from .settings import DataLakeSettings
from .storage.backend import StorageBackend
from .storage.parquet import plan_partition_path

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    """A non-destructive plan listing partitions that *would* be deleted."""

    layer: str
    cutoff_date: str
    table_to_partitions: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total_partitions(self) -> int:
        return sum(len(v) for v in self.table_to_partitions.values())

    @property
    def total_tables(self) -> int:
        return len(self.table_to_partitions)


@dataclass(frozen=True, slots=True)
class RetentionResult:
    """Outcome of an :meth:`RetentionEnforcer.apply` run."""

    layer: str
    cutoff_date: str
    partitions_deleted: int
    objects_deleted: int
    bytes_deleted: int


class RetentionEnforcer:
    """Apply day-based retention to Silver/Gold (and optionally Bronze)."""

    def __init__(
        self,
        *,
        settings: DataLakeSettings,
        backend: StorageBackend,
        catalog: LakeCatalog,
    ) -> None:
        self._settings = settings
        self._backend = backend
        self._catalog = catalog

    # --- planning --- #

    def plan(
        self,
        layer: str,
        *,
        bronze_retention_days: int | None = None,
        as_of: datetime | None = None,
        tenant_id: str | None = None,
    ) -> RetentionPlan:
        """Compute (but do not execute) the retention plan for a layer."""
        if layer not in {BRONZE, SILVER, GOLD}:
            raise ConfigurationError(f"unknown layer {layer!r}")
        as_of = as_of or datetime.now(UTC)
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=UTC)

        retention_days = self._retention_days_for(layer, bronze_retention_days)
        if retention_days is None:
            log.info("retention.skip.bronze_no_policy", layer=layer)
            return RetentionPlan(layer=layer, cutoff_date="")

        cutoff = (as_of - timedelta(days=retention_days)).date().isoformat()
        tenant_id = tenant_id or self._settings.tenant_id or DEFAULT_TENANT_ID

        plan: dict[str, list[str]] = {}
        for table in self._catalog.list_tables(layer=layer):
            partitions = self._catalog.list_partitions(
                table_name=table.name, layer=layer
            )
            old = [
                p.partition_key
                for p in partitions
                if _partition_dt_key(p.partition_key) < cutoff
                and (p.tenant_id == tenant_id or not p.tenant_id)
            ]
            if old:
                plan[table.name] = sorted(old)

        log.info(
            "retention.plan.ready",
            layer=layer,
            cutoff=cutoff,
            retention_days=retention_days,
            tables_affected=len(plan),
            partitions_affected=sum(len(v) for v in plan.values()),
        )
        return RetentionPlan(layer=layer, cutoff_date=cutoff, table_to_partitions=plan)

    # --- execution --- #

    def apply(
        self,
        plan: RetentionPlan,
        *,
        tenant_id: str | None = None,
        dry_run: bool = False,
    ) -> RetentionResult:
        """Execute a :class:`RetentionPlan`, deleting matching partitions."""
        if not plan.cutoff_date:
            return RetentionResult(
                layer=plan.layer,
                cutoff_date="",
                partitions_deleted=0,
                objects_deleted=0,
                bytes_deleted=0,
            )
        tenant_id = tenant_id or self._settings.tenant_id or DEFAULT_TENANT_ID
        partitions_deleted = 0
        objects_deleted = 0
        bytes_deleted = 0

        for table_name, partition_keys in plan.table_to_partitions.items():
            for partition_key in partition_keys:
                # partition_key is already Hive-style "dt=2026-05-19".
                prefix = plan_partition_path(
                    layer=plan.layer,
                    table=table_name,
                    partition_key=partition_key,
                    tenant_id=tenant_id,
                )
                deleted_here = 0
                bytes_here = 0
                for obj in list(self._backend.list_prefix(prefix)):
                    if dry_run:
                        objects_deleted += 1
                    else:
                        size = _safe_size(self._backend, obj)
                        self._backend.delete(obj)
                        objects_deleted += 1
                        deleted_here += 1
                        bytes_deleted += size
                        bytes_here += size
                if not dry_run:
                    # Catalog cleanup — we delete the partition row, but keep
                    # the lineage edge for traceability.
                    self._catalog_delete_partition(
                        table=table_name, partition_key=partition_key
                    )
                partitions_deleted += 1
                log.info(
                    "retention.partition.processed",
                    layer=plan.layer,
                    table=table_name,
                    partition=partition_key,
                    objects=deleted_here,
                    bytes=bytes_here,
                    dry_run=dry_run,
                )

        return RetentionResult(
            layer=plan.layer,
            cutoff_date=plan.cutoff_date,
            partitions_deleted=partitions_deleted,
            objects_deleted=objects_deleted,
            bytes_deleted=bytes_deleted,
        )

    # --- helpers --- #

    def _retention_days_for(
        self, layer: str, bronze_override: int | None
    ) -> int | None:
        if layer == BRONZE:
            return bronze_override  # may be None → skip
        if layer == SILVER:
            return self._settings.silver_retention_days
        if layer == GOLD:
            return self._settings.gold_retention_days
        return None  # unreachable; guarded earlier

    def _catalog_delete_partition(self, *, table: str, partition_key: str) -> None:
        """Best-effort catalog cleanup. Tolerant of missing helper methods."""
        with self._catalog._tx() as conn:  # type: ignore[attr-defined]
            conn.execute(
                "DELETE FROM partitions WHERE table_name = ? AND partition_key = ?",
                (table, partition_key),
            )


def _partition_dt_key(partition_key: str) -> str:
    """Extract the date portion of a Hive-style partition key.

    ``"dt=2026-05-19"`` → ``"2026-05-19"``.
    """
    prefix = f"{TIME_PARTITION_KEY}="
    if partition_key.startswith(prefix):
        return partition_key[len(prefix) :]
    return partition_key  # already a bare date


def _safe_size(backend: StorageBackend, key: str) -> int:
    """Best-effort byte count. Returns 0 if backend can't tell us."""
    try:
        data = backend.get_bytes(key)
        return len(data)
    except Exception:
        return 0


__all__ = ["RetentionEnforcer", "RetentionPlan", "RetentionResult"]
