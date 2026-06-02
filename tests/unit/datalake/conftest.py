"""Shared fixtures for Phase 10 tests.

Every test runs against a *local-filesystem* backend so the suite has zero
infrastructure dependencies — no MinIO, no Postgres, no Redis are required.
The same code paths are exercised: the only swap is the backend Protocol
implementation.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aegis.core.logging import configure_logging, is_configured
from aegis.datalake.catalog.registry import LakeCatalog
from aegis.datalake.facade import DataLake
from aegis.datalake.settings import DataLakeSettings
from aegis.datalake.storage.backend import LocalStorageBackend

# --------------------------------------------------------------------------- #
# Settings + backends
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _datalake_cli_logging() -> Iterator[None]:
    """Force human-readable logging for CLI tests (stderr is not a TTY in pytest)."""
    if is_configured():
        configure_logging(level="WARNING", json_output=False)
    yield
    if is_configured():
        configure_logging(level="WARNING", json_output=False)


@pytest.fixture()
def tmp_root() -> Iterator[str]:
    """A temporary directory that is wiped after the test."""
    path = tempfile.mkdtemp(prefix="aegis-phase10-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def tmp_settings(tmp_root: str) -> DataLakeSettings:
    """A :class:`DataLakeSettings` configured to use the local FS."""
    return DataLakeSettings(
        use_local_filesystem=True,
        local_root=Path(tmp_root),
        catalog_db_path=Path(tmp_root) / "catalog.db",
        bucket="aegis-test",
        tenant_id="00000000-0000-0000-0000-000000000001",
        # keep retention generous so accidental retention runs are no-ops
        silver_retention_days=365,
        gold_retention_days=1095,
    )


@pytest.fixture()
def tmp_backend(tmp_root: str) -> LocalStorageBackend:
    return LocalStorageBackend(Path(tmp_root))


@pytest.fixture()
def tmp_catalog(tmp_settings: DataLakeSettings) -> Iterator[LakeCatalog]:
    cat = LakeCatalog(tmp_settings.catalog_db_path)
    try:
        yield cat
    finally:
        cat.close()


@pytest.fixture()
def lake(tmp_settings: DataLakeSettings) -> Iterator[DataLake]:
    """A fully-wired :class:`DataLake` against the local filesystem."""
    instance = DataLake.open(tmp_settings)
    try:
        yield instance
    finally:
        instance.close()


# --------------------------------------------------------------------------- #
# Sample data
# --------------------------------------------------------------------------- #


@pytest.fixture()
def sample_signal_rows() -> list[dict]:
    """A small batch of realistic Bronze ``signals`` rows."""
    return [
        {
            "signal_id": f"s-{i}",
            "tenant_id": "00000000-0000-0000-0000-000000000001",
            "platform": platform,
            "title": f"Test signal {i}",
            "url": f"https://example.com/post/{i}",
            "author_handle": f"user_{i % 3}",
            "captured_at": datetime(2026, 5, 20, 12, i % 60, tzinfo=UTC).isoformat(),
            "views": 100 * i,
            "likes": 10 * i,
            "comments": 2 * i,
            "shares": i,
            "saves": i // 2,
            "raw_json": {"source": "fixture"},
        }
        for i, platform in enumerate(
            ["reddit", "hacker_news", "tiktok", "amazon", "youtube"] * 4
        )
    ]


@pytest.fixture()
def sample_prediction_rows() -> list[dict]:
    """A small batch of realistic Bronze ``predictions`` rows."""
    return [
        {
            "prediction_id": f"p-{i}",
            "tenant_id": "00000000-0000-0000-0000-000000000001",
            "trend_id": f"trend-{i % 3}",
            "finished_at": datetime(2026, 5, 20, 14, i % 60, tzinfo=UTC).isoformat(),
            "horizon_hours": 24,
            "p_breakout": 0.1 * (i % 10),
            "p_decline": 0.05 * (i % 5),
            "confidence": 0.5 + (i % 5) * 0.1,
            "model_manifest_id": "manifest-v1",
        }
        for i in range(8)
    ]


@pytest.fixture()
def sample_alert_rows() -> list[dict]:
    """A small batch of realistic Bronze ``alerts`` rows."""
    return [
        {
            "alert_id": f"a-{i}",
            "tenant_id": "00000000-0000-0000-0000-000000000001",
            "trend_id": f"trend-{i % 3}",
            "created_at": datetime(2026, 5, 20, 15, i % 60, tzinfo=UTC).isoformat(),
            "verdict": ["ENTER", "HOLD", "BLOCK"][i % 3],
            "score": 0.5 + (i % 5) * 0.08,
            "confidence": 0.6 + (i % 4) * 0.07,
            "halt_reason": None,
            "channels": ["telegram", "ntfy"],
        }
        for i in range(6)
    ]


@pytest.fixture()
def sample_agent_result_rows() -> list[dict]:
    """Phase 2 graph results — these land in Bronze via the Redis stream ingester."""
    return [
        {
            "trend_id": f"trend-{i % 3}",
            "tenant_id": "00000000-0000-0000-0000-000000000001",
            "final_verdict": ["ENTER", "HOLD", "BLOCK"][i % 3],
            "raw_verdict": ["proceed", "hold", "block"][i % 3],
            "final_score": 0.4 + (i % 6) * 0.1,
            "final_confidence": 0.55 + (i % 5) * 0.08,
            "final_priority": ["P0", "P1", "P2"][i % 3],
            "halt_reason": None,
            "started_at": datetime(2026, 5, 20, 13, i % 60, tzinfo=UTC).isoformat(),
            "finished_at": datetime(2026, 5, 20, 13, (i % 60) + 1, tzinfo=UTC).isoformat(),
            "duration_ms": 1200 + i * 50,
            "data_confidence": 0.9,
            "decisions": [],
            "blocked_by": [],
        }
        for i in range(5)
    ]
