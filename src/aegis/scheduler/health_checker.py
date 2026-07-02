"""Autonomous health monitoring and self-healing for AEGIS (GODMODE PASS5-5A).

Scheduler module. Runs every 5 minutes inside the autonomous loop
(``job_health_check`` in :mod:`aegis.scheduler.autonomous`). Detects
failure modes and triggers healing actions without human intervention.

Monitored conditions:
  1. Redis stream staleness — Phase 2 stream > 30 min old → emergency scrape
  2. Adapter health ratio — > 50% of swarm adapters DOWN → critical alert
  3. Model staleness — champion > 30 days old AND ≥ 100 outcomes → retrain
  4. Kill switch stuck — TRIPPED > 2 h without re-arm → critical escalation
  5. Dynamic thresholds stale — not updated in 8+ days → force update
  6. WSL clock drift — system vs Redis time > 5 s → hwclock sync
  7. Evolution loop idle — no outcomes recorded in 7 days → warning

Healing actions are fire-and-forget: they delegate to the existing
autonomous jobs (which own their resources) so a closed pool/redis at
the end of one health cycle never strands an in-flight remediation.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import structlog

_log = structlog.get_logger("aegis.scheduler.health_checker")

HealthStatus = Literal["healthy", "warning", "critical", "healing"]

# Thresholds
STREAM_STALE_S = 1800.0  # 30 minutes
ADAPTER_DOWN_WARNING_RATIO = 0.3
ADAPTER_DOWN_CRITICAL_RATIO = 0.5
MODEL_STALE_DAYS = 30
MODEL_RETRAIN_MIN_OUTCOMES = 100
KILLSWITCH_STUCK_HOURS = 2.0
THRESHOLDS_STALE_DAYS = 8
CLOCK_DRIFT_MAX_S = 5.0
EVOLUTION_IDLE_DAYS = 7
# Zero-yield: a platform that produced signals historically but none in this
# many days is silently broken (e.g. WAF-blocked commerce adapters). EMPTY is
# treated as "healthy" by the swarm pool, so without this check a dead adapter
# is indistinguishable from "no results today".
ADAPTER_ZERO_YIELD_DAYS = 3
ADAPTER_ZERO_YIELD_WARNING_COUNT = 1
ADAPTER_ZERO_YIELD_CRITICAL_COUNT = 5

# Redis keys / streams
PHASE2_STREAM = "aegis:phase2:graph_results"
HEALTH_STREAM = "aegis:core:health"
HEALTH_STREAM_MAXLEN = 288  # 24 h at 5-minute intervals
_KILLSWITCH_KEY = "aegis:execute:killswitch"
# The killswitch value is a plain "TRIPPED"/"ARMED" string with no
# timestamp, so the checker tracks when it first observed the trip.
_KILLSWITCH_SINCE_KEY = "aegis:core:health:killswitch_tripped_since"
_SWARM_HEALTH_KEY = "aegis:swarm:agent_health"
_THRESHOLDS_KEY = "aegis:core:thresholds"

# Strong references to fire-and-forget healing tasks (the event loop only
# keeps weak references — without this set a triggered retrain could be
# garbage-collected mid-flight).
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _spawn(coro: Any, name: str) -> None:
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


def _as_text(raw: Any) -> str:
    if isinstance(raw, bytes | bytearray):
        return raw.decode()
    return str(raw)


@dataclass
class HealthCheck:
    name: str
    status: HealthStatus
    message: str = ""
    value: float | None = None
    action_taken: str | None = None


@dataclass
class HealthReport:
    checks: list[HealthCheck]
    checked_at: datetime
    overall_status: HealthStatus
    healing_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at.isoformat(),
            "overall_status": self.overall_status,
            "healing_actions": self.healing_actions,
            "checks": [asdict(c) for c in self.checks],
        }


class AegisHealthChecker:
    """Runs periodic health checks and triggers healing actions.

    Usage (in autonomous.py)::

        checker = AegisHealthChecker(redis=redis_client, pool=pg_pool)
        report = await checker.check_and_heal()
    """

    def __init__(
        self,
        redis: Any | None = None,
        pool: Any | None = None,
        tenant_id: str = "00000000-0000-0000-0000-000000000001",
    ) -> None:
        self._redis = redis
        self._pool = pool
        self._tenant_id = tenant_id

    async def check_and_heal(self) -> HealthReport:
        """Run all health checks in parallel; heal critical findings."""
        names = [
            "stream_staleness",
            "adapter_health_ratio",
            "adapter_zero_yield",
            "model_staleness",
            "killswitch_state",
            "thresholds_staleness",
            "clock_drift",
            "evolution_loop_idle",
        ]
        results = await asyncio.gather(
            self._check_stream_staleness(),
            self._check_adapter_health_ratio(),
            self._check_adapter_zero_yield(),
            self._check_model_staleness(),
            self._check_killswitch_state(),
            self._check_thresholds_staleness(),
            self._check_clock_drift(),
            self._check_evolution_loop_idle(),
            return_exceptions=True,
        )

        checks: list[HealthCheck] = []
        for name, r in zip(names, results, strict=True):
            if isinstance(r, BaseException):
                checks.append(
                    HealthCheck(name=name, status="warning", message=f"Check failed: {r}")
                )
            else:
                checks.append(r)

        overall: HealthStatus = (
            "critical"
            if any(c.status == "critical" for c in checks)
            else ("warning" if any(c.status == "warning" for c in checks) else "healthy")
        )

        report = HealthReport(
            checks=checks,
            checked_at=datetime.now(UTC),
            overall_status=overall,
            healing_actions=[c.action_taken for c in checks if c.action_taken],
        )

        _log.info(
            "health_checker.complete",
            overall=overall,
            critical=[c.name for c in checks if c.status == "critical"],
            healing_actions=report.healing_actions,
        )
        return report

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    async def _check_stream_staleness(self) -> HealthCheck:
        """Phase 2 stream stale > 30 min → emergency scrape."""
        if not self._redis:
            return HealthCheck("stream_staleness", "warning", "No Redis client")
        try:
            info = await self._redis.xinfo_stream(PHASE2_STREAM)
            last_id = _as_text(info.get("last-generated-id", "0-0"))
            last_ms = int(last_id.split("-")[0]) if last_id != "0-0" else 0
            age_s = (time.time() * 1000 - last_ms) / 1000 if last_ms else 99999.0

            if age_s > STREAM_STALE_S:
                _log.warning(
                    "health.stream_stale", age_s=round(age_s), action="emergency_scrape"
                )
                try:
                    from aegis.scheduler.autonomous import job_scrape

                    _spawn(job_scrape(), "health-emergency-scrape")
                except Exception as exc:
                    _log.error("health.emergency_scrape_failed", error=str(exc))
                return HealthCheck(
                    "stream_staleness",
                    "critical",
                    f"Phase 2 stream {round(age_s / 60, 1)}m stale",
                    value=age_s,
                    action_taken="triggered_emergency_scrape",
                )
            return HealthCheck(
                "stream_staleness",
                "healthy",
                f"Last entry {round(age_s, 0)}s ago",
                value=age_s,
            )
        except Exception as exc:
            return HealthCheck("stream_staleness", "warning", str(exc))

    async def _check_adapter_health_ratio(self) -> HealthCheck:
        """Alert when > 50% of swarm adapters are quarantined (DOWN).

        Reads the persisted swarm health hash directly — the health
        checker runs in the scheduler process where no live
        ``SwarmAgentPool`` instance exists.
        """
        if not self._redis:
            return HealthCheck("adapter_health_ratio", "warning", "No Redis client")
        try:
            import json

            data = await self._redis.hgetall(_SWARM_HEALTH_KEY)
            if not data:
                return HealthCheck("adapter_health_ratio", "healthy", "No agents tracked")
            total = len(data)
            quarantined = 0
            for raw in data.values():
                try:
                    state = json.loads(_as_text(raw))
                    if state.get("health") == "DOWN":
                        quarantined += 1
                except (ValueError, TypeError):
                    continue
            ratio = quarantined / total
            status: HealthStatus = (
                "critical"
                if ratio > ADAPTER_DOWN_CRITICAL_RATIO
                else ("warning" if ratio > ADAPTER_DOWN_WARNING_RATIO else "healthy")
            )
            return HealthCheck(
                "adapter_health_ratio",
                status,
                f"{quarantined}/{total} adapters quarantined ({int(ratio * 100)}%)",
                value=ratio,
            )
        except Exception as exc:
            return HealthCheck("adapter_health_ratio", "warning", str(exc))

    async def _check_adapter_zero_yield(self) -> HealthCheck:
        """Flag platforms that produced signals historically but none recently.

        EMPTY ≠ DOWN in the swarm pool, so a WAF-blocked or broken adapter that
        silently returns 0 rows never trips ``adapter_health_ratio``. Here we
        compare each platform's freshest DB signal against now: any platform
        that was alive (has rows) but has gone quiet for
        ``ADAPTER_ZERO_YIELD_DAYS`` is a silent regression worth surfacing.
        """
        if not self._pool:
            return HealthCheck("adapter_zero_yield", "warning", "No DB pool")
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, TRUE)", self._tenant_id
                )
                rows = await conn.fetch(
                    """
                    SELECT platform::text AS platform, MAX(ts) AS latest
                    FROM signals
                    GROUP BY platform
                    HAVING MAX(ts) < NOW() - ($1 || ' days')::interval
                    ORDER BY MAX(ts)
                    """,
                    str(ADAPTER_ZERO_YIELD_DAYS),
                )
            stale = [r["platform"] for r in rows]
            n = len(stale)
            if n == 0:
                return HealthCheck(
                    "adapter_zero_yield", "healthy", "All known platforms fresh"
                )
            status: HealthStatus = (
                "critical"
                if n >= ADAPTER_ZERO_YIELD_CRITICAL_COUNT
                else ("warning" if n >= ADAPTER_ZERO_YIELD_WARNING_COUNT else "healthy")
            )
            _log.warning(
                "health.adapter_zero_yield",
                count=n,
                platforms=stale[:20],
                days=ADAPTER_ZERO_YIELD_DAYS,
            )
            return HealthCheck(
                "adapter_zero_yield",
                status,
                f"{n} platform(s) silent >{ADAPTER_ZERO_YIELD_DAYS}d: "
                f"{', '.join(stale[:10])}",
                value=float(n),
            )
        except Exception as exc:
            return HealthCheck("adapter_zero_yield", "warning", str(exc))

    async def _check_model_staleness(self) -> HealthCheck:
        """Trigger retrain if champion > 30 days old AND outcomes available."""
        if not self._pool:
            return HealthCheck("model_staleness", "warning", "No DB pool")
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, TRUE)", self._tenant_id
                )
                row = await conn.fetchrow(
                    """
                    SELECT candidate_id, test_auc,
                           COALESCE(promoted_at, created_at) AS champion_since
                    FROM model_candidates
                    WHERE is_champion = TRUE
                    ORDER BY created_at DESC LIMIT 1
                    """
                )
            if row is None:
                return HealthCheck("model_staleness", "warning", "No champion model found")

            age_days = (datetime.now(UTC) - row["champion_since"]).days
            if age_days > MODEL_STALE_DAYS:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        "SELECT set_config('app.current_tenant', $1, TRUE)", self._tenant_id
                    )
                    count = await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM prediction_outcomes
                        WHERE settlement_timestamp > NOW() - INTERVAL '30 days'
                        """
                    )
                if count is not None and count >= MODEL_RETRAIN_MIN_OUTCOMES:
                    _log.warning(
                        "health.model_stale",
                        age_days=age_days,
                        outcome_count=count,
                        action="triggering_retrain",
                    )
                    try:
                        from aegis.scheduler.autonomous import job_weekly_retrain

                        _spawn(job_weekly_retrain(), "health-stale-model-retrain")
                    except Exception as exc:
                        _log.error("health.retrain_trigger_failed", error=str(exc))
                    return HealthCheck(
                        "model_staleness",
                        "warning",
                        f"Champion {age_days}d old, {count} outcomes available",
                        value=float(age_days),
                        action_taken="triggered_retrain",
                    )
                return HealthCheck(
                    "model_staleness",
                    "warning",
                    f"Champion {age_days}d old but only {count or 0} outcomes — cannot retrain",
                    value=float(age_days),
                )
            return HealthCheck(
                "model_staleness",
                "healthy",
                f"Champion {age_days}d old, AUC={row['test_auc']:.4f}",
                value=float(age_days),
            )
        except Exception as exc:
            return HealthCheck("model_staleness", "warning", str(exc))

    async def _check_killswitch_state(self) -> HealthCheck:
        """Alert if killswitch has been TRIPPED > 2 hours without re-arm."""
        if not self._redis:
            return HealthCheck("killswitch_state", "warning", "No Redis client")
        try:
            raw = await self._redis.get(_KILLSWITCH_KEY)
            tripped = raw is not None and _as_text(raw).strip().upper() == "TRIPPED"
            if not tripped:
                # Clear the first-seen marker so the next trip restarts the clock.
                await self._redis.delete(_KILLSWITCH_SINCE_KEY)
                return HealthCheck("killswitch_state", "healthy", "Not tripped")

            now = datetime.now(UTC)
            await self._redis.set(_KILLSWITCH_SINCE_KEY, now.isoformat(), nx=True)
            since_raw = await self._redis.get(_KILLSWITCH_SINCE_KEY)
            since = datetime.fromisoformat(_as_text(since_raw)) if since_raw else now
            age_h = (now - since).total_seconds() / 3600

            if age_h > KILLSWITCH_STUCK_HOURS:
                _log.warning("health.killswitch_long_trip", age_hours=round(age_h, 1))
                return HealthCheck(
                    "killswitch_state",
                    "critical",
                    f"Killswitch tripped for {round(age_h, 1)}h — needs manual arm",
                    value=age_h,
                )
            return HealthCheck(
                "killswitch_state",
                "warning",
                f"Killswitch tripped ({round(age_h, 2)}h ago)",
                value=age_h,
            )
        except Exception as exc:
            return HealthCheck("killswitch_state", "warning", str(exc))

    async def _check_thresholds_staleness(self) -> HealthCheck:
        """Force a dynamic-threshold update when state is 8+ days old."""
        if not self._redis:
            return HealthCheck("thresholds_staleness", "warning", "No Redis client")
        try:
            import json

            raw = await self._redis.get(_THRESHOLDS_KEY)
            if raw is None:
                # Defaults in use — fine on fresh installs; the weekly job
                # will seed state once enough outcomes exist.
                return HealthCheck(
                    "thresholds_staleness", "healthy", "No adaptive state (defaults in use)"
                )
            data = json.loads(_as_text(raw))
            updated_at = datetime.fromisoformat(data["updated_at"])
            age_days = (datetime.now(UTC) - updated_at).total_seconds() / 86400

            if age_days > THRESHOLDS_STALE_DAYS:
                _log.warning(
                    "health.thresholds_stale",
                    age_days=round(age_days, 1),
                    action="forcing_update",
                )
                try:
                    from aegis.scheduler.autonomous import job_threshold_update

                    _spawn(job_threshold_update(), "health-threshold-update")
                except Exception as exc:
                    _log.error("health.threshold_update_failed", error=str(exc))
                return HealthCheck(
                    "thresholds_staleness",
                    "warning",
                    f"Thresholds {round(age_days, 1)}d stale",
                    value=age_days,
                    action_taken="triggered_threshold_update",
                )
            return HealthCheck(
                "thresholds_staleness",
                "healthy",
                f"Updated {round(age_days, 1)}d ago",
                value=age_days,
            )
        except Exception as exc:
            return HealthCheck("thresholds_staleness", "warning", str(exc))

    async def _check_clock_drift(self) -> HealthCheck:
        """Detect WSL clock drift. Common after laptop sleep/wake cycle."""
        if not self._redis:
            return HealthCheck("clock_drift", "warning", "No Redis client")
        try:
            redis_time = await self._redis.time()  # (seconds, microseconds)
            redis_ts = float(redis_time[0]) + float(redis_time[1]) / 1_000_000
            system_ts = time.time()
            drift_s = abs(system_ts - redis_ts)

            if drift_s > CLOCK_DRIFT_MAX_S:
                _log.warning(
                    "health.clock_drift",
                    drift_seconds=round(drift_s, 2),
                    action="syncing_hwclock",
                )
                await asyncio.to_thread(self._sync_hwclock)
                return HealthCheck(
                    "clock_drift",
                    "warning",
                    f"WSL clock drift {round(drift_s, 1)}s detected",
                    value=drift_s,
                    action_taken="ran_hwclock_sync",
                )
            return HealthCheck(
                "clock_drift",
                "healthy",
                f"Clock drift {round(drift_s * 1000, 1)}ms",
                value=drift_s,
            )
        except Exception as exc:
            return HealthCheck("clock_drift", "warning", str(exc))

    @staticmethod
    def _sync_hwclock() -> None:
        # ``sudo -n`` never prompts — without a NOPASSWD rule this is a
        # silent no-op rather than a hung subprocess.
        subprocess.run(
            ["sudo", "-n", "hwclock", "-s"],
            check=False,
            timeout=5,
            capture_output=True,
        )

    async def _check_evolution_loop_idle(self) -> HealthCheck:
        """Alert if no trade outcomes have been recorded in 7 days."""
        if not self._pool:
            return HealthCheck("evolution_loop_idle", "warning", "No DB pool")
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, TRUE)", self._tenant_id
                )
                count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM prediction_outcomes
                    WHERE settlement_timestamp > NOW() - INTERVAL '7 days'
                    """
                )
            if not count:
                return HealthCheck(
                    "evolution_loop_idle",
                    "warning",
                    "No trade outcomes recorded in 7 days — evolution loop may be broken",
                    value=0.0,
                )
            return HealthCheck(
                "evolution_loop_idle",
                "healthy",
                f"{count} outcomes recorded in last 7 days",
                value=float(count),
            )
        except Exception as exc:
            return HealthCheck("evolution_loop_idle", "warning", str(exc))


__all__ = [
    "AegisHealthChecker",
    "HealthCheck",
    "HealthReport",
    "HealthStatus",
]
