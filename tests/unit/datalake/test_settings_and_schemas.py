"""Settings + schemas tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from aegis.datalake.schemas import (
    BronzeAlert,
    BronzePrediction,
    BronzeSignal,
    IngestBatch,
    SilverSignal,
    WriteManifest,
)
from aegis.datalake.settings import DataLakeSettings


class TestDataLakeSettings:
    def test_defaults_exist(self, tmp_root: str) -> None:
        s = DataLakeSettings(
            use_local_filesystem=True,
            local_root=Path(tmp_root),
            catalog_db_path=Path(tmp_root) / "c.db",
        )
        assert s.bucket
        assert s.tenant_id

    def test_invalid_log_level_rejected(self, tmp_root: str) -> None:
        with pytest.raises(ValidationError):
            DataLakeSettings(
                use_local_filesystem=True,
                local_root=Path(tmp_root),
                catalog_db_path=Path(tmp_root) / "c.db",
                log_level="WHISPER",
            )

    def test_log_level_case_insensitive(self, tmp_root: str) -> None:
        s = DataLakeSettings(
            use_local_filesystem=True,
            local_root=Path(tmp_root),
            catalog_db_path=Path(tmp_root) / "c.db",
            log_level="debug",
        )
        assert s.log_level.upper() == "DEBUG"

    def test_ensure_local_dirs_idempotent(self, tmp_root: str) -> None:
        catalog = Path(tmp_root) / "subdir" / "c.db"
        s = DataLakeSettings(
            use_local_filesystem=True,
            local_root=Path(tmp_root),
            catalog_db_path=catalog,
        )
        s.ensure_local_dirs()
        s.ensure_local_dirs()  # idempotent
        assert catalog.parent.is_dir()


class TestIngestBatch:
    def test_batch_id_is_deterministic(self) -> None:
        rows = ({"id": 1}, {"id": 2})
        b1 = IngestBatch(
            table_name="t", layer="bronze", tenant_id="x",
            partition_key="dt=2026-05-20", source="s", rows=rows,
        )
        b2 = IngestBatch(
            table_name="t", layer="bronze", tenant_id="x",
            partition_key="dt=2026-05-20", source="s", rows=rows,
        )
        assert b1.batch_id == b2.batch_id

    def test_batch_id_differs_for_different_rows(self) -> None:
        b1 = IngestBatch(
            table_name="t", layer="bronze", tenant_id="x",
            partition_key="dt=2026-05-20", source="s",
            rows=({"id": 1},),
        )
        b2 = IngestBatch(
            table_name="t", layer="bronze", tenant_id="x",
            partition_key="dt=2026-05-20", source="s",
            rows=({"id": 2},),
        )
        assert b1.batch_id != b2.batch_id


class TestWriteManifest:
    def _manifest(self) -> WriteManifest:
        return WriteManifest(
            table_name="signals", layer="bronze",
            partition_key="dt=2026-05-20", tenant_id="t",
            row_count=10, byte_size=512, sha256="0" * 64,
            written_at=datetime(2026, 5, 20, tzinfo=UTC),
            file_paths=("bronze/signals/dt=2026-05-20/abc.parquet",),
            source="fixture", batch_id="abc123",
        )

    def test_construct(self) -> None:
        m = self._manifest()
        assert m.row_count == 10

    def test_is_frozen(self) -> None:
        m = self._manifest()
        with pytest.raises(Exception):
            m.row_count = 999  # type: ignore[misc]


class TestBronzeSchemasAcceptExtra:
    def test_bronze_signal_tolerates_extra_fields(self) -> None:
        # Bronze is schema-on-read tolerant — Pydantic should accept extra
        # fields without complaining.
        BronzeSignal.model_validate(
            {
                "signal_id": "x",
                "tenant_id": "t",
                "platform": "reddit",
                "captured_at": "2026-05-20T00:00:00Z",
                "extra_field_we_did_not_anticipate": "ok",
            }
        )

    def test_bronze_prediction_tolerates_extra(self) -> None:
        BronzePrediction.model_validate(
            {
                "prediction_id": "p",
                "tenant_id": "t",
                "trend_id": "tr",
                "horizon_h": 24,
                "finished_at": "2026-05-20T00:00:00Z",
                "future_field": True,
            }
        )

    def test_bronze_alert_tolerates_extra(self) -> None:
        BronzeAlert.model_validate(
            {
                "alert_id": "a",
                "tenant_id": "t",
                "trend_id": "tr",
                "verdict": "ENTER",
                "created_at": "2026-05-20T00:00:00Z",
                "anything": [1, 2],
            }
        )


class TestSilverSchemasAreStrict:
    def test_silver_signal_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            SilverSignal.model_validate(
                {
                    "signal_id": "s",
                    "tenant_id": "t",
                    "platform": "reddit",
                    "platform_tier": "TIER_1_INTENT",
                    "title": "x",
                    "url_normalized": "https://e.com/x",
                    "author_hash": None,
                    "captured_at": datetime(2026, 5, 20, tzinfo=UTC),
                    "captured_date": "2026-05-20",
                    "evil_field": "should be rejected",
                }
            )

    def test_silver_signal_accepts_valid_tier(self) -> None:
        SilverSignal.model_validate(
            {
                "signal_id": "s",
                "tenant_id": "t",
                "platform": "reddit",
                "platform_tier": "TIER_1_INTENT",
                "title": "x",
                "url_normalized": "https://e.com/x",
                "author_hash": None,
                "captured_at": datetime(2026, 5, 20, tzinfo=UTC),
                "captured_date": "2026-05-20",
            }
        )

    def test_silver_signal_rejects_invalid_tier(self) -> None:
        with pytest.raises(ValidationError):
            SilverSignal.model_validate(
                {
                    "signal_id": "s",
                    "tenant_id": "t",
                    "platform": "reddit",
                    "platform_tier": "TIER_99_INVALID",
                    "title": "x",
                    "url_normalized": "https://e.com/x",
                    "author_hash": None,
                    "captured_at": datetime(2026, 5, 20, tzinfo=UTC),
                    "captured_date": "2026-05-20",
                }
            )
