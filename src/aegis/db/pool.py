"""Async Postgres connection pool.

One module-level ``PgPool`` class wraps ``asyncpg.create_pool`` with everything
the rest of the system needs:

- **Tenant context**: every checkout runs ``SET app.current_tenant = $1`` so
  Row-Level Security policies apply transparently.
- **Statement + command timeouts**: both the Postgres-side ``statement_timeout``
  and the client-side ``command_timeout`` are configured from ``constants``.
- **Metrics**: pool size + in-use are published as gauges; query duration is
  a histogram. All scoped so the default metric registry stays clean in tests.
- **Health check**: ``pool.health()`` issues ``SELECT 1`` on a fresh checkout
  and tests end-to-end reachability + auth.
- **Graceful shutdown**: ``aclose()`` waits for in-flight queries up to a
  configurable deadline before force-closing the pool.
- **JSON codecs**: ``jsonb`` columns round-trip through ``orjson`` for speed
  and stable ordering (important for deterministic content hashes downstream).

What this module deliberately does NOT do:

- ORM mapping. We write SQL. If you need joins across 5 tables, write SQL.
- Migrations. That's Alembic's job; see ``alembic/env.py``.
- Read-replica routing. Phase 15 adds that on top of ``PgPool``.

Author: AEGIS Pulse Team
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Self, TypeVar
from uuid import UUID

import asyncpg
import orjson

from aegis.constants import (
    DB_COMMAND_TIMEOUT_SECONDS,
    DB_POOL_MAX_SIZE,
    DB_POOL_MIN_SIZE,
    DB_STATEMENT_TIMEOUT_MS,
)
from aegis.core.logging import get_logger
from aegis.core.metrics import (
    db_pool_in_use,
    db_pool_size,
    db_query_duration_seconds,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable

log = get_logger(__name__)

_T = TypeVar("_T")  # generic for _timed()

# The single tenant used in v1 deployments — mirrors the INSERT in 0001_init.sql.
DEFAULT_TENANT_ID: Final[UUID] = UUID("00000000-0000-0000-0000-000000000001")


# =============================================================================
# Configuration
# =============================================================================


@dataclass(frozen=True, slots=True)
class PgConfig:
    """Declarative pool configuration.

    Use ``PgConfig.from_env()`` for the common case; pass explicit args only
    in tests (``testcontainers``) where the URL is synthesised at runtime.
    """

    dsn: str
    """PostgreSQL connection string. Must include user/password/host/db."""

    min_size: int = DB_POOL_MIN_SIZE
    max_size: int = DB_POOL_MAX_SIZE
    command_timeout: float = DB_COMMAND_TIMEOUT_SECONDS
    statement_timeout_ms: int = DB_STATEMENT_TIMEOUT_MS
    application_name: str = "aegis-pulse"
    default_tenant: UUID = DEFAULT_TENANT_ID

    # How long to wait for in-flight queries on aclose() before force-closing.
    shutdown_grace_seconds: float = 5.0

    @classmethod
    def from_env(cls, env: dict[str, str], **overrides: Any) -> Self:
        """Build a config from an env dict. Accepts individual parts OR a full DSN.

        Recognised keys (in order of precedence):

        - ``AEGIS_PG_DSN``
        - ``POSTGRES_HOST`` + ``POSTGRES_PORT`` + ``POSTGRES_USER`` + ``POSTGRES_PASSWORD`` + ``POSTGRES_DB``
        """
        dsn = env.get("AEGIS_PG_DSN")
        if not dsn:
            host = env.get("POSTGRES_HOST", "localhost")
            port = env.get("POSTGRES_PORT", "5432")
            user = env.get("POSTGRES_USER", "aegis")
            password = env.get("POSTGRES_PASSWORD", "")
            db = env.get("POSTGRES_DB", "aegis")
            dsn = f"postgresql://{user}:{password}@{host}:{port}/{db}"
        kwargs: dict[str, Any] = {"dsn": dsn, **overrides}
        return cls(**kwargs)


# =============================================================================
# The pool
# =============================================================================


class PgPool:
    """A lazy-initialised asyncpg pool wrapper.

    ``PgPool`` instances are constructed cheaply; the actual connection pool
    is only created when ``connect()`` is called. This lets DI / test fixtures
    create the object and inject it places without performing network IO.
    """

    def __init__(self, config: PgConfig | None = None, *, dsn: str | None = None) -> None:
        if config is None:
            if dsn is None:
                raise ValueError("PgPool requires either a PgConfig or dsn=")
            config = PgConfig(dsn=dsn)
        self._config: PgConfig = config
        self._pool: asyncpg.Pool | None = None
        self._connect_lock: asyncio.Lock = asyncio.Lock()
        self._shutting_down: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the pool. Idempotent."""
        if self._pool is not None:
            return
        async with self._connect_lock:
            # Re-check inside the lock — another coroutine may have already
            # initialised the pool while we were waiting. Mypy's narrowing
            # cannot see across the lock acquire, so we re-read via getattr
            # to defeat the false-positive "unreachable" warning.
            if getattr(self, "_pool", None) is not None:
                return
            log.info(
                "pgpool.connecting",
                min_size=self._config.min_size,
                max_size=self._config.max_size,
                app=self._config.application_name,
            )
            self._pool = await asyncpg.create_pool(
                self._config.dsn,
                min_size=self._config.min_size,
                max_size=self._config.max_size,
                command_timeout=self._config.command_timeout,
                init=self._init_connection,
                server_settings={
                    "application_name": self._config.application_name,
                    # statement_timeout is in milliseconds as a string.
                    "statement_timeout": str(self._config.statement_timeout_ms),
                    # Kill pathological idle-in-transaction sessions; 10 min.
                    "idle_in_transaction_session_timeout": "600000",
                    # Use UTC everywhere.
                    "timezone": "UTC",
                },
            )
            db_pool_size.set(self._config.max_size)
            log.info("pgpool.connected")

    async def aclose(self) -> None:
        """Close the pool gracefully.

        Waits up to ``shutdown_grace_seconds`` for in-flight queries to
        complete, then force-closes.
        """
        if self._pool is None:
            return
        self._shutting_down = True
        log.info("pgpool.closing", grace_seconds=self._config.shutdown_grace_seconds)
        try:
            await asyncio.wait_for(
                self._pool.close(),
                timeout=self._config.shutdown_grace_seconds,
            )
        except TimeoutError:
            log.warning("pgpool.force_terminate")
            self._pool.terminate()
        finally:
            self._pool = None
            db_pool_size.set(0)
            db_pool_in_use.set(0)
            log.info("pgpool.closed")

    # Convenience aliases used by CLI / tests.
    start = connect
    close = aclose

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Connection init hook — runs once per physical connection
    # ------------------------------------------------------------------

    async def _init_connection(self, conn: asyncpg.Connection) -> None:
        """Run per-connection setup: JSON codecs, pgvector registration, default tenant."""
        # Register orjson as the jsonb codec — ~2× faster than stdlib json,
        # and sorts keys deterministically.
        await conn.set_type_codec(
            "jsonb",
            schema="pg_catalog",
            encoder=lambda value: orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode(),
            decoder=orjson.loads,
            format="text",
        )
        await conn.set_type_codec(
            "json",
            schema="pg_catalog",
            encoder=lambda value: orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode(),
            decoder=orjson.loads,
            format="text",
        )
        # Apply default tenant here so simple checkouts already have RLS context.
        # Code paths that need a different tenant override via acquire(tenant_id=...).
        await conn.execute(
            "SELECT set_config('app.current_tenant', $1, false)",
            str(self._config.default_tenant),
        )

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    @property
    def pool(self) -> asyncpg.Pool:
        """Access the underlying pool. Raises if not yet connected."""
        if self._pool is None:
            raise RuntimeError("PgPool is not connected; call await pool.connect() first")
        return self._pool

    @asynccontextmanager
    async def acquire(
        self, *, tenant_id: UUID | None = None,
    ) -> AsyncIterator[asyncpg.Connection]:
        """Check out a connection. Optional tenant override.

        Usage::

            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT ...")

        Sets ``app.current_tenant`` on the connection for the duration of the
        checkout; resets to the pool-default on release (asyncpg automatically
        resets ``SET LOCAL`` settings on checkout+release thanks to the
        per-connection init hook — we use ``SET`` at session level here and
        restore explicitly).
        """
        if self._shutting_down:
            raise RuntimeError("PgPool is shutting down; no new checkouts")

        db_pool_in_use.inc()
        acquired = False
        try:
            async with self.pool.acquire() as conn:
                acquired = True
                if tenant_id is not None and tenant_id != self._config.default_tenant:
                    await conn.execute(
                        "SELECT set_config('app.current_tenant', $1, false)",
                        str(tenant_id),
                    )
                try:
                    yield conn
                finally:
                    if tenant_id is not None and tenant_id != self._config.default_tenant:
                        # Restore default tenant before returning to pool.
                        await conn.execute(
                            "SELECT set_config('app.current_tenant', $1, false)",
                            str(self._config.default_tenant),
                        )
        finally:
            if acquired:
                db_pool_in_use.dec()

    async def execute(
        self,
        query: str,
        *args: Any,
        tenant_id: UUID | None = None,
        op_label: str = "execute",
    ) -> str:
        """Run a statement, discarding the result. Returns the status string."""
        async with self.acquire(tenant_id=tenant_id) as conn:
            return await self._timed(op_label, conn.execute(query, *args))

    async def fetch(
        self,
        query: str,
        *args: Any,
        tenant_id: UUID | None = None,
        op_label: str = "fetch",
    ) -> list[asyncpg.Record]:
        """Run a query and return all rows."""
        async with self.acquire(tenant_id=tenant_id) as conn:
            return await self._timed(op_label, conn.fetch(query, *args))

    async def fetchrow(
        self,
        query: str,
        *args: Any,
        tenant_id: UUID | None = None,
        op_label: str = "fetchrow",
    ) -> asyncpg.Record | None:
        """Run a query and return the first row (or None)."""
        async with self.acquire(tenant_id=tenant_id) as conn:
            return await self._timed(op_label, conn.fetchrow(query, *args))

    async def fetchval(
        self,
        query: str,
        *args: Any,
        tenant_id: UUID | None = None,
        op_label: str = "fetchval",
    ) -> Any:
        """Run a query and return the first column of the first row."""
        async with self.acquire(tenant_id=tenant_id) as conn:
            return await self._timed(op_label, conn.fetchval(query, *args))

    async def copy_records_to_table(
        self,
        table: str,
        *,
        records: list[tuple[Any, ...]],
        columns: list[str],
        tenant_id: UUID | None = None,
    ) -> int:
        """Bulk-insert records via ``COPY``. Returns the row count.

        Used by the signal ingestion path (``SIGNAL_INGEST_BATCH_SIZE``).
        """
        async with self.acquire(tenant_id=tenant_id) as conn:
            start = time.monotonic()
            try:
                result = await conn.copy_records_to_table(
                    table, records=records, columns=columns,
                )
            finally:
                db_query_duration_seconds.labels(op="copy_records").observe(
                    time.monotonic() - start,
                )
            # asyncpg returns e.g. "COPY 42".
            try:
                return int(result.rsplit(" ", 1)[-1])
            except (ValueError, AttributeError):
                return len(records)

    async def _timed(
        self, op_label: str, awaitable: Awaitable[_T],
    ) -> _T:
        """Await ``awaitable`` and record its duration under ``op_label``.

        Generic over the awaitable's return type so callers don't need
        to ``cast`` after awaiting.
        """
        start = time.monotonic()
        try:
            return await awaitable
        finally:
            db_query_duration_seconds.labels(op=op_label).observe(
                time.monotonic() - start,
            )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health(self, *, timeout: float = 2.0) -> HealthResult:
        """Check reachability, latency, and server version.

        Returns a ``HealthResult`` — NEVER raises on a normal failure. The
        caller decides what a failing result means (usually a 503 from the
        health endpoint, or a scaled-down SLO alert).
        """
        if self._pool is None:
            return HealthResult(ok=False, latency_ms=None, server_version=None,
                                error="pool not connected")
        t0 = time.monotonic()
        try:
            async with self.pool.acquire() as conn, asyncio.timeout(timeout):
                version_str = await conn.fetchval("SELECT version()")
                one = await conn.fetchval("SELECT 1")
                if one != 1:
                    return HealthResult(ok=False, latency_ms=None,
                                        server_version=None,
                                        error="SELECT 1 returned unexpected value")
                return HealthResult(
                    ok=True,
                    latency_ms=(time.monotonic() - t0) * 1000.0,
                    server_version=_short_version(version_str),
                    error=None,
                )
        except TimeoutError:  # asyncio.timeout raises TimeoutError
            return HealthResult(
                ok=False, latency_ms=None, server_version=None,
                error=f"health timeout after {timeout}s",
            )
        except Exception as e:
            return HealthResult(
                ok=False, latency_ms=None, server_version=None,
                error=f"{type(e).__name__}: {e}",
            )

    # ------------------------------------------------------------------
    # Transaction helpers
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def transaction(
        self,
        *,
        tenant_id: UUID | None = None,
        isolation: str | None = None,
        readonly: bool = False,
    ) -> AsyncIterator[asyncpg.Connection]:
        """Run a block inside a transaction.

        Args:
            tenant_id: override RLS context for this transaction.
            isolation: one of ``"read_committed"``, ``"repeatable_read"``,
                ``"serializable"``.
            readonly: mark transaction ``READ ONLY`` (lets the planner skip
                locking; required for read-replica routing later).
        """
        async with self.acquire(tenant_id=tenant_id) as conn, conn.transaction(isolation=isolation, readonly=readonly):
            yield conn


