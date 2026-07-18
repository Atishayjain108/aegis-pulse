"""High-level facade tying every Phase 10 component together.

The :class:`DataLake` class is the one-line setup most callers want. It wires:

* a storage backend (S3/MinIO or local filesystem),
* the SQLite-backed lake catalog,
* the Bronze/Silver/Gold builders, and
* a DuckDB query engine,

into a single object that is safe to share across a process.

Design notes
------------
* The facade is *purely a composer*. It owns no business logic. Every method
  delegates to the appropriate component, so behaviour stays deterministic and
  every transform is independently testable.
* Component construction is lazy where it costs something (DuckDB connection
  opens on first ``query()`` call). Cheap components (writer, builder,
  aggregator) are created eagerly because they hold no resources.
* The facade is *not* a god-object. Components remain public and can be
  swapped in tests. This matches the heuristic-first doctrine: nothing here
  is "magic"; every call site has a clear, reproducible counterpart.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ._logging import get_logger
from .bronze.writer import BronzeWriter
from .catalog.registry import LakeCatalog
from .constants import (
    DEFAULT_TENANT_ID,
    VALID_LAYERS,
)
from .errors import ConfigurationError
from .gold.aggregator import GoldAggregator
from .query.duckdb_engine import DuckDBQueryEngine, QueryResult
from .settings import DataLakeSettings
from .silver.builder import SilverBuilder
from .storage.backend import StorageBackend, build_backend

if TYPE_CHECKING:
    pass

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DataLakeHealth:
    """Lightweight self-check result for ops dashboards.

    Attributes
    ----------
    backend_ok:
        Storage backend responded to a list call without raising.
    catalog_ok:
        Catalog SQLite is reachable and the schema is at the expected version.
    tables_registered:
        Number of tables currently registered across all layers.
    storage_kind:
        ``"s3"`` or ``"local"`` — handy for runbooks.
    bucket:
        Configured bucket / local root name.
    """

    backend_ok: bool
    catalog_ok: bool
    tables_registered: int
    storage_kind: str
    bucket: str

    @property
    def healthy(self) -> bool:
        return self.backend_ok and self.catalog_ok


# --------------------------------------------------------------------------- #
# Facade
# --------------------------------------------------------------------------- #


class DataLake:
    """One-stop facade for the Phase 10 data lake.

    Parameters
    ----------
    settings:
        Resolved :class:`DataLakeSettings`. Build with ``DataLakeSettings()``
        for env-driven config, or pass a hand-crafted instance for tests
        (use ``use_local_filesystem=True`` to avoid needing MinIO).

    Examples
    --------
    >>> from aegis.datalake import DataLake
    >>> from aegis.datalake.settings import DataLakeSettings
    >>> lake = DataLake.open(DataLakeSettings(use_local_filesystem=True,
    ...                                       local_root="/tmp/aegis-lake",
    ...                                       catalog_db_path="/tmp/aegis-lake/catalog.db"))
    >>> manifest = lake.bronze.write(
    ...     table="signals", rows=[{"id": 1, "captured_at": "2026-05-20T00:00:00Z"}],
    ...     source="manual", partition_date="2026-05-20",
    ... )
    >>> lake.close()
    """

    # ----- construction ----- #

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
        self._tenant_id = settings.tenant_id or DEFAULT_TENANT_ID
        self._bronze = BronzeWriter(
            backend=backend, catalog=catalog, tenant_id=self._tenant_id
        )
        self._silver = SilverBuilder(
            backend=backend, catalog=catalog, tenant_id=self._tenant_id
        )
        self._gold = GoldAggregator(
            backend=backend, catalog=catalog, tenant_id=self._tenant_id
        )
        # DuckDB engine is created lazily — it opens a real connection.
        self._engine: DuckDBQueryEngine | None = None
        log.info(
            "datalake.facade.ready",
            storage=getattr(backend, "backend_name", "unknown"),
            bucket=settings.bucket,
            tenant=self._tenant_id,
        )

    @classmethod
    def open(cls, settings: DataLakeSettings | None = None) -> DataLake:
        """Build a :class:`DataLake` from settings (or env defaults)."""
        s = settings or DataLakeSettings()
        s.ensure_local_dirs()
        backend = build_backend(s)
        catalog = LakeCatalog(db_path=s.catalog_db_path)
        return cls(settings=s, backend=backend, catalog=catalog)

    @classmethod
    @contextmanager
    def session(cls, settings: DataLakeSettings | None = None) -> Iterator[DataLake]:
        """Context-manager variant ensuring ``close()`` is always called."""
        lake = cls.open(settings)
        try:
            yield lake
        finally:
            lake.close()

    # ----- exposed components ----- #

    @property
    def settings(self) -> DataLakeSettings:
        return self._settings

    @property
    def backend(self) -> StorageBackend:
        return self._backend

    @property
    def catalog(self) -> LakeCatalog:
        return self._catalog

    @property
    def bronze(self) -> BronzeWriter:
        return self._bronze

    @property
    def silver(self) -> SilverBuilder:
        return self._silver

    @property
    def gold(self) -> GoldAggregator:
        return self._gold

    @property
    def engine(self) -> DuckDBQueryEngine:
        """The DuckDB query engine. Created on first access."""
        if self._engine is None:
            self._engine = DuckDBQueryEngine(
                settings=self._settings,
                backend=self._backend,
                catalog=self._catalog,
            )
        return self._engine

    # ----- convenience operations ----- #

    def query(
        self,
        sql: str,
        *,
        params: Any | None = None,
        timeout_s: float | None = None,
        register_all: bool = True,
    ) -> QueryResult:
        """Execute a read-only SQL query against the lake.

        When ``register_all`` is true (default), every catalog-registered table
        is first registered as a DuckDB view, so users can reference tables by
        their logical names (e.g. ``SELECT * FROM signals``). For tight loops
        where the same engine is reused, set ``register_all=False`` after the
        first call.
        """
        engine = self.engine
        if register_all:
            engine.register_all_tables()
        return engine.execute(sql, params=params, timeout_s=timeout_s)

    def build_silver(self, date_iso: str) -> dict[str, Any]:
        """Run every Silver builder for a given UTC date."""
        from dataclasses import asdict as _asdict

        return {
            "signals": _asdict(self._silver.build_signals_for_date(date_iso=date_iso)),
            "predictions": _asdict(
                self._silver.build_predictions_for_date(date_iso=date_iso)
            ),
        }

    def build_gold(self, date_iso: str) -> dict[str, Any]:
        """Run every Gold aggregator for a given UTC date."""
        from dataclasses import asdict as _asdict

        return {
            "daily_platform_stats": _asdict(
                self._gold.build_daily_platform_stats(date_iso=date_iso)
            ),
            "trend_verdict_rollup": _asdict(
                self._gold.build_trend_verdict_rollup(date_iso=date_iso)
            ),
            "prediction_accuracy": _asdict(
                self._gold.build_prediction_accuracy(date_iso=date_iso)
            ),
        }

    def list_tables(self, layer: str | None = None) -> list[dict[str, Any]]:
        """List every catalog-registered table, optionally filtered by layer."""
        if layer is not None and layer not in VALID_LAYERS:
            raise ConfigurationError(
                f"unknown layer {layer!r}; expected one of {sorted(VALID_LAYERS)}"
            )
        return [
            {
                "name": t.name,
                "layer": t.layer,
                "description": t.description,
                "created_at": t.created_at,
                "partition_keys": list(t.partition_keys),
            }
            for t in self._catalog.list_tables(layer=layer)
        ]

    def health(self) -> DataLakeHealth:
        """Run a cheap end-to-end self-check."""
        backend_ok = True
        try:
            # list_prefix is cheap and exists on every backend
            list(self._backend.list_prefix(""))
        except Exception:
            backend_ok = False

        catalog_ok = True
        tables_registered = 0
        try:
            tables_registered = len(self._catalog.list_tables())
        except Exception:
            catalog_ok = False

        return DataLakeHealth(
            backend_ok=backend_ok,
            catalog_ok=catalog_ok,
            tables_registered=tables_registered,
            storage_kind=getattr(self._backend, "backend_name", "unknown"),
            bucket=self._settings.bucket,
        )

    # ----- lifecycle ----- #

    def close(self) -> None:
        """Release all held resources. Safe to call multiple times."""
        if self._engine is not None:
            try:
                self._engine.close()
            except Exception:
                log.warning("datalake.facade.close.engine_failed", exc_info=True)
            self._engine = None
        try:
            self._catalog.close()
        except Exception:
            log.warning("datalake.facade.close.catalog_failed", exc_info=True)
        log.info("datalake.facade.closed")

    def __enter__(self) -> DataLake:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


# Re-export for ergonomics.
__all__ = ["DataLake", "DataLakeHealth"]
