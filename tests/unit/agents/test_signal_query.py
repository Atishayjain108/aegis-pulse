"""Tests for aegis.agents.tools.signal_query (0% → covered)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from aegis.agents.tools.signal_query import fetch_recent


class TestFetchRecentNoPool:
    @pytest.mark.asyncio
    async def test_no_pool_returns_failure(self):
        with patch(
            "aegis.db.pool.get_shared_pool",
            side_effect=RuntimeError("shared PgPool not set"),
        ):
            result = await fetch_recent(tenant_id="00000000-0000-0000-0000-000000000001")
        assert not result.ok
        assert result.error is not None
        assert "AEGIS-TOOL-SIGNAL-NO-POOL" in result.error.code

    @pytest.mark.asyncio
    async def test_result_has_duration_ms(self):
        with patch(
            "aegis.db.pool.get_shared_pool",
            side_effect=RuntimeError("shared PgPool not set"),
        ):
            result = await fetch_recent(tenant_id="00000000-0000-0000-0000-000000000001")
        assert result.duration_ms >= 0.0


class TestFetchRecentWithMockPool:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_db_returns_nothing(self):
        pool = MagicMock()

        async def fake_fetch(
            p: Any, *, tenant_id: Any, limit: int, platform: str | None = None, since: Any = None
        ) -> list[Any]:
            return []

        with (
            patch("aegis.db.pool.get_shared_pool", return_value=pool),
            patch("aegis.db.signals.fetch_recent_signals", side_effect=fake_fetch),
        ):
            result = await fetch_recent(tenant_id="00000000-0000-0000-0000-000000000001")
        assert result.ok
        assert result.data == []

    @pytest.mark.asyncio
    async def test_returns_dict_rows(self):
        pool = MagicMock()
        rows = [{"signal_id": "s1", "platform": "reddit", "title": "Hello"}]

        async def fake_fetch(
            p: Any, *, tenant_id: Any, limit: int, platform: str | None = None, since: Any = None
        ) -> list[Any]:
            return rows

        with (
            patch("aegis.db.pool.get_shared_pool", return_value=pool),
            patch("aegis.db.signals.fetch_recent_signals", side_effect=fake_fetch),
        ):
            result = await fetch_recent(
                tenant_id="00000000-0000-0000-0000-000000000001",
                limit=10,
            )
        assert result.ok
        assert len(result.data) == 1
        assert result.data[0]["platform"] == "reddit"

    @pytest.mark.asyncio
    async def test_platform_filter_passed_through(self):
        pool = MagicMock()
        captured: dict[str, Any] = {}

        async def fake_fetch(
            p: Any, *, tenant_id: Any, limit: int, platform: str | None = None, since: Any = None
        ) -> list[Any]:
            captured["platform"] = platform
            return []

        with (
            patch("aegis.db.pool.get_shared_pool", return_value=pool),
            patch("aegis.db.signals.fetch_recent_signals", side_effect=fake_fetch),
        ):
            await fetch_recent(
                tenant_id="00000000-0000-0000-0000-000000000001",
                platform="reddit",
            )
        assert captured["platform"] == "reddit"

    @pytest.mark.asyncio
    async def test_db_exception_returns_failure(self):
        pool = MagicMock()

        async def fake_fetch(
            p: Any, *, tenant_id: Any, limit: int, platform: str | None = None, since: Any = None
        ) -> list[Any]:
            raise RuntimeError("db connection refused")

        with (
            patch("aegis.db.pool.get_shared_pool", return_value=pool),
            patch("aegis.db.signals.fetch_recent_signals", side_effect=fake_fetch),
        ):
            result = await fetch_recent(tenant_id="00000000-0000-0000-0000-000000000001")
        assert not result.ok
        assert result.error is not None
        assert "AEGIS-TOOL-SIGNAL-DB" in result.error.code

    @pytest.mark.asyncio
    async def test_model_dump_rows_serialised(self):
        pool = MagicMock()

        class FakeModel:
            def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
                return {"x": 1}

        async def fake_fetch(
            p: Any, *, tenant_id: Any, limit: int, platform: str | None = None, since: Any = None
        ) -> list[Any]:
            return [FakeModel()]

        with (
            patch("aegis.db.pool.get_shared_pool", return_value=pool),
            patch("aegis.db.signals.fetch_recent_signals", side_effect=fake_fetch),
        ):
            result = await fetch_recent(tenant_id="00000000-0000-0000-0000-000000000001")
        assert result.ok
        assert result.data == [{"x": 1}]

    @pytest.mark.asyncio
    async def test_raw_fallback_row(self):
        """Rows that are neither dict nor model get serialised as {'_raw': str}."""
        pool = MagicMock()

        class WeirdRow:
            def __iter__(self):
                raise TypeError("not iterable")

            def __str__(self):
                return "weird"

        async def fake_fetch(
            p: Any, *, tenant_id: Any, limit: int, platform: str | None = None, since: Any = None
        ) -> list[Any]:
            return [WeirdRow()]

        with (
            patch("aegis.db.pool.get_shared_pool", return_value=pool),
            patch("aegis.db.signals.fetch_recent_signals", side_effect=fake_fetch),
        ):
            result = await fetch_recent(tenant_id="00000000-0000-0000-0000-000000000001")
        assert result.ok
        assert result.data[0].get("_raw") == "weird"

    @pytest.mark.asyncio
    async def test_count_metadata_matches_rows(self):
        pool = MagicMock()
        rows: list[dict[str, Any]] = [{"id": i} for i in range(5)]

        async def fake_fetch(
            p: Any, *, tenant_id: Any, limit: int, platform: str | None = None, since: Any = None
        ) -> list[Any]:
            return rows

        with (
            patch("aegis.db.pool.get_shared_pool", return_value=pool),
            patch("aegis.db.signals.fetch_recent_signals", side_effect=fake_fetch),
        ):
            result = await fetch_recent(tenant_id="00000000-0000-0000-0000-000000000001")
        assert result.ok
        assert result.metadata.get("count") == 5
