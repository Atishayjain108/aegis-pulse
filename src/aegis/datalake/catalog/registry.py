"""Lightweight table catalog backed by SQLite.

We don't need Hive Metastore at AEGIS-laptop scale: a single SQLite file
co-located with the developer's home dir tracks (a) registered tables,
(b) per-partition manifests, and (c) lineage edges between layers.

The catalog is *read-mostly* — a write happens at the end of every
bronze/silver/gold batch. Concurrent writes are serialised via SQLite's
default journal mode (WAL).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from aegis.datalake import constants as C
from aegis.datalake._logging import get_logger
from aegis.datalake.errors import (
    SchemaMismatchError,
    TableAlreadyExistsError,
    TableNotFoundError,
)

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses (catalog records — value objects)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TableInfo:
    """Metadata about a registered table."""

    name: str
    layer: Literal["bronze", "silver", "gold"]
    schema_json: str  # serialised pydantic schema / JSON schema
    description: str
    created_at: datetime
    partition_keys: tuple[str, ...]  # e.g. ("dt", "tenant_id")


@dataclass(frozen=True, slots=True)
class PartitionInfo:
    """Records a single partition write."""

    table_name: str
    layer: str
    partition_key: str  # e.g. "dt=2026-05-20"
    tenant_id: str
    batch_id: str
    file_path: str
    manifest_path: str
    row_count: int
    byte_size: int
    sha256: str
    written_at: datetime


@dataclass(frozen=True, slots=True)
class LineageEdge:
    """Records the dependency between two tables (silver depends on bronze, etc)."""

    upstream_table: str
    upstream_layer: str
    downstream_table: str
    downstream_layer: str
    transform: str  # human-readable transform id


# ---------------------------------------------------------------------------
# SCHEMA
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS tables (
    name             TEXT NOT NULL,
    layer            TEXT NOT NULL,
    schema_json      TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    partition_keys   TEXT NOT NULL,  -- JSON array
    PRIMARY KEY (name, layer)
);

CREATE TABLE IF NOT EXISTS partitions (
    table_name       TEXT NOT NULL,
    layer            TEXT NOT NULL,
    partition_key    TEXT NOT NULL,
    tenant_id        TEXT NOT NULL,
    batch_id         TEXT NOT NULL,
    file_path        TEXT NOT NULL,
    manifest_path    TEXT NOT NULL,
    row_count        INTEGER NOT NULL,
    byte_size        INTEGER NOT NULL,
    sha256           TEXT NOT NULL,
    written_at       TEXT NOT NULL,
    PRIMARY KEY (table_name, layer, partition_key, tenant_id, batch_id)
);

CREATE INDEX IF NOT EXISTS partitions_by_table
    ON partitions(table_name, layer);
CREATE INDEX IF NOT EXISTS partitions_by_time
    ON partitions(written_at);

CREATE TABLE IF NOT EXISTS lineage (
    upstream_table     TEXT NOT NULL,
    upstream_layer     TEXT NOT NULL,
    downstream_table   TEXT NOT NULL,
    downstream_layer   TEXT NOT NULL,
    transform          TEXT NOT NULL,
    recorded_at        TEXT NOT NULL,
    PRIMARY KEY (upstream_table, upstream_layer, downstream_table, downstream_layer, transform)
);
"""


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
class LakeCatalog:
    """Catalog of tables, partitions, and lineage edges.

    All methods are thread-safe (an internal RLock serialises writes).
    Connection is opened lazily on first use; the file is auto-created.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # ---- connection management ------------------------------------------
    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                isolation_level=None,  # autocommit
                timeout=15.0,
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            self._conn = conn
            self._init_schema(conn)
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> LakeCatalog:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA_SQL)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO schema_version(version) VALUES (?)",
                (C.CATALOG_SCHEMA_VERSION,),
            )
        elif row["version"] != C.CATALOG_SCHEMA_VERSION:  # pragma: no cover
            raise SchemaMismatchError(
                f"catalog schema_version={row['version']} but code expects "
                f"{C.CATALOG_SCHEMA_VERSION}",
                hint="run `aegis datalake migrate` to upgrade",
            )

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Reentrant write transaction."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    # ---- table management ------------------------------------------------
    def register_table(
        self,
        *,
        name: str,
        layer: str,
        schema_json: str,
        description: str = "",
        partition_keys: tuple[str, ...] = (C.TIME_PARTITION_KEY, C.TENANT_PARTITION_KEY),
        if_exists: Literal["error", "skip", "replace"] = "skip",
    ) -> TableInfo:
        """Register (or no-op) a table in the catalog.

        Args:
            name: Logical table name (e.g. ``"signals"``).
            layer: ``"bronze"`` | ``"silver"`` | ``"gold"``.
            schema_json: A pydantic ``model_json_schema()`` string.
            description: Human-readable description.
            partition_keys: Partitioning columns, in order.
            if_exists: ``"error"`` raises, ``"skip"`` keeps existing,
                ``"replace"`` overwrites.
        """
        if layer not in C.VALID_LAYERS:
            raise ValueError(f"invalid layer: {layer!r}")
        now = datetime.now(UTC).isoformat()
        partition_keys_json = json.dumps(list(partition_keys))

        with self._tx() as conn:
            existing = conn.execute(
                "SELECT * FROM tables WHERE name=? AND layer=?", (name, layer)
            ).fetchone()
            if existing is not None:
                if if_exists == "error":
                    raise TableAlreadyExistsError(
                        f"table {name!r} already registered in layer {layer!r}"
                    )
                if if_exists == "skip":
                    return _row_to_table_info(existing)
                # replace
                conn.execute(
                    "DELETE FROM tables WHERE name=? AND layer=?", (name, layer)
                )

            conn.execute(
                """
                INSERT INTO tables
                  (name, layer, schema_json, description, created_at, partition_keys)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, layer, schema_json, description, now, partition_keys_json),
            )

        _log.info(
            "catalog.table.register", table=name, layer=layer, if_exists=if_exists
        )
        return TableInfo(
            name=name,
            layer=layer,  # type: ignore[arg-type]
            schema_json=schema_json,
            description=description,
            created_at=datetime.fromisoformat(now),
            partition_keys=partition_keys,
        )

    def get_table(self, name: str, layer: str) -> TableInfo:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM tables WHERE name=? AND layer=?", (name, layer)
        ).fetchone()
        if row is None:
            raise TableNotFoundError(
                f"no table {name!r} in layer {layer!r}",
                hint="call register_table() first",
            )
        return _row_to_table_info(row)

    def list_tables(self, layer: str | None = None) -> list[TableInfo]:
        conn = self._get_conn()
        if layer is not None:
            rows = conn.execute(
                "SELECT * FROM tables WHERE layer=? ORDER BY name", (layer,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tables ORDER BY layer, name").fetchall()
        return [_row_to_table_info(r) for r in rows]

    def drop_table(self, name: str, layer: str, *, drop_partitions: bool = True) -> bool:
        with self._tx() as conn:
            result = conn.execute(
                "DELETE FROM tables WHERE name=? AND layer=?", (name, layer)
            )
            deleted = result.rowcount > 0
            if drop_partitions:
                conn.execute(
                    "DELETE FROM partitions WHERE table_name=? AND layer=?",
                    (name, layer),
                )
        return deleted

    # ---- partition tracking ---------------------------------------------
    def record_partition(self, info: PartitionInfo) -> None:
        """Insert (or replace) a partition record."""
        with self._tx() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO partitions
                  (table_name, layer, partition_key, tenant_id, batch_id,
                   file_path, manifest_path, row_count, byte_size, sha256, written_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    info.table_name,
                    info.layer,
                    info.partition_key,
                    info.tenant_id,
                    info.batch_id,
                    info.file_path,
                    info.manifest_path,
                    info.row_count,
                    info.byte_size,
                    info.sha256,
                    info.written_at.astimezone(UTC).isoformat(),
                ),
            )

    def list_partitions(
        self,
        *,
        table_name: str,
        layer: str,
        tenant_id: str | None = None,
        partition_key: str | None = None,
    ) -> list[PartitionInfo]:
        clauses = ["table_name=?", "layer=?"]
        params: list[Any] = [table_name, layer]
        if tenant_id is not None:
            clauses.append("tenant_id=?")
            params.append(tenant_id)
        if partition_key is not None:
            clauses.append("partition_key=?")
            params.append(partition_key)
        query = (
            "SELECT * FROM partitions WHERE "  # noqa: S608 — clauses built from safe predefined strings
            + " AND ".join(clauses)
            + " ORDER BY written_at"
        )
        conn = self._get_conn()
        rows = conn.execute(query, params).fetchall()
        return [_row_to_partition_info(r) for r in rows]

    def partition_count(self, *, table_name: str, layer: str) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM partitions WHERE table_name=? AND layer=?",
            (table_name, layer),
        ).fetchone()
        return int(row["n"]) if row else 0

    def total_rows(self, *, table_name: str, layer: str) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COALESCE(SUM(row_count), 0) AS s "
            "FROM partitions WHERE table_name=? AND layer=?",
            (table_name, layer),
        ).fetchone()
        return int(row["s"]) if row else 0

    # ---- lineage --------------------------------------------------------
    def record_lineage(self, edge: LineageEdge) -> None:
        now = datetime.now(UTC).isoformat()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO lineage
                  (upstream_table, upstream_layer, downstream_table, downstream_layer,
                   transform, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    edge.upstream_table,
                    edge.upstream_layer,
                    edge.downstream_table,
                    edge.downstream_layer,
                    edge.transform,
                    now,
                ),
            )

    def list_lineage(
        self, *, downstream_table: str | None = None
    ) -> list[LineageEdge]:
        conn = self._get_conn()
        if downstream_table is not None:
            rows = conn.execute(
                "SELECT * FROM lineage WHERE downstream_table=?",
                (downstream_table,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM lineage").fetchall()
        return [_row_to_lineage(r) for r in rows]


# ---------------------------------------------------------------------------
# Row → dataclass helpers
# ---------------------------------------------------------------------------
def _row_to_table_info(row: sqlite3.Row) -> TableInfo:
    return TableInfo(
        name=row["name"],
        layer=row["layer"],
        schema_json=row["schema_json"],
        description=row["description"] or "",
        created_at=datetime.fromisoformat(row["created_at"]),
        partition_keys=tuple(json.loads(row["partition_keys"])),
    )


def _row_to_partition_info(row: sqlite3.Row) -> PartitionInfo:
    return PartitionInfo(
        table_name=row["table_name"],
        layer=row["layer"],
        partition_key=row["partition_key"],
        tenant_id=row["tenant_id"],
        batch_id=row["batch_id"],
        file_path=row["file_path"],
        manifest_path=row["manifest_path"],
        row_count=int(row["row_count"]),
        byte_size=int(row["byte_size"]),
        sha256=row["sha256"],
        written_at=datetime.fromisoformat(row["written_at"]),
    )


def _row_to_lineage(row: sqlite3.Row) -> LineageEdge:
    return LineageEdge(
        upstream_table=row["upstream_table"],
        upstream_layer=row["upstream_layer"],
        downstream_table=row["downstream_table"],
        downstream_layer=row["downstream_layer"],
        transform=row["transform"],
    )


__all__ = [
    "LakeCatalog",
    "LineageEdge",
    "PartitionInfo",
    "TableInfo",
]
