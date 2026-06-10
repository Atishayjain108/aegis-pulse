"""
tests/conftest.py — Phase 15 DR test fixtures.
"""

from __future__ import annotations

import os

# Ensure pydantic-settings doesn't try to read a real .env during tests
os.environ.setdefault("AEGIS_DR_PG_DSN", "postgresql://test:test@localhost:5433/test")
os.environ.setdefault("AEGIS_DR_REDIS_URL", "redis://localhost:6380/15")
os.environ.setdefault("AEGIS_DR_MINIO_ENDPOINT", "localhost:9002")
os.environ.setdefault("AEGIS_DR_MINIO_ACCESS_KEY", "test-key")
os.environ.setdefault("AEGIS_DR_MINIO_SECRET_KEY", "test-secret")
os.environ.setdefault("AEGIS_DR_DRILL_ENABLED", "false")
os.environ.setdefault("AEGIS_DR_RESTIC_ENABLED", "false")
