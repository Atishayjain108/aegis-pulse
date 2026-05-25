"""Retention enforcer tests."""

from __future__ import annotations

import pytest

from aegis.datalake.constants import BRONZE, SILVER
from aegis.datalake.errors import ConfigurationError
from aegis.datalake.facade import DataLake
from aegis.datalake.retention import RetentionEnforcer, RetentionPlan


def _build_enforcer(lake: DataLake) -> RetentionEnforcer:
    return RetentionEnforcer(
        settings=lake.settings,
        backend=lake.backend,
        catalog=lake.catalog,
    )


class TestPlan:
    def test_empty_lake(self, lake: DataLake) -> None:
        enforcer = _build_enforcer(lake)
        plan = enforcer.plan(SILVER)
        assert plan.layer == SILVER
        assert plan.total_partitions == 0

    def test_invalid_layer_raises(self, lake: DataLake) -> None:
        enforcer = _build_enforcer(lake)
        with pytest.raises(ConfigurationError):
            enforcer.plan("platinum")

    def test_bronze_with_no_policy_is_empty(self, lake: DataLake) -> None:
        enforcer = _build_enforcer(lake)
        plan = enforcer.plan(BRONZE)
        # No bronze policy + no override → empty plan
        assert plan.total_partitions == 0
        assert plan.cutoff_date == ""

    def test_bronze_with_override_computes_cutoff(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        # Write a bronze partition for old date
        lake.bronze.write(
            table="signals", rows=sample_signal_rows[:3],
            source="fx", partition_date="2024-01-01",
        )
        enforcer = _build_enforcer(lake)
        plan = enforcer.plan(BRONZE, bronze_retention_days=30)
        # Old partition should be in plan
        assert plan.total_partitions >= 1
        assert "signals" in plan.table_to_partitions

    def test_silver_retention_with_old_data(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        # Settings default silver retention is 365 days; old date should match
        lake.bronze.write(
            table="signals", rows=sample_signal_rows[:3],
            source="fx", partition_date="2020-01-01",
        )
        lake.silver.build_signals_for_date(date_iso="2020-01-01")
        enforcer = _build_enforcer(lake)
        plan = enforcer.plan(SILVER)
        assert plan.total_partitions >= 1


class TestApply:
    def test_dry_run_does_not_delete(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows[:3],
            source="fx", partition_date="2020-01-01",
        )
        lake.silver.build_signals_for_date(date_iso="2020-01-01")
        enforcer = _build_enforcer(lake)
        plan = enforcer.plan(SILVER)
        result = enforcer.apply(plan, dry_run=True)
        assert result.partitions_deleted == plan.total_partitions
        # Catalog should still have the partitions
        remaining = lake.catalog.list_partitions(
            table_name="signals", layer=SILVER
        )
        assert len(remaining) >= 1

    def test_apply_real_deletes_storage_and_catalog(
        self, lake: DataLake, sample_signal_rows: list[dict]
    ) -> None:
        lake.bronze.write(
            table="signals", rows=sample_signal_rows[:3],
            source="fx", partition_date="2020-01-01",
        )
        lake.silver.build_signals_for_date(date_iso="2020-01-01")
        before = lake.catalog.list_partitions(
            table_name="signals", layer=SILVER
        )
        assert len(before) >= 1

        enforcer = _build_enforcer(lake)
        plan = enforcer.plan(SILVER)
        result = enforcer.apply(plan, dry_run=False)
        assert result.partitions_deleted >= 1
        # Catalog should now have one fewer
        after = lake.catalog.list_partitions(
            table_name="signals", layer=SILVER
        )
        assert len(after) < len(before)

    def test_apply_empty_plan_returns_zeros(self, lake: DataLake) -> None:
        enforcer = _build_enforcer(lake)
        plan = RetentionPlan(layer=SILVER, cutoff_date="")
        result = enforcer.apply(plan)
        assert result.partitions_deleted == 0