# =============================================================================
# Helpers
# =============================================================================


@dataclass(frozen=True, slots=True)
class HealthResult:
    """Outcome of a ``PgPool.health()`` check."""

    ok: bool
    latency_ms: float | None
    server_version: str | None
    error: str | None


def _short_version(version_str: str | None) -> str | None:
    """Extract a short version tag from ``SELECT version()`` output.

    Example input: ``"PostgreSQL 16.4 on x86_64-pc-linux-gnu, ..."``
    Example output: ``"16.4"``
    """
    if not version_str:
        return None
    parts = version_str.split()
    if len(parts) >= 2 and parts[0] == "PostgreSQL":
        return parts[1]
    return version_str[:40]


# =============================================================================
# Module-level convenience (optional — DI-friendly callers can ignore)
# =============================================================================

_shared_pool: PgPool | None = None


def set_shared_pool(pool: PgPool | None) -> None:
    """Install (or clear) a process-global shared pool. Optional convenience.

    Code paths that prefer DI should pass ``PgPool`` explicitly. The shared
    pool pattern is useful for CLI commands and short-lived jobs.
    """
    global _shared_pool  # noqa: PLW0603
    _shared_pool = pool


def get_shared_pool() -> PgPool:
    """Return the process-global shared pool; raises if unset."""
    if _shared_pool is None:
        raise RuntimeError(
            "shared PgPool not set; call set_shared_pool(pool) at startup",
        )
    return _shared_pool


__all__ = [
    "DEFAULT_TENANT_ID",
    "HealthResult",
    "PgConfig",
    "PgPool",
    "get_shared_pool",
    "set_shared_pool",
]
