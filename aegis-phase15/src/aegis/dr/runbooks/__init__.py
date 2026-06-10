"""
aegis.dr.runbooks
=================
Code-encoded runbooks for Phase 15 — automated failure recovery procedures.

Each runbook is a callable that:
1. Detects whether the failure condition is active.
2. Runs automated remediation steps in sequence.
3. Returns a ``RecoveryPlan`` describing what was done.
4. Escalates to human (sets ``requires_human=True``) for steps it cannot automate.

Architecture
-----------
Runbooks have NO side-effects when called with ``dry_run=True``.
All steps are logged via structlog so they appear in the audit trail.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import structlog

from aegis.dr.schemas import (
    FailureMode,
    FailureModeCategory,
    RecoveryPlan,
)

_log = structlog.get_logger("aegis.dr.runbooks")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _run_cmd(
    cmd: list[str],
    *,
    timeout: int = 60,
    dry_run: bool = False,
) -> tuple[int, str]:
    """Run a shell command; return (returncode, combined output)."""
    if dry_run:
        _log.info("runbook.dry_run_cmd", cmd=cmd)
        return 0, "[dry-run]"
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return proc.returncode or 0, out_b.decode(errors="replace")


# ---------------------------------------------------------------------------
# Postgres corruption
# ---------------------------------------------------------------------------

PG_CORRUPTION_MODE = FailureMode(
    category=FailureModeCategory.PG_CORRUPTION,
    title="PostgreSQL Data Corruption",
    description=(
        "TimescaleDB signals table or WAL is corrupt; "
        "queries return errors or crash the process."
    ),
    detection_signals=[
        "pg log: 'invalid page in block'",
        "pg log: 'could not read block'",
        "asyncpg.PostgresError in aegis.db.*",
    ],
    automated_steps=[
        "1. Stop aegis-postgres container",
        "2. Rename corrupted data directory",
        "3. Download latest pg_dump from MinIO DR bucket",
        "4. Restore with pg_restore -j 4",
        "5. Verify row counts match manifest",
        "6. Restart aegis-postgres container",
    ],
    manual_steps=[
        "If automated restore fails: manually inspect pg_dump, "
        "contact support with AEGIS-DR-0010."
    ],
    expected_rto_s=1800,
    severity="critical",
    runbook_path="docs/DR_RUNBOOK.md#pg-corruption",
)


async def run_pg_corruption_recovery(
    *,
    settings: Any,
    minio_client: Any,
    dry_run: bool = False,
) -> RecoveryPlan:
    """Automated recovery for Postgres corruption."""
    steps_done: list[str] = []

    # Step 1: Stop container
    rc, out = await _run_cmd(
        ["docker", "stop", "aegis-postgres"], dry_run=dry_run
    )
    steps_done.append(f"stop aegis-postgres: rc={rc}")

    # Step 2: Download and restore
    from aegis.dr.restore.postgres import PostgresRestore

    engine = PostgresRestore(
        settings=settings,
        minio_client=minio_client,
        dry_run=dry_run,
    )
    try:
        result = await engine.run()
        steps_done.append(
            f"pg_restore: status={result.status.value}, "
            f"rows={result.rows_restored}, verified={result.verification_passed}"
        )
        requires_human = not result.verification_passed
    except Exception as exc:
        steps_done.append(f"pg_restore FAILED: {exc}")
        requires_human = True

    # Step 3: Restart container
    rc2, _ = await _run_cmd(
        ["docker", "start", "aegis-postgres"], dry_run=dry_run
    )
    steps_done.append(f"start aegis-postgres: rc={rc2}")

    return RecoveryPlan(
        plan_id=uuid4(),
        created_at=datetime.now(UTC),
        failure_mode=FailureModeCategory.PG_CORRUPTION,
        steps=steps_done,
        estimated_rto_s=PG_CORRUPTION_MODE.expected_rto_s,
        requires_human=requires_human,
        human_escalation_reason=(
            "Automated restore verification failed — manual inspection required"
            if requires_human
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Redis OOM
# ---------------------------------------------------------------------------

REDIS_OOM_MODE = FailureMode(
    category=FailureModeCategory.REDIS_OOM,
    title="Redis Out-of-Memory",
    description="Redis maxmemory reached; writes returning OOM errors.",
    detection_signals=[
        "redis log: 'OOM command not allowed'",
        "redis INFO: used_memory > maxmemory",
    ],
    automated_steps=[
        "1. Run MEMORY PURGE to free expired keys",
        "2. Flush TTL-less non-critical keyspaces (db 1, 2)",
        "3. Increase maxmemory by 20% via CONFIG SET",
        "4. Restart aegis-redis if memory still critical",
    ],
    manual_steps=["If still OOM after automation: scale Redis RAM or add replica."],
    expected_rto_s=120,
    severity="high",
    runbook_path="docs/DR_RUNBOOK.md#redis-oom",
)


async def run_redis_oom_recovery(
    *,
    redis_client: Any,
    dry_run: bool = False,
) -> RecoveryPlan:
    """Automated recovery for Redis OOM."""
    steps_done: list[str] = []
    requires_human = False

    if dry_run:
        return RecoveryPlan(
            plan_id=uuid4(),
            created_at=datetime.now(UTC),
            failure_mode=FailureModeCategory.REDIS_OOM,
            steps=["[dry-run] would purge expired keys and adjust maxmemory"],
            estimated_rto_s=REDIS_OOM_MODE.expected_rto_s,
            requires_human=False,
        )

    try:
        # Step 1: Memory purge
        await redis_client.execute_command("MEMORY PURGE")
        steps_done.append("MEMORY PURGE: ok")

        # Step 2: Get current maxmemory and increase by 20%
        info = await redis_client.config_get("maxmemory")
        current = int(info.get("maxmemory", 0))
        if current > 0:
            new_max = int(current * 1.2)
            await redis_client.config_set("maxmemory", new_max)
            steps_done.append(f"maxmemory: {current} → {new_max}")
        else:
            steps_done.append("maxmemory: unlimited (no change)")

    except Exception as exc:
        steps_done.append(f"ERROR: {exc}")
        requires_human = True

    return RecoveryPlan(
        plan_id=uuid4(),
        created_at=datetime.now(UTC),
        failure_mode=FailureModeCategory.REDIS_OOM,
        steps=steps_done,
        estimated_rto_s=REDIS_OOM_MODE.expected_rto_s,
        requires_human=requires_human,
    )


# ---------------------------------------------------------------------------
# Disk full
# ---------------------------------------------------------------------------

DISK_FULL_MODE = FailureMode(
    category=FailureModeCategory.DISK_FULL,
    title="WSL Disk Full",
    description="ext4 virtual disk usage ≥ 95%; writes failing.",
    detection_signals=[
        "shutil.disk_usage: free < 5 GB",
        "IOError: No space left on device",
    ],
    automated_steps=[
        "1. Run docker system prune (removes unused images, volumes)",
        "2. Remove aegis-dr local cache files older than 7 days",
        "3. Run restic prune to compact the restic repo",
        "4. Alert with current disk state",
    ],
    manual_steps=[
        "Expand WSL virtual disk: wsl --manage Ubuntu-24.04 --set-sparse true",
        "Move cold data to external MinIO bucket.",
    ],
    expected_rto_s=600,
    severity="high",
    runbook_path="docs/DR_RUNBOOK.md#disk-full",
)


async def run_disk_full_recovery(*, dry_run: bool = False) -> RecoveryPlan:
    """Automated recovery for disk-full condition."""
    steps_done: list[str] = []

    # Docker prune
    rc, out = await _run_cmd(
        ["docker", "system", "prune", "-f", "--volumes"],
        timeout=120,
        dry_run=dry_run,
    )
    steps_done.append(f"docker prune: rc={rc}, freed≈{_extract_space(out)}")

    # Report new disk usage
    import shutil as _shutil
    usage = _shutil.disk_usage("/")
    free_gb = usage.free / 1e9
    steps_done.append(f"disk_free_after: {free_gb:.1f} GB")

    return RecoveryPlan(
        plan_id=uuid4(),
        created_at=datetime.now(UTC),
        failure_mode=FailureModeCategory.DISK_FULL,
        steps=steps_done,
        estimated_rto_s=DISK_FULL_MODE.expected_rto_s,
        requires_human=free_gb < 5.0,
        human_escalation_reason=(
            "Less than 5 GB free after automated cleanup — expand WSL disk manually"
            if free_gb < 5.0
            else None
        ),
    )


def _extract_space(docker_output: str) -> str:
    """Extract freed space string from docker prune output."""
    for line in docker_output.splitlines():
        if "reclaimed" in line.lower():
            return line.strip()
    return "unknown"


# ---------------------------------------------------------------------------
# Docker daemon dead
# ---------------------------------------------------------------------------

DOCKER_DEAD_MODE = FailureMode(
    category=FailureModeCategory.DOCKER_DEAD,
    title="Docker Daemon Unresponsive",
    description="docker info times out; all containers unreachable.",
    detection_signals=[
        "docker info: timeout",
        "Cannot connect to Docker daemon",
    ],
    automated_steps=[
        "1. Kill all dockerd processes",
        "2. Restart Docker Desktop (Windows side via PowerShell)",
        "3. Wait for daemon readiness (poll docker info)",
        "4. Restart all AEGIS containers",
    ],
    manual_steps=[
        "If Docker Desktop won't start: reboot Windows, then re-run aegis up."
    ],
    expected_rto_s=300,
    severity="critical",
    runbook_path="docs/DR_RUNBOOK.md#docker-dead",
)


async def run_docker_dead_recovery(*, dry_run: bool = False) -> RecoveryPlan:
    steps_done: list[str] = []

    # Try restarting the docker service (works inside WSL if Docker Desktop
    # has the WSL integration daemon)
    rc, out = await _run_cmd(
        ["sudo", "service", "docker", "restart"],
        timeout=60,
        dry_run=dry_run,
    )
    steps_done.append(f"docker service restart: rc={rc}")

    # Poll for daemon readiness
    for attempt in range(12):
        await asyncio.sleep(5)
        rc2, _ = await _run_cmd(["docker", "info"], timeout=10, dry_run=dry_run)
        if rc2 == 0:
            steps_done.append(f"docker daemon ready after {(attempt+1)*5}s")
            break
    else:
        steps_done.append("docker daemon still unresponsive — requires manual restart")
        return RecoveryPlan(
            plan_id=uuid4(),
            created_at=datetime.now(UTC),
            failure_mode=FailureModeCategory.DOCKER_DEAD,
            steps=steps_done,
            estimated_rto_s=DOCKER_DEAD_MODE.expected_rto_s,
            requires_human=True,
            human_escalation_reason="Docker daemon did not recover automatically",
        )

    # Restart AEGIS containers
    rc3, _ = await _run_cmd(
        ["docker", "compose", "-f", "docker-compose.yml", "up", "-d"],
        timeout=120,
        dry_run=dry_run,
    )
    steps_done.append(f"docker compose up -d: rc={rc3}")

    return RecoveryPlan(
        plan_id=uuid4(),
        created_at=datetime.now(UTC),
        failure_mode=FailureModeCategory.DOCKER_DEAD,
        steps=steps_done,
        estimated_rto_s=DOCKER_DEAD_MODE.expected_rto_s,
        requires_human=rc3 != 0,
    )


# ---------------------------------------------------------------------------
# Registry of all runbooks
# ---------------------------------------------------------------------------

FAILURE_MODES: dict[FailureModeCategory, FailureMode] = {
    FailureModeCategory.PG_CORRUPTION: PG_CORRUPTION_MODE,
    FailureModeCategory.REDIS_OOM: REDIS_OOM_MODE,
    FailureModeCategory.DISK_FULL: DISK_FULL_MODE,
    FailureModeCategory.DOCKER_DEAD: DOCKER_DEAD_MODE,
}


# Resolve forward reference
from typing import Any  # noqa: E402
