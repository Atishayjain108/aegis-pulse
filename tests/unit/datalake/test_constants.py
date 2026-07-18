"""Constants module sanity tests."""

from __future__ import annotations

from aegis.datalake import constants as C


class TestLayerConstants:
    def test_layers_distinct(self) -> None:
        assert C.BRONZE != C.SILVER != C.GOLD
        assert {C.BRONZE, C.SILVER, C.GOLD} == set(C.VALID_LAYERS)

    def test_layers_are_strings(self) -> None:
        for layer in C.VALID_LAYERS:
            assert isinstance(layer, str)
            assert layer.islower()
            assert " " not in layer


class TestPartitionConstants:
    def test_partition_keys_present(self) -> None:
        assert C.TIME_PARTITION_KEY
        assert C.TENANT_PARTITION_KEY
        assert C.TIME_PARTITION_KEY != C.TENANT_PARTITION_KEY


class TestPhase2Integration:
    def test_stream_field_is_body_not_payload(self) -> None:
        """Regression guard for the gotcha documented in CLAUDE.md."""
        assert C.PHASE2_STREAM_FIELD == "body"
        assert C.PHASE2_STREAM_FIELD != "payload"

    def test_stream_key_matches_main_repo(self) -> None:
        assert C.PHASE2_STREAM_KEY == "aegis:phase2:graph_results"

    def test_consumer_group_is_namespaced(self) -> None:
        assert C.PHASE2_CONSUMER_GROUP.startswith("aegis-")


class TestErrorCodePrefix:
    def test_error_prefix_is_namespaced(self) -> None:
        assert C.ERR_PREFIX.startswith("AEGIS")
        assert "DATALAKE" in C.ERR_PREFIX


class TestTenantConstants:
    def test_default_tenant_is_valid_uuid_shape(self) -> None:
        # Not strictly a UUID parse — just check the shape because
        # the catalog enforces this elsewhere.
        assert len(C.DEFAULT_TENANT_ID) == 36
        assert C.DEFAULT_TENANT_ID.count("-") == 4


class TestParquetConstants:
    def test_row_group_size_positive(self) -> None:
        assert C.PARQUET_ROW_GROUP_SIZE > 0

    def test_compression_is_supported(self) -> None:
        assert C.PARQUET_COMPRESSION.lower() in {"zstd", "snappy", "gzip", "none", "lz4"}
