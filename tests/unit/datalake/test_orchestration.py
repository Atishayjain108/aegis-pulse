"""Orchestration flow tests.

These exercise the Prefect-absent path (graceful degradation). The flows
remain plain async functions when Prefect isn't installed.
"""

from __future__ import annotations

import asyncio

import pytest

from aegis.datalake.orchestration import flows
from aegis.datalake.settings import DataLakeSettings


class TestPrefectDetection:
    def test_prefect_available_flag(self) -> None:
        # Either True or False — both valid; we just want the flag exposed.
        assert isinstance(flows.PREFECT_AVAILABLE, bool)


class TestSilverFlow:
    def test_silver_flow_runs(self, tmp_settings: DataLakeSettings) -> None:
        # No bronze rows → silver builders are no-ops, but the flow must complete.
        result = flows.silver_build_for_date(
            settings=tmp_settings, date_iso="2026-05-20",
        )
        assert isinstance(result, dict)
        assert "signals" in result


class TestGoldFlow:
    def test_gold_flow_runs(self, tmp_settings: DataLakeSettings) -> None:
        result = flows.gold_build_for_date(
            settings=tmp_settings, date_iso="2026-05-20",
        )
        assert isinstance(result, dict)
        assert "daily_platform_stats" in result


class TestEndToEndFlowWithoutPool:
    def test_end_to_end_without_pool_raises_only_when_called(
        self, tmp_settings: DataLakeSettings
    ) -> None:
        # The flow expects a pool — passing None should surface clearly.
        async def _run():
            return await flows.end_to_end_for_date(
                settings=tmp_settings, pool=None, date_iso="2026-05-20",
            )

        with pytest.raises(Exception):
            asyncio.run(_run())
