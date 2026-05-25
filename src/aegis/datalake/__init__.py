"""AEGIS Pulse — Phase 10: Data Lake & Analytics.

Medallion-architecture data lake providing Bronze (raw immutable),
Silver (cleaned + conformed), and Gold (business-ready aggregates) layers
over Parquet on MinIO with DuckDB as the federated query engine.

Design principles (consistent with Phases 0-4):
  - Heuristic-first: every transformation is deterministic. No ML in the data layer.
  - Graceful degradation: missing optional deps (duckdb, pyarrow, boto3) never break import.
  - Idempotent writes: every batch is content-addressable; reruns are safe.
  - Zero-cost: MinIO + DuckDB + Parquet. No paid services required.
  - Audit-first: every write produces a manifest with sha256 + row counts + lineage.

Public surface:
  - DataLake       : top-level facade
  - BronzeWriter   : raw signal/prediction/alert ingestion
  - SilverBuilder  : cleaning + conformance
  - GoldAggregator : business-facing rollups
  - LakeCatalog    : table registry + partition tracking
  - QualityGate    : pandera-based row validation

Module is import-safe with zero side effects.
"""

from __future__ import annotations

__all__ = [
    "FEATURE_FLAGS",
    "PHASE",
    "VERSION",
    "BronzeWriter",
    "DataLake",
    "GoldAggregator",
    "LakeCatalog",
    "QualityGate",
    "SilverBuilder",
]

PHASE: str = "phase10"
VERSION: str = "0.10.0"

# Feature flags — every optional capability is gated and discoverable.
FEATURE_FLAGS: dict[str, bool] = {
    "iceberg_compatible_layout": True,
    "duckdb_query_engine": True,
    "prefect_orchestration": True,
    "dbt_transformations": True,
    "pandera_quality_gate": True,
    "minio_object_lock": False,  # opt-in for production
    "clickhouse_sink": False,  # opt-in upgrade path
}


def _lazy_imports() -> None:
    """Defer heavy imports to first use.

    The public classes are re-exported through __getattr__ to keep
    `import aegis.datalake` fast (< 5 ms) even when the lake is unused.
    """


def __getattr__(name: str) -> object:
    """Resolve heavy classes lazily on first attribute access."""
    if name == "DataLake":
        from aegis.datalake.facade import DataLake

        return DataLake
    if name == "BronzeWriter":
        from aegis.datalake.bronze.writer import BronzeWriter

        return BronzeWriter
    if name == "SilverBuilder":
        from aegis.datalake.silver.builder import SilverBuilder

        return SilverBuilder
    if name == "GoldAggregator":
        from aegis.datalake.gold.aggregator import GoldAggregator

        return GoldAggregator
    if name == "LakeCatalog":
        from aegis.datalake.catalog.registry import LakeCatalog

        return LakeCatalog
    if name == "QualityGate":
        from aegis.datalake.quality.gate import QualityGate

        return QualityGate
    raise AttributeError(f"module 'aegis.datalake' has no attribute {name!r}")
