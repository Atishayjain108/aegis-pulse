"""Tests for ProfileStore (Phase M1) — asyncpg mocked, RLS enforced."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aegis.mentor.profiles import ProfileStore
from aegis.mentor.schemas import (
    AutonomyPreference,
    Channel,
    ExperienceLevel,
    Intent,
    RiskTolerance,
    UserProfile,
)


def _make_pool(row=None):
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=row)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _profile(**kw) -> UserProfile:
    base = {"sector": "d2c_india", "intent": Intent.INCOME, "raw_description": "sell online"}
    base.update(kw)
    return UserProfile(**base)


class TestUpsert:
    @pytest.mark.asyncio
    async def test_upsert_sets_tenant_and_inserts(self) -> None:
        pool, conn = _make_pool()
        store = ProfileStore(pool)
        ok = await store.upsert(_profile())
        assert ok is True
        # First call sets RLS tenant context, second is the upsert.
        assert conn.execute.call_count == 2
        first_sql = conn.execute.call_args_list[0].args[0]
        assert "app.current_tenant" in first_sql

    @pytest.mark.asyncio
    async def test_upsert_db_failure_returns_false(self) -> None:
        pool, conn = _make_pool()
        conn.execute = AsyncMock(side_effect=RuntimeError("db down"))
        store = ProfileStore(pool)
        assert await store.upsert(_profile()) is False


class TestFetch:
    @pytest.mark.asyncio
    async def test_fetch_maps_row_to_profile(self) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        row = {
            "profile_id": "11111111-1111-1111-1111-111111111111",
            "tenant_id": "00000000-0000-0000-0000-000000000001",
            "raw_description": "sell online",
            "sector": "d2c_india",
            "sector_raw": "sell online",
            "intent": "income",
            "autonomy_preference": "guide_me",
            "capital_usd": 100.0,
            "risk_tolerance": "low",
            "channels": "online_only",
            "skills": json.dumps(["design"]),
            "constraints": json.dumps(["never_leaves_home"]),
            "time_per_week_hrs": 10.0,
            "experience_level": "none",
            "region": "IN",
            "currency": "INR",
            "created_at": now,
            "updated_at": now,
        }
        pool, _ = _make_pool(row=row)
        store = ProfileStore(pool)
        p = await store.fetch("11111111-1111-1111-1111-111111111111")
        assert p is not None
        assert p.sector == "d2c_india"
        assert p.intent == Intent.INCOME
        assert p.autonomy_preference == AutonomyPreference.GUIDE_ME
        assert p.risk_tolerance == RiskTolerance.LOW
        assert p.channels == Channel.ONLINE_ONLY
        assert p.experience_level == ExperienceLevel.NONE
        assert p.skills == ["design"]
        assert p.constraints == ["never_leaves_home"]

    @pytest.mark.asyncio
    async def test_fetch_missing_returns_none(self) -> None:
        pool, _ = _make_pool(row=None)
        store = ProfileStore(pool)
        assert await store.fetch("nope") is None
