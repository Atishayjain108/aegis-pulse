"""Gold aggregator tests."""

from __future__ import annotations

from aegis.datalake.facade import DataLake


class TestGoldDailyPlatformStats:
    def test_with_no_silver_is_noop(self, lake: DataLake) -> None:
        stats = lake.gold.build_daily_platform_stats(date_iso="2026-05-20")
        assert stats.silver_rows_read == 0
        assert stats.gold_rows_written == 0

    def test_aggregates_by_platform(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        lake.silver.build_signals_for_date(date_iso="2026-05-20")
        stats = lake.gold.build_daily_platform_stats(date_iso="2026-05-20")
        # Sample data uses 5 platforms in rotation.
        assert stats.gold_rows_written >= 1
        assert stats.silver_rows_read == len(sample_signal_rows)

    def test_query_gold_returns_per_platform_counts(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        lake.silver.build_signals_for_date(date_iso="2026-05-20")
        lake.gold.build_daily_platform_stats(date_iso="2026-05-20")

        result = lake.query(
            "SELECT platform, signal_count FROM gold_daily_platform_stats "
            "ORDER BY signal_count DESC"
        )
        rows = result.to_dicts()
        assert rows
        total = sum(r["signal_count"] for r in rows)
        assert total == len(sample_signal_rows)


class TestGoldTrendVerdictRollup:
    def test_no_input_is_noop(self, lake: DataLake) -> None:
        stats = lake.gold.build_trend_verdict_rollup(date_iso="2026-05-20")
        assert stats.gold_rows_written == 0


class TestGoldPredictionAccuracy:
    def test_no_input_is_noop(self, lake: DataLake) -> None:
        stats = lake.gold.build_prediction_accuracy(date_iso="2026-05-20")
        assert stats.gold_rows_written == 0
