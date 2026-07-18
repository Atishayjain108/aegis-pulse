"""Parquet encode/decode + write_batch idempotency tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from aegis.datalake.constants import BRONZE
from aegis.datalake.errors import ParquetWriteError
from aegis.datalake.schemas import IngestBatch
from aegis.datalake.storage.backend import LocalStorageBackend
from aegis.datalake.storage.parquet import (
    list_manifests,
    parquet_bytes_to_rows,
    plan_file_path,
    plan_manifest_path,
    plan_partition_path,
    read_manifest,
    rows_to_parquet_bytes,
    write_batch,
)


class TestPlanPaths:
    def test_partition_path(self) -> None:
        path = plan_partition_path(
            layer="bronze", table="signals", partition_key="dt=2026-05-20",
            tenant_id="00000000-0000-0000-0000-000000000001",
        )
        assert "bronze/signals/dt=2026-05-20" in path
        assert "tenant_id=" in path

    def test_file_path_includes_batch_id(self) -> None:
        p = plan_file_path(
            layer="bronze", table="signals", partition_key="dt=2026-05-20",
            tenant_id="t", batch_id="abc123",
        )
        assert "abc123.parquet" in p

    def test_manifest_path_includes_batch_id(self) -> None:
        p = plan_manifest_path(
            layer="bronze", table="signals", partition_key="dt=2026-05-20",
            tenant_id="t", batch_id="abc123",
        )
        assert "abc123" in p
        assert p.endswith("manifest.json") or "_manifest" in p

    def test_partition_path_requires_hive_style_key(self) -> None:
        with pytest.raises(ValueError):
            plan_partition_path(
                layer="bronze", table="signals",
                partition_key="2026-05-20",  # missing "dt=" prefix
                tenant_id="t",
            )

    def test_partition_path_rejects_invalid_layer(self) -> None:
        with pytest.raises(ValueError):
            plan_partition_path(
                layer="platinum", table="signals",
                partition_key="dt=2026-05-20", tenant_id="t",
            )


class TestRowsToParquetBytes:
    def test_empty_rows_returns_some_bytes(self) -> None:
        b = rows_to_parquet_bytes([])
        assert isinstance(b, bytes)
        # Empty parquet file is still a valid file with footer/metadata.
        assert len(b) > 0

    def test_simple_roundtrip(self) -> None:
        rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        data = rows_to_parquet_bytes(rows)
        back = parquet_bytes_to_rows(data)
        assert back == rows

    def test_datetime_serialised_as_iso(self) -> None:
        rows = [{"t": datetime(2026, 5, 20, tzinfo=UTC), "v": 1}]
        data = rows_to_parquet_bytes(rows)
        back = parquet_bytes_to_rows(data)
        assert back[0]["t"].startswith("2026-05-20")
        assert back[0]["v"] == 1

    def test_dict_value_serialised_as_json_string(self) -> None:
        """Regression: empty/heterogeneous dicts can't be Arrow struct-encoded."""
        rows = [{"id": 1, "raw_json": {}}, {"id": 2, "raw_json": {"k": "v"}}]
        # Must NOT raise — Bronze writes are schema-on-read tolerant.
        data = rows_to_parquet_bytes(rows)
        back = parquet_bytes_to_rows(data)
        assert back[0]["id"] == 1
        # The value is a JSON string at rest.
        assert isinstance(back[0]["raw_json"], str)
        assert isinstance(back[1]["raw_json"], str)

    def test_list_value_serialised_as_json_string(self) -> None:
        rows = [{"id": 1, "tags": ["a", "b", "c"]}]
        data = rows_to_parquet_bytes(rows)
        back = parquet_bytes_to_rows(data)
        assert isinstance(back[0]["tags"], str)
        assert "a" in back[0]["tags"]

    def test_invalid_input_raises_parquet_write_error(self) -> None:
        """Truly un-encodable values should raise our typed error."""
        # An object pyarrow can't infer a type for (e.g. an opaque class).
        class _Opaque:
            pass

        rows = [{"id": 1, "obj": _Opaque()}]
        with pytest.raises(ParquetWriteError):
            rows_to_parquet_bytes(rows)


class TestWriteBatch:
    def _batch(self, rows: list[dict]) -> IngestBatch:
        return IngestBatch(
            table_name="signals",
            layer=BRONZE,
            tenant_id="00000000-0000-0000-0000-000000000001",
            partition_key="dt=2026-05-20",
            source="fixture",
            rows=tuple(rows),
        )

    def test_write_returns_manifest(self, tmp_root: str) -> None:
        b = LocalStorageBackend(Path(tmp_root))
        rows = [{"id": 1, "v": "x"}, {"id": 2, "v": "y"}]
        m = write_batch(backend=b, batch=self._batch(rows), source="fixture")
        assert m.row_count == 2
        assert m.byte_size > 0
        assert m.sha256
        assert m.batch_id

    def test_idempotency_same_input_same_batch_id(self, tmp_root: str) -> None:
        b = LocalStorageBackend(Path(tmp_root))
        rows = [{"id": 1, "v": "x"}]
        m1 = write_batch(backend=b, batch=self._batch(rows), source="fixture")
        m2 = write_batch(backend=b, batch=self._batch(rows), source="fixture")
        assert m1.batch_id == m2.batch_id

    def test_idempotency_different_data_different_id(self, tmp_root: str) -> None:
        b = LocalStorageBackend(Path(tmp_root))
        m1 = write_batch(
            backend=b, batch=self._batch([{"id": 1}]), source="fixture",
        )
        m2 = write_batch(
            backend=b, batch=self._batch([{"id": 2}]), source="fixture",
        )
        assert m1.batch_id != m2.batch_id


class TestReadAndListManifests:
    def test_read_manifest_after_write(self, tmp_root: str) -> None:
        b = LocalStorageBackend(Path(tmp_root))
        rows = [{"id": 1, "v": "x"}]
        batch = IngestBatch(
            table_name="signals", layer=BRONZE, tenant_id="t1",
            partition_key="dt=2026-05-20", source="fx", rows=tuple(rows),
        )
        m = write_batch(backend=b, batch=batch, source="fx")
        # Read back
        m2 = read_manifest(backend=b, manifest_path=plan_manifest_path(
            layer=BRONZE, table="signals", partition_key="dt=2026-05-20",
            tenant_id="t1", batch_id=m.batch_id,
        ))
        assert m2.batch_id == m.batch_id

    def test_list_manifests_finds_written_files(self, tmp_root: str) -> None:
        b = LocalStorageBackend(Path(tmp_root))
        rows = [{"id": 1}]
        batch = IngestBatch(
            table_name="signals", layer=BRONZE, tenant_id="t1",
            partition_key="dt=2026-05-20", source="fx", rows=tuple(rows),
        )
        write_batch(backend=b, batch=batch, source="fx")
        manifests = list_manifests(backend=b, layer=BRONZE, table="signals")
        assert len(manifests) >= 1
