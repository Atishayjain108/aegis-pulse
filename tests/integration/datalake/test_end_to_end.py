"""End-to-end integration: Bronze → Silver → Gold → Query, all local.

This is the integration counterpart of the unit tests. It exercises the full
pipeline through the public :class:`DataLake` facade and asserts the data
flows through every layer correctly.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aegis.datalake.facade import DataLake


def test_full_pipeline_signals_to_gold(
    lake: DataLake, sample_signal_rows: list[dict]
) -> None:
    """One contiguous pipeline run with assertions at every layer."""
    # --- Bronze --- #
    manifest = lake.bronze.write(
        table="signals", rows=sample_signal_rows,
        source="integration_test", partition_date="2026-05-20",
    )
    assert manifest.row_count == len(sample_signal_rows)
    bronze_partitions = lake.catalog.list_partitions(
        table_name="signals", layer="bronze"
    )
    assert len(bronze_partitions) == 1

    # --- Silver --- #
    silver_stats = lake.silver.build_signals_for_date(date_iso="2026-05-20")
    assert silver_stats.bronze_rows_read == len(sample_signal_rows)
    assert silver_stats.silver_rows_written == len(sample_signal_rows)
    assert silver_stats.rows_rejected == 0
    silver_partitions = lake.catalog.list_partitions(
        table_name="signals", layer="silver"
    )
    assert len(silver_partitions) >= 1

    # --- Gold --- #
    gold_stats = lake.gold.build_daily_platform_stats(date_iso="2026-05-20")
    assert gold_stats.silver_rows_read == len(sample_signal_rows)
    assert gold_stats.gold_rows_written >= 1

    # --- Query the pipeline outputs --- #
    bronze_count = lake.query("SELECT COUNT(*) AS n FROM bronze_signals").to_dicts()
    assert bronze_count[0]["n"] == len(sample_signal_rows)

    silver_count = lake.query("SELECT COUNT(*) AS n FROM silver_signals").to_dicts()
    assert silver_count[0]["n"] == len(sample_signal_rows)

    gold_rows = lake.query(
        "SELECT platform, signal_count FROM gold_daily_platform_stats "
        "ORDER BY signal_count DESC"
    ).to_dicts()
    total = sum(r["signal_count"] for r in gold_rows)
    assert total == len(sample_signal_rows)


def test_pipeline_is_idempotent(
    lake: DataLake, sample_signal_rows: list[dict]
) -> None:
    """Running each step twice with same input shouldn't duplicate data."""
    # First pass
    lake.bronze.write(
        table="signals", rows=sample_signal_rows,
        source="fx", partition_date="2026-05-20",
    )
    lake.silver.build_signals_for_date(date_iso="2026-05-20")
    lake.gold.build_daily_platform_stats(date_iso="2026-05-20")

    # Get baseline counts
    n1 = lake.query("SELECT COUNT(*) AS n FROM bronze_signals").to_dicts()[0]["n"]
    s1 = lake.query("SELECT COUNT(*) AS n FROM silver_signals").to_dicts()[0]["n"]

    # Second pass with identical input → batch IDs match → no new rows
    lake.bronze.write(
        table="signals", rows=sample_signal_rows,
        source="fx", partition_date="2026-05-20",
    )
    lake.silver.build_signals_for_date(date_iso="2026-05-20")
    lake.gold.build_daily_platform_stats(date_iso="2026-05-20")

    n2 = lake.query("SELECT COUNT(*) AS n FROM bronze_signals").to_dicts()[0]["n"]
    s2 = lake.query("SELECT COUNT(*) AS n FROM silver_signals").to_dicts()[0]["n"]

    assert n1 == n2
    assert s1 == s2


def test_multi_day_pipeline(lake: DataLake) -> None:
    """Pipeline handles two distinct partitions independently."""
    day_a_rows = [
        {
            "signal_id": f"a-{i}", "tenant_id": lake.settings.tenant_id,
            "platform": "reddit", "title": f"a-{i}",
            "url": f"https://e.com/a/{i}", "author_handle": f"u-{i}",
            "captured_at": datetime(2026, 5, 19, 10, i, tzinfo=UTC).isoformat(),
            "views": 100, "likes": 5, "comments": 1, "shares": 0, "saves": 0,
            "raw_json": {},
        }
        for i in range(3)
    ]
    day_b_rows = [
        {
            "signal_id": f"b-{i}", "tenant_id": lake.settings.tenant_id,
            "platform": "hacker_news", "title": f"b-{i}",
            "url": f"https://e.com/b/{i}", "author_handle": f"u-{i}",
            "captured_at": datetime(2026, 5, 20, 10, i, tzinfo=UTC).isoformat(),
            "views": 200, "likes": 10, "comments": 2, "shares": 0, "saves": 1,
            "raw_json": {},
        }
        for i in range(4)
    ]

    lake.bronze.write(
        table="signals", rows=day_a_rows, source="fx", partition_date="2026-05-19",
    )
    lake.bronze.write(
        table="signals", rows=day_b_rows, source="fx", partition_date="2026-05-20",
    )
    lake.silver.build_signals_for_date(date_iso="2026-05-19")
    lake.silver.build_signals_for_date(date_iso="2026-05-20")
    lake.gold.build_daily_platform_stats(date_iso="2026-05-19")
    lake.gold.build_daily_platform_stats(date_iso="2026-05-20")

    bronze_count = lake.query("SELECT COUNT(*) AS n FROM bronze_signals").to_dicts()[0]["n"]
    assert bronze_count == 7

    # Should be two days × one platform each
    gold_rows = lake.query(
        "SELECT dt, platform, signal_count FROM gold_daily_platform_stats "
        "ORDER BY dt, platform"
    ).to_dicts()
    assert len(gold_rows) >= 2
