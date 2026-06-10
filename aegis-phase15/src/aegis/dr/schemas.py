"""
aegis.dr.schemas
================
Pydantic v2 **frozen** models for Phase 15 — Disaster Recovery.

All models are immutable after construction (model_config frozen=True).
All datetime fields are timezone-aware UTC (enforced by validator).

Architecture position: shared across backup, restore, drill, health, CLI, API.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BackupTarget(str, Enum):
    POSTGRES = "postgres"
    REDIS = "redis"
    CHROMADB = "chromadb"
    MODELS = "models"
    RESTIC = "restic"
    MINIO = "minio"


class BackupStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class RestoreStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class DrillOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"


class SlaStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


class FailureModeCategory(str, Enum):
    PG_CORRUPTION = "pg_corruption"
    REDIS_OOM = "redis_oom"
    DISK_FULL = "disk_full"
    DOCKER_DEAD = "docker_dead"
    LAPTOP_STOLEN = "laptop_stolen"
    NETWORK_OUTAGE = "network_outage"
    WSL_CRASH = "wsl_crash"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class BackupManifest(BaseModel):
    """Immutable record of a completed backup operation."""

    model_config = ConfigDict(frozen=True)

    manifest_id: UUID = Field(default_factory=uuid4)
    target: BackupTarget
    status: BackupStatus
    started_at: datetime
    finished_at: datetime
    size_bytes: int = Field(ge=0)
    checksum_sha256: str
    minio_path: str
    pg_lsn: str | None = None          # PostgreSQL WAL LSN at dump time
    row_count: int | None = None       # Approximate signal row count (PG only)
    error_code: str | None = None
    error_detail: str | None = None
    schema_version: str = "1.0"

    @field_validator("started_at", "finished_at", mode="before")
    @classmethod
    def _ensure_utc(cls, v: Any) -> datetime:
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if isinstance(v, datetime) and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware UTC")
        return v

    @model_validator(mode="after")
    def _finished_after_started(self) -> BackupManifest:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must be >= started_at")
        return self

    @property
    def duration_s(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    def content_hash(self) -> str:
        """Deterministic hash over immutable fields — used as idempotency key."""
        payload = json.dumps(
            {
                "target": self.target.value,
                "minio_path": self.minio_path,
                "checksum_sha256": self.checksum_sha256,
                "started_at": self.started_at.isoformat(),
            },
            sort_keys=True,
        ).encode()
        return hashlib.sha256(payload).hexdigest()


class RestoreResult(BaseModel):
    """Immutable record of a restore operation."""

    model_config = ConfigDict(frozen=True)

    restore_id: UUID = Field(default_factory=uuid4)
    target: BackupTarget
    status: RestoreStatus
    manifest_id: UUID
    started_at: datetime
    finished_at: datetime
    rows_restored: int | None = None
    verification_passed: bool = False
    error_code: str | None = None
    error_detail: str | None = None

    @field_validator("started_at", "finished_at", mode="before")
    @classmethod
    def _ensure_utc(cls, v: Any) -> datetime:
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if isinstance(v, datetime) and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware UTC")
        return v

    @property
    def duration_s(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


class DrillResult(BaseModel):
    """Immutable record of a full restore-drill run."""

    model_config = ConfigDict(frozen=True)

    drill_id: UUID = Field(default_factory=uuid4)
    triggered_at: datetime
    completed_at: datetime | None = None
    outcome: DrillOutcome
    rto_actual_s: float
    rpo_actual_s: float
    targets_tested: list[BackupTarget]
    restore_results: list[RestoreResult]
    rto_target_s: int
    rpo_target_s: int
    rto_met: bool
    rpo_met: bool
    notes: str = ""

    @field_validator("triggered_at", "completed_at", mode="before")
    @classmethod
    def _ensure_utc(cls, v: Any) -> datetime | None:
        if v is None:
            return v
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if isinstance(v, datetime) and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware UTC")
        return v

    @property
    def passed(self) -> bool:
        return self.outcome == DrillOutcome.PASS and self.rto_met and self.rpo_met


class SlaSnapshot(BaseModel):
    """Point-in-time SLA health across all backup targets."""

    model_config = ConfigDict(frozen=True)

    captured_at: datetime
    overall_status: SlaStatus
    rpo_status: SlaStatus
    rto_status: SlaStatus
    last_backup_ages_s: dict[str, float]     # target → age in seconds
    last_drill_outcome: DrillOutcome | None
    last_drill_at: datetime | None
    next_drill_due_at: datetime | None
    active_alerts: list[str] = Field(default_factory=list)

    @field_validator("captured_at", "last_drill_at", "next_drill_due_at", mode="before")
    @classmethod
    def _ensure_utc(cls, v: Any) -> datetime | None:
        if v is None:
            return v
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if isinstance(v, datetime) and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware UTC")
        return v


class FailureMode(BaseModel):
    """Describes a known failure mode and its automated recovery plan."""

    model_config = ConfigDict(frozen=True)

    category: FailureModeCategory
    title: str
    description: str
    detection_signals: list[str]
    automated_steps: list[str]
    manual_steps: list[str]
    expected_rto_s: int
    severity: str          # "critical" | "high" | "medium"
    runbook_path: str      # relative path inside docs/


class RecoveryPlan(BaseModel):
    """Execution plan generated for a specific failure event."""

    model_config = ConfigDict(frozen=True)

    plan_id: UUID = Field(default_factory=uuid4)
    created_at: datetime
    failure_mode: FailureModeCategory
    steps: list[str]
    estimated_rto_s: int
    requires_human: bool
    human_escalation_reason: str | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def _ensure_utc(cls, v: Any) -> datetime:
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if isinstance(v, datetime) and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware UTC")
        return v


class BackupJobState(BaseModel):
    """Mutable live state for a running backup job (NOT frozen — updated in place)."""

    model_config = ConfigDict(frozen=False)

    job_id: UUID = Field(default_factory=uuid4)
    target: BackupTarget
    status: BackupStatus = BackupStatus.PENDING
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    bytes_written: int = 0
    last_heartbeat: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None

    def touch(self) -> None:
        """Update heartbeat timestamp."""
        object.__setattr__(self, "last_heartbeat", datetime.now(UTC))
