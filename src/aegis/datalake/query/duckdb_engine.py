"""DuckDB-backed query engine for the data lake.

Why DuckDB:
  - Zero infrastructure (in-process, no daemon).
  - Reads Parquet directly from S3 / MinIO / local FS via httpfs.
  - SQL-on-Parquet at sub-second latency at our scale.
  - Plays nicely with pandas / pyarrow when needed.

The engine builds a lightweight "view registry" over the catalog so
``SELECT * FROM gold.daily_platform_stats`` Just Works without the user
typing the partition glob themselves.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from aegis.datalake._logging import get_logger
from aegis.datalake.catalog import LakeCatalog
from aegis.datalake.errors import (
    MissingDependencyError,
    QueryError,
    QuerySyntaxError,
    QueryTimeoutError,
)
from aegis.datalake.settings import DataLakeSettings
from aegis.datalake.storage import StorageBackend

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class QueryResult:
    """Result of a query.

    ``columns`` and ``rows`` are aligned: ``rows[i][j]`` is the j-th column
    of the i-th row. ``rowcount`` is just ``len(rows)``.

    Wallclock duration is included for dashboard observability.
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    duration_ms: float
    query: str

    @property
    def rowcount(self) -> int:
        return len(self.rows)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class DuckDBQueryEngine:
    """Embedded DuckDB engine wired to the lake's storage backend.

    Engine is initialised lazily on first :meth:`execute` to keep
    instantiation cheap and import-safe.
    """

    # Allowlist of SQL keywords legal at the start of a query.
    # Defense-in-depth against an obvious class of misuse from the
    # dashboard ops console (matches CLAUDE.md's allowlist pattern).
    _ALLOWED_LEAD_KEYWORDS = frozenset(
        {"SELECT", "WITH", "EXPLAIN", "DESCRIBE", "SHOW", "PRAGMA"}
    )

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
        self._conn: Any | None = None
        self._lock = threading.RLock()
        self._registered_views: set[tuple[str, str]] = set()

    # ---- lifecycle ------------------------------------------------------
    def _ensure_conn(self) -> Any:
        if self._conn is not None:
            return self._conn
        try:
            import duckdb  # type: ignore[import-untyped]
        except ImportError as exc:
            raise MissingDependencyError(
                "duckdb is required for the query engine",
                hint="pip install duckdb>=1.0.0",
            ) from exc

        s = self._settings
        s.ensure_local_dirs()

        # In-memory database; we attach via parquet over S3/local FS
        conn = duckdb.connect(database=":memory:")
        conn.execute(f"PRAGMA threads={s.duckdb_thread_count}")
        conn.execute(f"PRAGMA memory_limit='{s.duckdb_memory_limit_mb}MB'")
        conn.execute(f"PRAGMA temp_directory='{s.duckdb_temp_dir}'")

        # Wire S3 if the backend is S3
        if self._backend.backend_name == "s3":
            conn.execute("INSTALL httpfs; LOAD httpfs;")
            endpoint = s.s3_endpoint
            host_for_duckdb = (
                endpoint.removeprefix("http://").removeprefix("https://").rstrip("/")
            )
            use_ssl = "true" if s.s3_use_ssl else "false"
            conn.execute(f"SET s3_region='{s.s3_region}'")
            conn.execute(f"SET s3_endpoint='{host_for_duckdb}'")
            conn.execute(f"SET s3_use_ssl={use_ssl}")
            conn.execute("SET s3_url_style='path'")  # MinIO uses path style
            conn.execute(
                f"SET s3_access_key_id='{s.s3_access_key.get_secret_value()}'"
            )
            conn.execute(
                f"SET s3_secret_access_key='{s.s3_secret_key.get_secret_value()}'"
            )

        self._conn = conn
        _log.info(
            "duckdb.ready",
            threads=s.duckdb_thread_count,
            memory_mb=s.duckdb_memory_limit_mb,
            backend=self._backend.backend_name,
        )
        return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                self._registered_views.clear()

    def __enter__(self) -> DuckDBQueryEngine:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ---- view registration ---------------------------------------------
    def register_table(self, *, name: str, layer: str) -> str:
        """Create a DuckDB VIEW over the table's Parquet files.

        Returns the view name (``{layer}_{name}``).
        """
        with self._lock:
            conn = self._ensure_conn()
            key = (layer, name)
            view_name = f"{layer}_{name}"
            if key in self._registered_views:
                return view_name
            # Validate the table exists in the catalog so we have a useful
            # error before the (potentially expensive) glob attempt.
            try:
                self._catalog.get_table(name, layer)
            except Exception as exc:
                raise QueryError(
                    f"cannot register view for unknown table {layer}.{name}: {exc}"
                ) from exc

            uri = self._table_glob_uri(layer=layer, table=name)
            conn.execute(
                f"CREATE OR REPLACE VIEW {view_name} AS "  # noqa: S608 — catalog-resolved names only
                f"SELECT * FROM read_parquet('{uri}', union_by_name=true, "
                f"hive_partitioning=true)"
            )
            self._registered_views.add(key)
            _log.info("duckdb.view.registered", view=view_name, uri=uri)
            return view_name

    def register_all_tables(self) -> list[str]:
        """Register a view for every table in the catalog."""
        registered: list[str] = []
        for t in self._catalog.list_tables():
            try:
                registered.append(self.register_table(name=t.name, layer=t.layer))
            except QueryError as exc:
                _log.warning(
                    "duckdb.view.register_failed",
                    table=t.name,
                    layer=t.layer,
                    error=str(exc),
                )
        return registered

    def _table_glob_uri(self, *, layer: str, table: str) -> str:
        """Return the recursive glob URI for a table's Parquet files."""
        relative = f"{layer}/{table}/**/*.parquet"
        return self._backend.uri_for(relative)

    # ---- query ---------------------------------------------------------
    def execute(
        self,
        sql: str,
        *,
        params: Mapping[str, Any] | tuple[Any, ...] | list[Any] | None = None,
        timeout_s: float | None = None,
    ) -> QueryResult:
        """Execute a read-only SQL statement.

        Args:
            sql: A SQL statement (SELECT / WITH / DESCRIBE / SHOW / PRAGMA).
            params: Optional parameters. Positional via tuple/list; named via dict.
            timeout_s: Wall-clock timeout. Default: settings.query_timeout_s.

        Returns:
            A :class:`QueryResult`.
        """
        sql = sql.strip().rstrip(";")
        self._enforce_read_only(sql)
        budget = timeout_s if timeout_s is not None else self._settings.query_timeout_s

        with self._lock:
            conn = self._ensure_conn()
            start = time.monotonic()
            try:
                if params is None:
                    cursor = conn.execute(sql)
                else:
                    cursor = conn.execute(sql, params)
            except Exception as exc:
                # Best-effort syntax-vs-other split
                msg = str(exc)
                if "syntax" in msg.lower() or "parse" in msg.lower():
                    raise QuerySyntaxError(msg) from exc
                raise QueryError(msg) from exc

            elapsed = time.monotonic() - start
            if elapsed > budget:
                raise QueryTimeoutError(
                    f"query exceeded {budget}s budget (took {elapsed:.2f}s)",
                    hint="narrow the date range or add filters",
                )

            cols = tuple(d[0] for d in (cursor.description or ()))
            rows_raw = cursor.fetchall()
            rows = tuple(tuple(r) for r in rows_raw)

        return QueryResult(
            columns=cols,
            rows=rows,
            duration_ms=elapsed * 1000.0,
            query=sql,
        )

    def explain(self, sql: str) -> str:
        """Return DuckDB's EXPLAIN plan."""
        result = self.execute(f"EXPLAIN {sql}")
        return "\n".join(" | ".join(str(c) for c in r) for r in result.rows)

    # ---- safety --------------------------------------------------------
    def _enforce_read_only(self, sql: str) -> None:
        """Reject statements that aren't in the allowlist."""
        match = re.match(r"^\s*(\w+)", sql, re.IGNORECASE)
        if not match:
            raise QuerySyntaxError("empty query")
        first = match.group(1).upper()
        if first not in self._ALLOWED_LEAD_KEYWORDS:
            raise QueryError(
                f"only read-only queries are permitted; got leading keyword {first!r}",
                hint=(
                    f"allowed: {sorted(self._ALLOWED_LEAD_KEYWORDS)}; "
                    "use BronzeWriter/SilverBuilder/GoldAggregator for writes"
                ),
            )


__all__ = ["DuckDBQueryEngine", "QueryResult"]
