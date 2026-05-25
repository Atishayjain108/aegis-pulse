"""LakeCatalog (SQLite registry) tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aegis.datalake.catalog.registry import (
    LakeCatalog,
    LineageEdge,
    PartitionInfo,
)
from aegis.datalake.errors import TableAlreadyExistsError, TableNotFoundError


def _partition(
    *, table: str = "signals", layer: str = "bronze",
    partition_key: str = "dt=2026-05-20",
    tenant_id: str = "00000000-0000-0000-0000-000000000001",
    batch_id: str = "abc",
    rows: int = 10,
) -> PartitionInfo:
    return PartitionInfo(
        table_name=table,
        layer=layer,
        partition_key=partition_key,
        tenant_id=tenant_id,
        batch_id=batch_id,
        file_path=f"{layer}/{table}/{partition_key}/{batch_id}.parquet",
        manifest_path=f"{layer}/{table}/{partition_key}/{batch_id}_manifest.json",
        row_count=rows,
        byte_size=1024,
        sha256="0" * 64,
        written_at=datetime(2026, 5, 20, tzinfo=UTC),
    )


class TestRegisterTable:
    def test_register_then_get(self, tmp_catalog: LakeCatalog) -> None:
        info = tmp_catalog.register_table(
            name="signals", layer="bronze", schema_json="{}",
        )
        assert info.name == "signals"
        assert info.layer == "bronze"
        got = tmp_catalog.get_table("signals", "bronze")
        assert got.name == "signals"

    def test_register_duplicate_default_error(
        self, tmp_catalog: LakeCatalog
    ) -> None:
        tmp_catalog.register_table(name="signals", layer="bronze", schema_json="{}")
        with pytest.raises(TableAlreadyExistsError):
            tmp_catalog.register_table(
                name="signals", layer="bronze", schema_json="{}",
                if_exists="error",
            )

    def test_register_duplicate_with_skip(
        self, tmp_catalog: LakeCatalog
    ) -> None:
        first = tmp_catalog.register_table(
            name="signals", layer="bronze", schema_json="{}",
        )
        second = tmp_catalog.register_table(
            name="signals", layer="bronze", schema_json="{}", if_exists="skip",
        )
        assert first.name == second.name

    def test_register_duplicate_with_replace(
        self, tmp_catalog: LakeCatalog
    ) -> None:
        tmp_catalog.register_table(
            name="signals", layer="bronze", schema_json="{}",
            description="old",
        )
        new = tmp_catalog.register_table(
            name="signals", layer="bronze", schema_json="{}",
            description="new", if_exists="replace",
        )
        assert new.description == "new"

    def test_get_missing_raises(self, tmp_catalog: LakeCatalog) -> None:
        with pytest.raises(TableNotFoundError):
            tmp_catalog.get_table("nope", "bronze")

    def test_list_tables_no_filter(self, tmp_catalog: LakeCatalog) -> None:
        tmp_catalog.register_table(name="t1", layer="bronze", schema_json="{}")
        tmp_catalog.register_table(name="t2", layer="silver", schema_json="{}")
        tmp_catalog.register_table(name="t3", layer="gold", schema_json="{}")
        all_tables = tmp_catalog.list_tables()
        assert len(all_tables) == 3

    def test_list_tables_filtered(self, tmp_catalog: LakeCatalog) -> None:
        tmp_catalog.register_table(name="t1", layer="bronze", schema_json="{}")
        tmp_catalog.register_table(name="t2", layer="silver", schema_json="{}")
        silver = tmp_catalog.list_tables(layer="silver")
        assert len(silver) == 1
        assert silver[0].name == "t2"


class TestPartitions:
    def test_record_then_list(self, tmp_catalog: LakeCatalog) -> None:
        tmp_catalog.register_table(name="signals", layer="bronze", schema_json="{}")
        tmp_catalog.record_partition(_partition(batch_id="b1"))
        tmp_catalog.record_partition(
            _partition(batch_id="b2", partition_key="dt=2026-05-21")
        )
        parts = tmp_catalog.list_partitions(table_name="signals", layer="bronze")
        assert len(parts) == 2

    def test_record_is_upsert(self, tmp_catalog: LakeCatalog) -> None:
        tmp_catalog.register_table(name="signals", layer="bronze", schema_json="{}")
        tmp_catalog.record_partition(_partition(batch_id="b1", rows=10))
        tmp_catalog.record_partition(_partition(batch_id="b1", rows=999))
        parts = tmp_catalog.list_partitions(table_name="signals", layer="bronze")
        assert len(parts) == 1
        assert parts[0].row_count == 999

    def test_partition_count(self, tmp_catalog: LakeCatalog) -> None:
        tmp_catalog.register_table(name="signals", layer="bronze", schema_json="{}")
        for i in range(3):
            tmp_catalog.record_partition(
                _partition(batch_id=f"b{i}", partition_key=f"dt=2026-05-{20 + i}")
            )
        assert tmp_catalog.partition_count(table_name="signals", layer="bronze") == 3

    def test_total_rows(self, tmp_catalog: LakeCatalog) -> None:
        tmp_catalog.register_table(name="signals", layer="bronze", schema_json="{}")
        tmp_catalog.record_partition(_partition(batch_id="b1", rows=10))
        tmp_catalog.record_partition(
            _partition(batch_id="b2", partition_key="dt=2026-05-21", rows=20)
        )
        assert tmp_catalog.total_rows(table_name="signals", layer="bronze") == 30


class TestLineage:
    def test_record_and_list(self, tmp_catalog: LakeCatalog) -> None:
        e = LineageEdge(
            upstream_table="signals", upstream_layer="bronze",
            downstream_table="signals", downstream_layer="silver",
            transform="silver_signals_build",
        )
        tmp_catalog.record_lineage(e)
        edges = tmp_catalog.list_lineage()
        assert any(
            x.upstream_table == "signals" and x.downstream_layer == "silver"
            for x in edges
        )


class TestDropTable:
    def test_drop_table_removes_record(self, tmp_catalog: LakeCatalog) -> None:
        tmp_catalog.register_table(name="signals", layer="bronze", schema_json="{}")
        dropped = tmp_catalog.drop_table("signals", "bronze")
        assert dropped is True
        with pytest.raises(TableNotFoundError):
            tmp_catalog.get_table("signals", "bronze")

    def test_drop_missing_returns_false(self, tmp_catalog: LakeCatalog) -> None:
        result = tmp_catalog.drop_table("nope", "bronze")
        assert result is False
