"""FastAPI router exposing read-only Data Lake endpoints.

Endpoints
---------

``GET /datalake/health``
    Storage + catalog reachability + table count.
``GET /datalake/tables``
    List registered tables. Optional ``layer`` query parameter.
``GET /datalake/tables/{name}/partitions``
    List partitions for a single table.
``POST /datalake/query``
    Execute a read-only SQL query. Statement-type allowlist is enforced
    by the DuckDB engine; a per-request timeout is enforced here.
``GET /datalake/lineage``
    Inspect lineage edges recorded by Silver/Gold builders.

Security
--------
The router is **mounted behind whatever auth the host app uses** (Phase 4
already protects ``/`` paths with the same HMAC scheme). The router itself
adds **no auth**, deliberately — host apps must wrap it. Statement-type
gating happens at the DuckDB layer; we also reject queries longer than
:const:`MAX_QUERY_CHARS` to avoid pathological inputs.
"""

from __future__ import annotations

import time
from typing import Any

from .._logging import get_logger
from ..constants import VALID_LAYERS
from ..errors import (
    ConfigurationError,
    QueryError,
    QueryTimeoutError,
    TableNotFoundError,
)
from ..facade import DataLake
from ..settings import DataLakeSettings

log = get_logger(__name__)

#: Max characters in a query body — defense in depth alongside the DuckDB
#: statement-type allowlist.
MAX_QUERY_CHARS = 8_192

#: Max rows returned to a single API call.
MAX_API_ROWS = 5_000


# Module-level Pydantic models (must NOT be defined inside a function for
# FastAPI body parsing to identify them correctly).
try:  # pragma: no cover - guarded for headless installs
    from pydantic import BaseModel, Field, field_validator

    class QueryRequest(BaseModel):
        """Payload for ``POST /datalake/query``."""

        sql: str = Field(..., min_length=1, max_length=MAX_QUERY_CHARS)
        timeout_s: float | None = Field(default=None, ge=0.1, le=60.0)
        limit: int = Field(default=1_000, ge=1, le=MAX_API_ROWS)

    class QueryResponse(BaseModel):
        columns: list[str]
        rowcount: int
        duration_ms: float
        rows: list[dict[str, Any]]

    class TableModel(BaseModel):
        name: str
        layer: str
        description: str
        created_at: str
        partition_keys: list[str]

        @field_validator("created_at", mode="before")
        @classmethod
        def _coerce_created_at(cls, v: Any) -> str:
            # The catalog returns created_at as a datetime; coerce to ISO-8601.
            if hasattr(v, "isoformat"):
                return v.isoformat()
            return str(v)

    class PartitionModel(BaseModel):
        table_name: str
        layer: str
        partition_key: str
        tenant_id: str
        row_count: int
        byte_size: int
        written_at: str

    class HealthModel(BaseModel):
        backend_ok: bool
        catalog_ok: bool
        tables_registered: int
        storage_kind: str
        bucket: str

    class LineageEdgeModel(BaseModel):
        upstream_table: str
        upstream_layer: str
        downstream_table: str
        downstream_layer: str
        transform: str

    _PYDANTIC_OK = True
except ImportError:  # pragma: no cover
    _PYDANTIC_OK = False


def build_router(
    *,
    settings: DataLakeSettings | None = None,
    lake: DataLake | None = None,
) -> Any:
    """Build a FastAPI :class:`APIRouter` for the Data Lake.

    Parameters
    ----------
    settings:
        If provided, a new :class:`DataLake` is opened per request from these
        settings (suitable for tests).
    lake:
        If provided, this single :class:`DataLake` instance is reused across
        requests (suitable for production — open once at app startup).

    Exactly one of ``settings`` / ``lake`` must be given.
    """
    try:
        from fastapi import APIRouter, HTTPException, Query
    except ImportError as exc:  # pragma: no cover
        raise ConfigurationError(
            "fastapi is not installed; `pip install fastapi` to use the API"
        ) from exc

    if (settings is None) == (lake is None):
        raise ConfigurationError(
            "build_router() requires exactly one of settings or lake"
        )

    router = APIRouter(prefix="/datalake", tags=["datalake"])

    def _lake_ctx():
        """Return a context manager yielding a DataLake."""
        if lake is not None:
            class _Passthrough:
                def __enter__(self) -> DataLake:
                    return lake  # type: ignore[return-value]

                def __exit__(self, *args: Any) -> None:
                    return None

            return _Passthrough()
        return DataLake.session(settings)

    @router.get("/health", response_model=HealthModel)
    def health_endpoint() -> Any:
        with _lake_ctx() as lk:
            h = lk.health()
            return HealthModel(
                backend_ok=h.backend_ok,
                catalog_ok=h.catalog_ok,
                tables_registered=h.tables_registered,
                storage_kind=h.storage_kind,
                bucket=h.bucket,
            )

    @router.get("/tables", response_model=list[TableModel])
    def list_tables_endpoint(layer: str | None = Query(default=None)) -> Any:
        if layer is not None and layer not in VALID_LAYERS:
            raise HTTPException(
                status_code=400,
                detail=f"unknown layer {layer!r}; expected one of {sorted(VALID_LAYERS)}",
            )
        with _lake_ctx() as lk:
            return [TableModel(**t) for t in lk.list_tables(layer=layer)]

    @router.get(
        "/tables/{table_name}/partitions",
        response_model=list[PartitionModel],
    )
    def list_partitions_endpoint(
        table_name: str, layer: str = Query(...)
    ) -> Any:
        if layer not in VALID_LAYERS:
            raise HTTPException(
                status_code=400, detail=f"unknown layer {layer!r}"
            )
        with _lake_ctx() as lk:
            try:
                parts = lk.catalog.list_partitions(
                    table_name=table_name, layer=layer
                )
            except TableNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return [
                PartitionModel(
                    table_name=p.table_name,
                    layer=p.layer,
                    partition_key=p.partition_key,
                    tenant_id=p.tenant_id,
                    row_count=p.row_count,
                    byte_size=p.byte_size,
                    written_at=p.written_at.isoformat(),
                )
                for p in parts
            ]

    @router.get("/lineage", response_model=list[LineageEdgeModel])
    def lineage_endpoint(
        downstream_table: str | None = Query(default=None),
    ) -> Any:
        with _lake_ctx() as lk:
            edges = lk.catalog.list_lineage(downstream_table=downstream_table)
            return [
                LineageEdgeModel(
                    upstream_table=e.upstream_table,
                    upstream_layer=e.upstream_layer,
                    downstream_table=e.downstream_table,
                    downstream_layer=e.downstream_layer,
                    transform=e.transform,
                )
                for e in edges
            ]

    @router.post("/query", response_model=QueryResponse)
    def query_endpoint(req: QueryRequest) -> Any:
        started = time.perf_counter()
        try:
            with _lake_ctx() as lk:
                result = lk.query(req.sql, timeout_s=req.timeout_s)
                rows = result.to_dicts()[: req.limit]
                duration_ms = (time.perf_counter() - started) * 1000.0
                return QueryResponse(
                    columns=list(result.columns),
                    rowcount=result.rowcount,
                    duration_ms=duration_ms,
                    rows=rows,
                )
        except QueryTimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except QueryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            log.exception("datalake.api.query.unexpected", error=str(exc))
            raise HTTPException(status_code=500, detail="internal query error") from exc

    return router


__all__ = ["MAX_API_ROWS", "MAX_QUERY_CHARS", "QueryRequest", "build_router"]
