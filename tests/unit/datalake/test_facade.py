"""DataLake facade tests + end-to-end pipeline through the facade."""

from __future__ import annotations

import pytest

from aegis.datalake.constants import BRONZE, SILVER
from aegis.datalake.errors import ConfigurationError
from aegis.datalake.facade import DataLake, DataLakeHealth
from aegis.datalake.settings import DataLakeSettings


class TestOpenAndClose:
    def test_open_returns_lake(self, tmp_settings: DataLakeSettings) -> None:
        lake = DataLake.open(tmp_settings)
        try:
            assert lake.settings is tmp_settings
        finally:
            lake.close()

    def test_double_close_is_safe(self, tmp_settings: DataLakeSettings) -> None:
        lake = DataLake.open(tmp_settings)
        lake.close()
        lake.close()  # must not raise

    def test_session_context_manager(self, tmp_settings: DataLakeSettings) -> None:
        with DataLake.session(tmp_settings) as lake:
            assert lake is not None
        # post-session, calling close again must still be safe
        # but the engine may have been released already.

    def test_lake_is_context_manager(self, tmp_settings: DataLakeSettings) -> None:
        with DataLake.open(tmp_settings) as lake:
            assert lake is not None


class TestProperties:
    def test_components_exposed(self, lake: DataLake) -> None:
        assert lake.bronze is not None
        assert lake.silver is not None
        assert lake.gold is not None
        assert lake.catalog is not None
        assert lake.backend is not None

    def test_engine_lazy(self, lake: DataLake) -> None:
        # Engine should be created on first access only.
        e1 = lake.engine
        e2 = lake.engine
        assert e1 is e2


class TestHealth:
    def test_health_on_fresh_lake(self, lake: DataLake) -> None:
        h = lake.health()
        assert isinstance(h, DataLakeHealth)
        assert h.backend_ok
        assert h.catalog_ok
        assert h.tables_registered == 0
        assert h.healthy

    def test_health_after_writes(self, lake: DataLake, sample_signal_rows: list[dict]) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        h = lake.health()
        assert h.tables_registered >= 1


class TestListTables:
    def test_empty_initially(self, lake: DataLake) -> None:
        assert lake.list_tables() == []

    def test_invalid_layer_raises(self, lake: DataLake) -> None:
        with pytest.raises(ConfigurationError):
            lake.list_tables(layer="platinum")

    def test_filter_by_layer(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        bronze_tables = lake.list_tables(layer=BRONZE)
        assert len(bronze_tables) >= 1
        assert all(t["layer"] == BRONZE for t in bronze_tables)
        silver_tables = lake.list_tables(layer=SILVER)
        assert silver_tables == []


class TestBuildSilverAndGold:
    def test_build_silver_for_date(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        out = lake.build_silver("2026-05-20")
        assert "signals" in out
        assert out["signals"]["silver_rows_written"] > 0

    def test_build_gold_for_date(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        lake.build_silver("2026-05-20")
        out = lake.build_gold("2026-05-20")
        assert "daily_platform_stats" in out
        assert out["daily_platform_stats"]["gold_rows_written"] > 0


class TestQuery:
    def test_query_after_pipeline(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        lake.build_silver("2026-05-20")
        lake.build_gold("2026-05-20")

        result = lake.query("SELECT COUNT(*) AS n FROM silver_signals")
        rows = result.to_dicts()
        assert len(rows) == 1
        assert rows[0]["n"] == len(sample_signal_rows)

    def test_query_rejects_write_statements(self, lake: DataLake) -> None:
        from aegis.datalake.errors import QueryError

        with pytest.raises(QueryError):
            lake.query("CREATE TABLE evil (x INT)")
