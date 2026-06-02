"""
tests.conftest
==============

Shared pytest fixtures for the AEGIS Pulse test suite.

Layered design
--------------

* **Unit tests** depend only on the ``reset_registry`` and ``capture_logs``
  fixtures.  They never touch Docker, the network, or the file system
  outside of ``tmp_path``.

* **Integration tests** additionally request ``pg_container``,
  ``redis_container``, or ``minio_container``.  Each container spins up
  exactly once per test session and is shared across tests; rows / keys
  / objects are namespaced by tenant UUID so tests cannot collide.

* If Docker is not available on the host, integration fixtures emit a
  clean ``pytest.skip`` rather than raising — this keeps ``pytest`` runs
  on dev laptops without Docker frictionless.

Environment isolation
---------------------

Every test runs inside a context where ``AEGIS_*`` env vars are reset to
deterministic test values.  The pydantic-settings ``settings()`` cache
is also flushed via ``reload_settings()`` to prevent leakage between
tests that mutate the environment.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    # Imported only for type hints to avoid hard runtime dependency on
    # heavy modules during pure-unit collection.
    from collections.abc import AsyncIterator, Iterator

    from aegis.cache.redis_cache import RedisCache
    from aegis.db.pool import PgPool


# ---------------------------------------------------------------------
# Environment setup — applied before any aegis.* import in test modules
# ---------------------------------------------------------------------

_TEST_ENV: dict[str, str] = {
    "AEGIS_ENV": "test",
    "AEGIS_LOG_LEVEL": "DEBUG",
    "AEGIS_LOG_JSON": "false",
    "AEGIS_SERVICE_NAME": "aegis-pulse-tests",
    "AEGIS_PG_DSN": "postgresql://aegis_app:test@127.0.0.1:5432/aegis_test",
    "AEGIS_REDIS_URL": "redis://127.0.0.1:6379/15",
    "AEGIS_DEFAULT_TENANT_ID": "00000000-0000-0000-0000-000000000001",
    # Phase 2 — disable Ollama in unit tests (heuristic-only path)
    "AEGIS_DISABLE_OLLAMA": "1",
    # Phase 2 — deterministic HMAC key for messaging tests
    "AEGIS_AGENT_HMAC_KEY": "test-key-do-not-use-in-prod",
}


def pytest_configure(config: pytest.Config) -> None:
    """Apply test environment defaults *before* any aegis import happens.

    This runs once at collection start, well before the first fixture
    is evaluated, which is the only safe moment to mutate the
    environment for a process-wide singleton like ``settings()``.
    """
    for key, value in _TEST_ENV.items():
        os.environ.setdefault(key, value)

    config.addinivalue_line(
        "markers",
        "integration: marks tests as requiring docker (deselect with -m 'not integration')",
    )
    config.addinivalue_line(
        "markers",
        "slow: marks tests that take >1s (deselect with -m 'not slow')",
    )


# ---------------------------------------------------------------------
# Registry / settings reset — runs around every test
# ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_registry() -> Iterator[None]:
    """Reset the Prometheus registry and settings cache around every test.

    Without this, a metric registered during one test would persist into
    the next, causing ``Duplicated timeseries`` errors when any test
    re-registers the same metric (which the metrics module does at
    import time).  We snapshot ``os.environ`` too so a test that
    mutates env vars cannot leak into its neighbours.
    """
    # Snapshot env so per-test mutations are reversible.
    env_snapshot = dict(os.environ)

    # Lazy imports so this fixture works even when only a tiny subset
    # of the package is importable (e.g. during early development).
    try:
        from aegis.config import reload_settings
        from aegis.core.metrics import reset_registry as _reset_metrics
    except Exception:
        reload_settings = None
        _reset_metrics = None

    if _reset_metrics is not None:
        _reset_metrics()
    if reload_settings is not None:
        reload_settings()

    yield

    # Restore env exactly.
    for key in list(os.environ.keys()):
        if key not in env_snapshot:
            del os.environ[key]
    for key, value in env_snapshot.items():
        os.environ[key] = value

    if _reset_metrics is not None:
        _reset_metrics()
    if reload_settings is not None:
        reload_settings()


@pytest.fixture
def capture_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Convenience wrapper that sets DEBUG capture and structlog bridging."""
    caplog.set_level(logging.DEBUG, logger="aegis")
    return caplog


@pytest.fixture
def tenant_id() -> uuid.UUID:
    """A deterministic tenant UUID for tests that need one."""
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------
# Asyncio event loop
# ---------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    """Use the default policy; pytest-asyncio's ``asyncio_mode = auto`` does the rest."""
    return asyncio.DefaultEventLoopPolicy()


# ---------------------------------------------------------------------
# Docker availability gate — used by every container fixture
# ---------------------------------------------------------------------


