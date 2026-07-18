"""
aegis.dr — Phase 15: Disaster Recovery & Business Continuity
=============================================================

Public exports
--------------
DrOrchestrator      — start/stop all backup + drill jobs
RestoreDrill        — on-demand or scheduled restore drill
DrHealthChecker     — SLA health snapshot
BackupManifest      — immutable backup record (Pydantic v2 frozen)
DrillResult         — immutable drill outcome record
SlaSnapshot         — current RPO/RTO snapshot
get_dr_settings     — settings singleton

Integration points
------------------
* Phase 1 (Postgres/Redis): backup sources + restore targets
* Phase 3 (Model registry): model artifact backups
* Phase 4 (Alert pipeline): failure notifications via ntfy / Phase 4 notifiers
* Phase 10 (Data lake): MinIO bucket reuse
* Dashboard (Port 8300): ``/dr/*`` routes via ``aegis.dr.api.router``

RPO target: 15 minutes  |  RTO target: 60 minutes
"""

from aegis.dr.config import DisasterRecoverySettings, get_dr_settings
from aegis.dr.drill import RestoreDrill
from aegis.dr.health import DrHealthChecker
from aegis.dr.orchestrator import DrOrchestrator
from aegis.dr.schemas import (
    BackupManifest,
    BackupStatus,
    BackupTarget,
    DrillOutcome,
    DrillResult,
    FailureMode,
    FailureModeCategory,
    RecoveryPlan,
    RestoreResult,
    SlaSnapshot,
    SlaStatus,
)

PHASE: str = "phase15"
VERSION: str = "0.15.0"

__all__ = [
    "PHASE",
    "VERSION",
    "BackupManifest",
    "BackupStatus",
    "BackupTarget",
    "DisasterRecoverySettings",
    "DrHealthChecker",
    "DrOrchestrator",
    "DrillOutcome",
    "DrillResult",
    "FailureMode",
    "FailureModeCategory",
    "RecoveryPlan",
    "RestoreDrill",
    "RestoreResult",
    "SlaSnapshot",
    "SlaStatus",
    "get_dr_settings",
]
