"""
aegis.dr.health
===============
DR health checker for Phase 15 — Disaster Recovery.

Responsibilities
----------------
* Compute current RPO drift (age of last successful backup per target).
* Compute last-drill outcome and next-drill due time.
* Expose ``SlaSnapshot`` — consumed by the Dashboard API and CLI.
* Emit Prometheus metrics (graceful no-op when prometheus_client absent).
* Publish to Redis key ``aegis:dr:health`` (TTL 120s) for dashboard SSE.

Architecture
-----------
Reads from MinIO manifest objects — no dependency on backup engines at runtime.
Integrates with Phase 4 Redis (same key-space pattern as Phase 10 datalake).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from aegis.dr.config import DisasterRecoverySettings
from aegis.dr.constants import (
    HEALTH_STALE_BACKUP_CRIT_S,
    HEALTH_STALE_BACKUP_WARN_S,
    PG_PREFIX,
    REDIS_PREFIX,
)
from aegis.dr.schemas import (
    BackupManifest,
    BackupTarget,
    DrillOutcome,
    DrillResult,
    SlaSnapshot,
    SlaStatus,
)

_log = structlog.get_logger("aegis.dr.health")

# ---------------------------------------------------------------------------
# Prometheus metrics (graceful degradation)
# ---------------------------------------------------------------------------

try:
    from prometheus_client import Counter, Gauge  # type: ignore[import-untyped]

    _backup_age_gauge = Gauge(
        "aegis_dr_backup_age_seconds",
        "Age of most recent successful backup in seconds",
        ["target"],
    )
    _rpo_breach_counter = Counter(
        "aegis_dr_rpo_breach_total",
        "Number of times RPO target was breached",
    )
    _drill_pass_counter = Counter(
        "aegis_dr_drill_pass_total",
        "Number of successful restore drills",
    )
    _drill_fail_counter = Counter(
        "aegis_dr_drill_fail_total",
        "Number of failed restore drills",
    )
    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False


class DrHealthChecker:
    """
    Computes and publishes DR health state.

    Parameters
    ----------
    settings:
        DR settings singleton.
    minio_client:
        Pre-initialized ``minio.Minio`` client.
    redis_client:
        Optional async Redis client for publishing health to dashboard.
    """

    REDIS_HEALTH_KEY = "aegis:dr:health"
    REDIS_HEALTH_TTL = 120  # seconds

    def __init__(
        self,
        *,
        settings: DisasterRecoverySettings,
        minio_client: Any,
        redis_client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._minio = minio_client
        self._redis = redis_client

    async def check(self) -> SlaSnapshot:
        """
        Compute a fresh SLA snapshot.

        Returns
        -------
        SlaSnapshot
            Current RPO/RTO status across all backup targets.
        """
        now = datetime.now(UTC)
        ages: dict[str, float] = {}
        alerts: list[str] = []
        overall_rpo = SlaStatus.OK

        for target in [BackupTarget.POSTGRES, BackupTarget.REDIS]:
            manifest = await self._latest_manifest(target)
            if manifest is None:
                age_s = float("inf")
                alerts.append(f"No backup found for target={target.value}")
                overall_rpo = SlaStatus.CRITICAL
            else:
                age_s = (now - manifest.finished_at).total_seconds()

            ages[target.value] = age_s

            status = self._age_to_status(age_s)
            if status == SlaStatus.CRITICAL and overall_rpo != SlaStatus.CRITICAL:
                overall_rpo = SlaStatus.CRITICAL
            elif status == SlaStatus.WARNING and overall_rpo == SlaStatus.OK:
                overall_rpo = SlaStatus.WARNING

            if _PROM_AVAILABLE and age_s != float("inf"):
                _backup_age_gauge.labels(target=target.value).set(age_s)

            if status != SlaStatus.OK:
                alerts.append(
                    f"Backup age for {target.value} is "
                    f"{age_s:.0f}s (target ≤ {self._settings.rpo_target_s}s)"
                )

        if overall_rpo == SlaStatus.CRITICAL and _PROM_AVAILABLE:
            _rpo_breach_counter.inc()

        last_drill, last_drill_at = await self._last_drill()
        next_drill_at = (
            datetime.fromtimestamp(
                last_drill_at.timestamp() + self._settings.drill_interval_s, tz=UTC
            )
            if last_drill_at
            else None
        )

        snapshot = SlaSnapshot(
            captured_at=now,
            overall_status=overall_rpo,
            rpo_status=overall_rpo,
            rto_status=SlaStatus.OK,  # RTO assessed only during drills
            last_backup_ages_s=ages,
            last_drill_outcome=last_drill,
            last_drill_at=last_drill_at,
            next_drill_due_at=next_drill_at,
            active_alerts=alerts,
        )

        if self._redis is not None:
            await self._publish_to_redis(snapshot)

        _log.info(
            "dr.health.check",
            overall=snapshot.overall_status.value,
            alerts=alerts,
            rpo_ages_s=ages,
        )

        return snapshot

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _latest_manifest(
        self, target: BackupTarget
    ) -> BackupManifest | None:
        """Scan MinIO manifests for the most recent SUCCESS record."""
        prefix_map = {
            BackupTarget.POSTGRES: PG_PREFIX,
            BackupTarget.REDIS: REDIS_PREFIX,
        }
        prefix = prefix_map.get(target, target.value)

        try:
            objects = sorted(
                self._minio.list_objects(
                    self._settings.dr_bucket,
                    prefix=f"{prefix}/",
                    recursive=True,
                ),
                key=lambda o: o.last_modified or datetime.min,
                reverse=True,
            )
        except Exception as exc:
            _log.warning(
                "dr.health.list_objects_failed",
                target=target.value,
                error=str(exc),
            )
            return None

        for obj in objects:
            if not obj.object_name.endswith("_manifest.json"):
                continue
            try:
                response = self._minio.get_object(
                    self._settings.dr_bucket, obj.object_name
                )
                m = BackupManifest.model_validate_json(response.read())
                if m.status.value == "success":
                    return m
            except Exception as exc:
                _log.warning(
                    "dr.health.manifest_read_error",
                    obj=obj.object_name,
                    error=str(exc),
                )
                continue

        return None

    async def _last_drill(
        self,
    ) -> tuple[DrillOutcome | None, datetime | None]:
        """Read latest drill result from MinIO."""
        try:
            objects = sorted(
                self._minio.list_objects(
                    self._settings.dr_bucket,
                    prefix="drills/",
                    recursive=True,
                ),
                key=lambda o: o.last_modified or datetime.min,
                reverse=True,
            )
            for obj in objects:
                if not obj.object_name.endswith(".json"):
                    continue
                response = self._minio.get_object(
                    self._settings.dr_bucket, obj.object_name
                )
                result = DrillResult.model_validate_json(response.read())
                return result.outcome, result.triggered_at
        except Exception as exc:
            _log.debug("dr.health.no_drill_history", error=str(exc))
        return None, None

    async def _publish_to_redis(self, snapshot: SlaSnapshot) -> None:
        """Publish health snapshot to Redis for the dashboard SSE feed."""
        try:
            await self._redis.set(
                self.REDIS_HEALTH_KEY,
                snapshot.model_dump_json(),
                ex=self.REDIS_HEALTH_TTL,
            )
        except Exception as exc:
            _log.warning("dr.health.redis_publish_failed", error=str(exc))

    def _age_to_status(self, age_s: float) -> SlaStatus:
        if age_s == float("inf") or age_s > HEALTH_STALE_BACKUP_CRIT_S:
            return SlaStatus.CRITICAL
        if age_s > HEALTH_STALE_BACKUP_WARN_S:
            return SlaStatus.WARNING
        return SlaStatus.OK
