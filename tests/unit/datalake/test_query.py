"""DuckDB query engine tests."""

from __future__ import annotations

import pytest

from aegis.datalake.errors import QueryError
from aegis.datalake.facade import DataLake


class TestExecute:
    def test_basic_select_from_registered_view(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        result = lake.query("SELECT COUNT(*) AS n FROM bronze_signals")
        assert result.rowcount == 1
        assert result.to_dicts()[0]["n"] == len(sample_signal_rows)

    def test_columns_property(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        result = lake.query("SELECT signal_id, platform FROM bronze_signals LIMIT 1")
        assert "signal_id" in result.columns
        assert "platform" in result.columns

    def test_duration_ms_recorded(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        result = lake.query("SELECT COUNT(*) FROM bronze_signals")
        assert result.duration_ms >= 0


class TestReadOnlyEnforcement:
    @pytest.mark.parametrize(
        "sql",
        [
            "CREATE TABLE t (x INT)",
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET x = 2",
            "DELETE FROM t",
            "DROP TABLE t",
            "ALTER TABLE t ADD COLUMN y INT",
        ],
    )
    def test_write_statements_rejected(self, lake: DataLake, sql: str) -> None:
        with pytest.raises(QueryError):
            lake.query(sql)

    def test_select_allowed(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        result = lake.query("SELECT 1 AS one")
        assert result.to_dicts() == [{"one": 1}]

    def test_with_allowed(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        result = lake.query(
            "WITH t AS (SELECT 1 AS x) SELECT x FROM t"
        )
        assert result.to_dicts() == [{"x": 1}]

    def test_explain_allowed(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        result = lake.query("EXPLAIN SELECT * FROM bronze_signals")
        assert result.rowcount >= 1


class TestRegisterAllTables:
    def test_registers_after_pipeline(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        lake.silver.build_signals_for_date(date_iso="2026-05-20")
        lake.gold.build_daily_platform_stats(date_iso="2026-05-20")

        registered = lake.engine.register_all_tables()
        view_names = set(registered)
        assert "bronze_signals" in view_names
        assert "silver_signals" in view_names
        assert "gold_daily_platform_stats" in view_names
