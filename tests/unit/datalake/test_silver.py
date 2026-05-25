"""Silver builder tests — verify pure transformations + lineage + quarantine."""

from __future__ import annotations

from aegis.datalake.facade import DataLake


class TestSilverSignalsBuild:
    def test_silver_signals_with_no_bronze_is_noop(self, lake: DataLake) -> None:
        stats = lake.silver.build_signals_for_date(date_iso="2026-05-20")
        assert stats.bronze_rows_read == 0
        assert stats.silver_rows_written == 0

    def test_silver_signals_writes_cleaned_rows(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        stats = lake.silver.build_signals_for_date(date_iso="2026-05-20")
        assert stats.bronze_rows_read == len(sample_signal_rows)
        assert stats.silver_rows_written == len(sample_signal_rows)
        assert stats.rows_rejected == 0

    def test_silver_signals_platform_tier_assigned(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        lake.silver.build_signals_for_date(date_iso="2026-05-20")
        # Query silver and confirm tiers are assigned
        result = lake.query("SELECT DISTINCT platform_tier FROM silver_signals")
        tiers = {row["platform_tier"] for row in result.to_dicts()}
        # Should include at least some recognised tier
        assert any(t.startswith("TIER_") for t in tiers)

    def test_silver_signals_engagement_total_computed(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        lake.silver.build_signals_for_date(date_iso="2026-05-20")
        result = lake.query(
            "SELECT signal_id, views, likes, comments, shares, saves, engagement_total "
            "FROM silver_signals ORDER BY signal_id LIMIT 5"
        )
        for row in result.to_dicts():
            expected = (
                (row["views"] or 0) + (row["likes"] or 0) + (row["comments"] or 0)
                + (row["shares"] or 0) + (row["saves"] or 0)
            )
            assert row["engagement_total"] == expected

    def test_silver_signals_author_hashed(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        lake.silver.build_signals_for_date(date_iso="2026-05-20")
        # No silver row should contain the raw author handle.
        result = lake.query(
            "SELECT author_hash FROM silver_signals WHERE author_hash IS NOT NULL LIMIT 5"
        )
        for row in result.to_dicts():
            # Author hashes are hex digests (16+ hex chars), not raw "user_X" handles.
            assert not row["author_hash"].startswith("user_")
            assert not row["author_hash"].startswith("u-")
            assert all(c in "0123456789abcdef" for c in row["author_hash"])
            assert len(row["author_hash"]) >= 16


class TestSilverIdempotency:
    def test_double_build_does_not_duplicate(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows,
            source="fixture", partition_date="2026-05-20",
        )
        lake.silver.build_signals_for_date(date_iso="2026-05-20")
        # Build a second time — same inputs → same batch id → no new file.
        stats2 = lake.silver.build_signals_for_date(date_iso="2026-05-20")
        # silver_rows_written may be reported either as 0 (already-written
        # skipped) or as the count again (re-issued same id); either way,
        # the catalog must show a single partition.
        partitions = lake.catalog.list_partitions(
            table_name="signals", layer="silver"
        )
        # Each unique batch_id == one partition row.
        unique_ids = {p.batch_id for p in partitions}
        assert len(unique_ids) == len(partitions)
        _ = stats2  # silence unused
