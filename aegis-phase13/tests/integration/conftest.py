"""
tests/integration/conftest.py — Integration test fixtures using real infrastructure.

Provides session-scoped testcontainer fixtures for:
  - PostgreSQL 16 with TimescaleDB + pgvector (via timescale/timescaledb-ha image)
  - Redis 7
  - MinIO (S3-compatible object store)

All fixtures are session-scoped to avoid re-spinning containers per test.
Tests using these fixtures MUST be marked @pytest.mark.integration.

Environment gate: integration tests only run when AEGIS_TEST_POSTGRES=1 (etc.) is set,
OR when the full integration suite is invoked via `make test-integration`.

Usage in test file:
    @pytest.mark.integration
    async def test_something(pg_dsn: str, redis_url: str) -> None:
        ...
"""

from __future__ import annotations

from collections.abc import Generator
import os
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Guard: skip if AEGIS_TEST_INTEGRATION not set
# ---------------------------------------------------------------------------

def pytest_collection_modifyitems(items: list[Any]) -> None:
    """Auto-skip integration tests unless AEGIS_TEST_INTEGRATION=1."""
    skip_marker = pytest.mark.skip(
        reason="Integration tests disabled. Set AEGIS_TEST_INTEGRATION=1 to enable."
    )
    if os.getenv("AEGIS_TEST_INTEGRATION", "0") != "1":
        for item in items:
            if "integration" in str(item.fspath):
                item.add_marker(skip_marker)


# ---------------------------------------------------------------------------
# PostgreSQL container
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def pg_container() -> Generator[Any, None, None]:
    """Spin a real TimescaleDB container for the test session."""
    try:
        from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("testcontainers not installed")

    with PostgresContainer(
        image="timescale/timescaledb-ha:pg16-latest",
        username="aegis_test",
        password="aegis_test_pw",
        dbname="aegis_test",
        port=5432,
    ) as container:
        yield container


@pytest.fixture(scope="session")
def pg_dsn(pg_container: Any) -> str:
    """DSN string for the test PostgreSQL container."""
    return pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://"
    )


@pytest.fixture(scope="session")
async def pg_pool(pg_dsn: str) -> Any:
    """Shared asyncpg pool for the test session."""
    try:
        import asyncpg  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("asyncpg not installed")

    pool = await asyncpg.create_pool(
        dsn=pg_dsn,
        min_size=2,
        max_size=5,
        command_timeout=30,
    )
    # Apply migrations
    await _apply_test_migrations(pool)
    yield pool
    await pool.close()


async def _apply_test_migrations(pool: Any) -> None:
    """Apply the base AEGIS schema migrations to the test DB."""
    from pathlib import Path

    migrations_dir = Path(__file__).parent.parent.parent / "db" / "migrations"
    if not migrations_dir.exists():
        return  # Skip if migrations dir not found (running in isolation)

    async with pool.acquire() as conn:
        # Enable required extensions
        await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

        for sql_file in sorted(migrations_dir.glob("*.sql")):
            sql = sql_file.read_text()
            try:
                await conn.execute(sql)
            except Exception as exc:
                # Some migrations are idempotent; ignore IF NOT EXISTS errors
                if "already exists" not in str(exc).lower():
                    raise


# ---------------------------------------------------------------------------
# Redis container
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def redis_container() -> Generator[Any, None, None]:
    try:
        from testcontainers.redis import RedisContainer  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("testcontainers not installed")

    with RedisContainer(image="redis:7.4-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def redis_url(redis_container: Any) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/15"  # DB 15 = test isolation


@pytest.fixture()
async def redis_client(redis_url: str) -> Any:
    """Per-test Redis client that flushes DB 15 before each test."""
    try:
        import redis.asyncio as aioredis  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("redis[asyncio] not installed")

    client = aioredis.from_url(redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


# ---------------------------------------------------------------------------
# MinIO container
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def minio_container() -> Generator[Any, None, None]:
    try:
        from testcontainers.minio import MinioContainer  # type: ignore[import-untyped]
    except ImportError:
        # Fallback: use generic Docker container
        pytest.skip("testcontainers[minio] not installed")

    with MinioContainer(image="minio/minio:latest") as container:
        yield container


@pytest.fixture(scope="session")
def minio_endpoint(minio_container: Any) -> str:
    host = minio_container.get_container_host_ip()
    port = minio_container.get_exposed_port(9000)
    return f"http://{host}:{port}"


@pytest.fixture(scope="session")
def minio_client(minio_endpoint: str) -> Any:
    try:
        from minio import Minio  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("minio client not installed")

    client = Minio(
        minio_endpoint.replace("http://", ""),
        access_key="minioadmin",
        secret_key="minioadmin",
        secure=False,
    )
    # Create standard buckets
    for bucket in ["aegis-signals", "aegis-models", "aegis-datalake", "aegis-backups"]:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
    return client


# ---------------------------------------------------------------------------
# Tenant setup helper
# ---------------------------------------------------------------------------

@pytest.fixture()
async def test_tenant(pg_pool: Any) -> str:
    """Create a test tenant and return its UUID string."""
    import uuid
    tenant_id = str(uuid.uuid4())
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tenants (tenant_id, name, created_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT DO NOTHING
            """,
            tenant_id,
            f"test-tenant-{tenant_id[:8]}",
        )
    return tenant_id
