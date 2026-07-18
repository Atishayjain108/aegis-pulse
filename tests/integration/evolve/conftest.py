"""Fixtures for the evolve integration suite.

``db_pool`` — a REAL asyncpg pool against the database the integration lane
provisions (AEGIS_PG_DSN; migrations already applied by `aegis migrate` in
the workflow). The suite referenced this fixture from day one but it was
never defined anywhere — "fixture 'db_pool' not found" surfaced on the
lane's first-ever execution (2026-07-17). No fakes here by design: this is
the lane that exists to catch what fixture-injected mocks cannot.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest


@pytest.fixture()
async def db_pool() -> AsyncIterator[asyncpg.Pool]:
    dsn = os.environ.get("AEGIS_PG_DSN", "")
    if not dsn:
        pytest.skip("AEGIS_PG_DSN not set — no database to integrate against")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        yield pool
    finally:
        await pool.close()
