"""
aegis.dr.constants
==================
Named constants for Phase 15 — Disaster Recovery & Business Continuity.

All time values are in seconds unless the name contains a unit suffix.
All size values are in bytes unless the name contains a unit suffix.

Architecture position: imported by every DR module; no aegis.* imports here
to avoid circular dependencies.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Phase identity
# ---------------------------------------------------------------------------
PHASE: str = "phase15"
VERSION: str = "0.15.0"

# ---------------------------------------------------------------------------
# SLA targets (RPO / RTO)
# ---------------------------------------------------------------------------
RPO_TARGET_S: int = 900          # 15 minutes — maximum tolerable data loss window
RTO_TARGET_S: int = 3_600        # 60 minutes — maximum tolerable recovery time
DRILL_INTERVAL_S: int = 604_800  # 7 days — weekly restore drill cadence

# ---------------------------------------------------------------------------
# Backup schedule & retention
# ---------------------------------------------------------------------------
PG_BACKUP_INTERVAL_S: int = 900           # pgdump every 15 min (matches RPO target)
REDIS_BACKUP_INTERVAL_S: int = 300        # Redis BGSAVE every 5 min
CHROMADB_BACKUP_INTERVAL_S: int = 3_600   # ChromaDB export every 1 h
MODEL_BACKUP_INTERVAL_S: int = 3_600      # Model registry every 1 h

RETENTION_DAYS_HOT: int = 7               # Days kept in primary MinIO bucket
RETENTION_DAYS_COLD: int = 30             # Days kept in secondary/Backblaze bucket
MAX_PG_SNAPSHOTS_HOT: int = 14            # Max PG dump files in hot tier
MAX_MODEL_VERSIONS_HOT: int = 5           # Keep last N model versions always hot

# ---------------------------------------------------------------------------
# MinIO / S3 layout
# ---------------------------------------------------------------------------
DR_BUCKET: str = "aegis-dr"
PG_PREFIX: str = "postgres"
REDIS_PREFIX: str = "redis"
CHROMADB_PREFIX: str = "chromadb"
MODELS_PREFIX: str = "models"
RESTIC_PREFIX: str = "restic"
MANIFESTS_PREFIX: str = "manifests"

OBJECT_LOCK_RETENTION_DAYS: int = 30      # WORM lock for audit-log objects

# ---------------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------------
CHECKSUM_ALGO: str = "sha256"
MANIFEST_VERSION: str = "1.0"
HMAC_ALGO: str = "sha256"

# ---------------------------------------------------------------------------
# Postgres backup
# ---------------------------------------------------------------------------
PG_DUMP_FORMAT: str = "custom"            # pg_dump -Fc — allows parallel restore
PG_DUMP_COMPRESS: int = 6                 # zstd level (1=fast, 9=best)
PG_RESTORE_JOBS: int = 4                  # parallel restore workers
PG_CONNECT_TIMEOUT_S: int = 10
PG_DUMP_TIMEOUT_S: int = 600              # 10 min max for pg_dump
PG_VERIFY_QUERY: str = "SELECT COUNT(*) FROM signals"

# ---------------------------------------------------------------------------
# Redis backup
# ---------------------------------------------------------------------------
REDIS_BGSAVE_POLL_INTERVAL_S: float = 0.5
REDIS_BGSAVE_TIMEOUT_S: int = 120
REDIS_RDB_FILENAME: str = "dump.rdb"
REDIS_UPLOAD_CHUNK_MB: int = 16

# ---------------------------------------------------------------------------
# restic
# ---------------------------------------------------------------------------
RESTIC_COMPRESSION: str = "auto"          # restic ≥ 0.14 supports auto
RESTIC_PACK_SIZE_MB: int = 128
RESTIC_KEEP_DAILY: int = 7
RESTIC_KEEP_WEEKLY: int = 4
RESTIC_KEEP_MONTHLY: int = 3

# ---------------------------------------------------------------------------
# Drill validation thresholds
# ---------------------------------------------------------------------------
DRILL_PG_ROW_TOLERANCE: float = 0.001    # Allow 0.1% row count discrepancy
DRILL_REDIS_KEY_TOLERANCE: float = 0.05  # Allow 5% key-count discrepancy (TTL expirations)
DRILL_MAX_DURATION_S: int = 1800         # Drill must complete within 30 min

# ---------------------------------------------------------------------------
# Health monitoring
# ---------------------------------------------------------------------------
HEALTH_STALE_BACKUP_WARN_S: int = 1_800  # Warn if last backup older than 30 min
HEALTH_STALE_BACKUP_CRIT_S: int = 3_600  # Critical if older than 1 h
HEALTH_CHECK_INTERVAL_S: int = 60

# ---------------------------------------------------------------------------
# Error codes  AEGIS-DR-0001..0030
# ---------------------------------------------------------------------------
ERR_BACKUP_PG_FAILED: str = "AEGIS-DR-0001"
ERR_BACKUP_REDIS_FAILED: str = "AEGIS-DR-0002"
ERR_BACKUP_MINIO_FAILED: str = "AEGIS-DR-0003"
ERR_BACKUP_CHROMADB_FAILED: str = "AEGIS-DR-0004"
ERR_BACKUP_MODELS_FAILED: str = "AEGIS-DR-0005"
ERR_BACKUP_RESTIC_FAILED: str = "AEGIS-DR-0006"

ERR_RESTORE_PG_FAILED: str = "AEGIS-DR-0010"
ERR_RESTORE_REDIS_FAILED: str = "AEGIS-DR-0011"
ERR_RESTORE_CHROMADB_FAILED: str = "AEGIS-DR-0012"
ERR_RESTORE_MODELS_FAILED: str = "AEGIS-DR-0013"
ERR_RESTORE_VERIFICATION_FAILED: str = "AEGIS-DR-0014"

ERR_DRILL_TIMEOUT: str = "AEGIS-DR-0020"
ERR_DRILL_VERIFICATION_FAILED: str = "AEGIS-DR-0021"
ERR_DRILL_NO_BACKUP_FOUND: str = "AEGIS-DR-0022"

ERR_RPO_BREACH: str = "AEGIS-DR-0025"
ERR_RTO_BREACH: str = "AEGIS-DR-0026"

ERR_MANIFEST_CORRUPT: str = "AEGIS-DR-0030"
