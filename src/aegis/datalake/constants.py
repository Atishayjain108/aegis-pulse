"""Constants for the Phase 10 data lake.

Every magic number in the codebase has a rationale comment here.
Tuning happens via environment variables in `aegis.datalake.settings`
which read these as defaults. Direct mutation is forbidden.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Layer names — medallion architecture
# ---------------------------------------------------------------------------
BRONZE: Final[str] = "bronze"  # raw, immutable, schema-on-read tolerant
SILVER: Final[str] = "silver"  # cleaned + conformed, strict schema
GOLD: Final[str] = "gold"  # business-ready aggregates, denormalized

VALID_LAYERS: Final[frozenset[str]] = frozenset({BRONZE, SILVER, GOLD})

# ---------------------------------------------------------------------------
# Default S3 / MinIO bucket — matches Phase 1 MinIO setup
# ---------------------------------------------------------------------------
DEFAULT_BUCKET: Final[str] = "aegis-datalake"
DEFAULT_S3_ENDPOINT: Final[str] = "http://localhost:9002"
DEFAULT_S3_REGION: Final[str] = "us-east-1"  # MinIO doesn't care, but boto3 demands one

# ---------------------------------------------------------------------------
# Parquet / write tuning
# ---------------------------------------------------------------------------
# Chosen so each Parquet file is roughly 64–128 MB after compression on
# typical signal payloads — DuckDB's sweet spot for row-group skipping.
PARQUET_ROW_GROUP_SIZE: Final[int] = 128_000
PARQUET_COMPRESSION: Final[str] = "zstd"  # better ratio than snappy, fast enough
PARQUET_COMPRESSION_LEVEL: Final[int] = 3  # zstd 3 ≈ snappy speed at ~20% smaller

# Maximum rows held in memory before flushing a bronze partition.
# At ~2 KB/row this is ~100 MB RAM ceiling per writer.
BRONZE_BATCH_MAX_ROWS: Final[int] = 50_000

# Maximum wall-clock seconds before flushing even if batch under-full.
# Bounds visibility latency for downstream silver/gold.
BRONZE_BATCH_MAX_AGE_S: Final[float] = 60.0

# ---------------------------------------------------------------------------
# Partitioning — Hive-style, daily granularity
# ---------------------------------------------------------------------------
# Hive partition key for time-based tables. UTC dates only.
TIME_PARTITION_KEY: Final[str] = "dt"

# Tenants get a partition prefix to enable Row-Level Security via path.
TENANT_PARTITION_KEY: Final[str] = "tenant_id"

# ---------------------------------------------------------------------------
# Catalog (SQLite, embedded — Hive metastore would be overkill at this scale)
# ---------------------------------------------------------------------------
DEFAULT_CATALOG_DB_PATH: Final[str] = ".aegis/datalake/catalog.sqlite3"
CATALOG_SCHEMA_VERSION: Final[int] = 1

# ---------------------------------------------------------------------------
# DuckDB
# ---------------------------------------------------------------------------
# Memory budget in MB for the embedded DuckDB engine. Conservative default
# fits comfortably alongside other AEGIS services on a 16 GB laptop.
DUCKDB_MEMORY_LIMIT_MB: Final[int] = 2_048
DUCKDB_THREAD_COUNT: Final[int] = 4
DUCKDB_TEMP_DIR_SUBPATH: Final[str] = ".aegis/datalake/duckdb_tmp"

# Query timeout — protects the dashboard ops console from runaway scans.
DEFAULT_QUERY_TIMEOUT_S: Final[float] = 30.0

# ---------------------------------------------------------------------------
# Quality gate thresholds
# ---------------------------------------------------------------------------
# Maximum fraction of rows that may fail validation before the batch is
# quarantined to a `_rejected/` prefix instead of promoted to silver.
QUALITY_MAX_REJECT_FRACTION: Final[float] = 0.05  # 5%

# Hard floor — even if reject_fraction is below the soft limit, a single
# rejected row gets logged with full row context.
QUALITY_ALWAYS_LOG_REJECTS: Final[bool] = True

# ---------------------------------------------------------------------------
# Retention policy defaults (driven by aegis.datalake.settings)
# ---------------------------------------------------------------------------
# Bronze data is kept forever by default (raw audit trail).
DEFAULT_BRONZE_RETENTION_DAYS: Final[int | None] = None

# Silver data: 365 days is the regulatory comfortable middle ground.
DEFAULT_SILVER_RETENTION_DAYS: Final[int] = 365

# Gold data: 90 days hot, then offload to compressed archive.
DEFAULT_GOLD_RETENTION_DAYS: Final[int] = 90

# ---------------------------------------------------------------------------
# Manifest / lineage
# ---------------------------------------------------------------------------
MANIFEST_FILENAME: Final[str] = "_manifest.json"
MANIFEST_SCHEMA_VERSION: Final[int] = 1

# ---------------------------------------------------------------------------
# Phase 2 stream coordinates — must match `aegis.agents.runner._publish_phase2_result`
# ---------------------------------------------------------------------------
PHASE2_STREAM_KEY: Final[str] = "aegis:phase2:graph_results"
PHASE2_STREAM_FIELD: Final[str] = "body"  # not "payload" — see CLAUDE.md gotcha
PHASE2_CONSUMER_GROUP: Final[str] = "aegis-datalake-bronze"
PHASE2_CONSUMER_NAME_DEFAULT: Final[str] = "datalake-1"
PHASE2_READ_BATCH: Final[int] = 256
PHASE2_BLOCK_MS: Final[int] = 5_000

# ---------------------------------------------------------------------------
# Swarm stream coordinates — Phase 5+
# ---------------------------------------------------------------------------
SWARM_STREAM_KEY: Final[str] = "aegis:swarm:results"
SWARM_STREAM_FIELD: Final[str] = "body"
SWARM_CONSUMER_GROUP: Final[str] = "aegis-datalake-swarm"

# ---------------------------------------------------------------------------
# Default tenant — matches Phase 4 default
# ---------------------------------------------------------------------------
DEFAULT_TENANT_ID: Final[str] = "00000000-0000-0000-0000-000000000001"

# ---------------------------------------------------------------------------
# Error codes (matches AEGIS-{COMPONENT}-NNNN scheme)
# ---------------------------------------------------------------------------
ERR_PREFIX: Final[str] = "AEGIS-DATALAKE"