def _docker_available() -> bool:
    """Return ``True`` iff a docker daemon is reachable.

    We use a cheap socket probe before falling back to ``docker info``
    so the common "no docker installed" case skips fast.
    """
    if not shutil.which("docker"):
        return False
    # Common docker socket locations on Linux/macOS.
    for path in ("/var/run/docker.sock", str(Path("~/.docker/run/docker.sock").expanduser())):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                sock.connect(path)
                return True
        except OSError:
            continue
    # Last-resort: actually call ``docker info``.  This is slower (~1 s)
    # but catches Windows/Colima/Rancher setups.
    import subprocess

    try:
        result = subprocess.run(
            ["docker", "info"],
            check=False,
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


_HAS_DOCKER = _docker_available()


def _requires_docker() -> None:
    """Skip the calling test if Docker is not available."""
    if not _HAS_DOCKER:
        pytest.skip("docker not available; skipping integration test")


# ---------------------------------------------------------------------
# Postgres container — session scoped
# ---------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_container() -> Iterator[Any]:
    """Spin up a TimescaleDB-flavoured Postgres for integration tests."""
    _requires_docker()
    try:
        from testcontainers.postgres import PostgresContainer  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("testcontainers not installed; skipping integration test")

    # We use vanilla postgres for the unit-flavoured DB tests.  The full
    # TimescaleDB hypertable suite lives elsewhere and is opt-in.
    container = PostgresContainer(image="postgres:16-alpine")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
async def pg_pool(pg_container: Any) -> AsyncIterator[PgPool]:
    """Yield a started ``PgPool`` bound to the test Postgres container."""
    from aegis.db.pool import PgConfig, PgPool

    dsn = pg_container.get_connection_url().replace("postgresql+psycopg2", "postgresql")
    pool = PgPool(PgConfig(dsn=dsn, min_size=1, max_size=4))
    await pool.start()
    try:
        yield pool
    finally:
        await pool.close()


# ---------------------------------------------------------------------
# Redis container — session scoped
# ---------------------------------------------------------------------


@pytest.fixture(scope="session")
def redis_container() -> Iterator[Any]:
    _requires_docker()
    try:
        from testcontainers.redis import RedisContainer  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("testcontainers not installed; skipping integration test")

    container = RedisContainer(image="redis:7-alpine")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
async def redis_cache(redis_container: Any) -> AsyncIterator[RedisCache]:
    """Yield a started ``RedisCache`` bound to the test Redis container."""
    from aegis.cache.redis_cache import RedisCache

    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    url = f"redis://{host}:{port}/0"

    cache = RedisCache(url=url, namespace="aegis-test")
    # Some implementations have explicit ``connect``; tolerate both.
    if hasattr(cache, "connect"):
        await cache.connect()
    try:
        yield cache
    finally:
        if hasattr(cache, "close"):
            await cache.close()


# ---------------------------------------------------------------------
# MinIO container — session scoped
# ---------------------------------------------------------------------


@pytest.fixture(scope="session")
def minio_container() -> Iterator[Any]:
    _requires_docker()
    try:
        from testcontainers.minio import MinioContainer  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("testcontainers (minio extra) not installed; skipping")

    container = MinioContainer()
    container.start()
    try:
        yield container
    finally:
        container.stop()


# ---------------------------------------------------------------------
# Phase 3 predict fixtures — no Docker / network / API keys required
# ---------------------------------------------------------------------

import random  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402


@pytest.fixture(autouse=True)
def seed_rng() -> None:
    """Autouse fixture to seed the random number generator for deterministic tests."""
    random.seed(1234)


@pytest.fixture
def utc_now() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def synthetic_signals(utc_now: datetime) -> list[dict[str, Any]]:
    """72-row synthetic signal stream usable by the Phase 3 feature builder."""
    base = utc_now - timedelta(hours=72)
    rows: list[dict[str, Any]] = []
    for i in range(72):
        rows.append(
            {
                "id": f"sig-{i}",
                "platform": "twitter" if i < 36 else "tiktok",
                "captured_at": base + timedelta(hours=i),
                "title": None,
                "body": f"msg {i}",
                "url": None,
                "content_hash": f"h{i:08d}",
                "author_id": f"a{i % 5}",
                "views": i * 10,
                "likes": i,
                "comments": i // 2,
                "shares": 0,
                "saves": 0,
                "sentiment": 0.0,
                "commercial_intent": 0.0,
                "novelty": 0.5,
            }
        )
    return rows


@pytest.fixture
def feature_window(synthetic_signals: list[dict[str, Any]], utc_now: datetime) -> Any:
    """A built FeatureWindow over the synthetic signal stream."""
    try:
        from aegis.predict.features.builder import build_window_from_rows

        return build_window_from_rows(
            tenant_id="t1",
            trend_id="trend-1",
            rows=synthetic_signals,
            window_end=utc_now,
            window_size=168,
        )
    except ImportError:
        pytest.skip("aegis.predict not available")


@pytest.fixture
def empty_window(utc_now: datetime) -> Any:
    """A zero-filled FeatureWindow useful for boundary tests."""
    try:
        from aegis.predict import FEATURE_DIM
        from aegis.predict.schemas import FeatureWindow

        return FeatureWindow(
            tenant_id="t1",
            trend_id="empty",
            captured_at=utc_now,
            window_size=168,
            feature_dim=FEATURE_DIM,
            values=[0.0] * (168 * FEATURE_DIM),
        )
    except ImportError:
        pytest.skip("aegis.predict not available")
