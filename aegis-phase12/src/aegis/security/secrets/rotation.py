"""
aegis.security.secrets.rotation — Automated secret rotation manager.

Provides scheduled and on-demand rotation for:
    - JWT signing secrets
    - HMAC keys
    - Database passwords (writes new value to Vault, triggers service reload)
    - API keys (marks old key for revocation after grace period)

Rotation workflow
-----------------
1. Generate a new secret value (cryptographically secure random).
2. Write new value to Vault KV v2 with ``cas`` (check-and-set) to prevent races.
3. Publish a ``secret.rotated`` audit event.
4. Start a grace-period timer during which BOTH old and new values are valid
   (zero-downtime rotation).
5. After grace period, mark the old version as ``deleted`` in Vault.
6. Publish ``secret.rotation_complete`` audit event.

Error codes: AEGIS-SEC-0116..0120
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from aegis.security.audit.logger import AuditLogger
from aegis.security.crypto import secure_hex
from aegis.security.vault._errors import VaultError
from aegis.security.vault.client import VaultClient

_log = structlog.get_logger(__name__)

# Grace period during which both old and new secrets are accepted
DEFAULT_GRACE_PERIOD_SECONDS: int = 300  # 5 minutes


@staticmethod
def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class RotationRecord:
    """Tracks the state of an in-progress secret rotation."""

    def __init__(
        self,
        path: str,
        old_version: int,
        new_version: int,
        grace_period_s: int = DEFAULT_GRACE_PERIOD_SECONDS,
    ) -> None:
        self.path = path
        self.old_version = old_version
        self.new_version = new_version
        self.started_at = _utcnow()
        self.grace_until = self.started_at + timedelta(seconds=grace_period_s)
        self.completed = False
        self.completed_at: datetime | None = None

    @property
    def in_grace_period(self) -> bool:
        """True while both old and new secrets are valid."""
        return not self.completed and _utcnow() < self.grace_until

    @property
    def grace_remaining_s(self) -> float:
        """Seconds remaining in the grace period (0 if expired)."""
        remaining = (self.grace_until - _utcnow()).total_seconds()
        return max(0.0, remaining)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "old_version": self.old_version,
            "new_version": self.new_version,
            "started_at": self.started_at.isoformat(),
            "grace_until": self.grace_until.isoformat(),
            "completed": self.completed,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "in_grace_period": self.in_grace_period,
        }


class SecretRotationManager:
    """Manages automated and on-demand secret rotation.

    Parameters
    ----------
    vault_client:
        Connected ``VaultClient`` instance.
    audit_logger:
        ``AuditLogger`` instance for rotation audit trail.
    actor:
        Service name for audit log entries.
    """

    def __init__(
        self,
        vault_client: VaultClient,
        audit_logger: AuditLogger | None = None,
        actor: str = "service:rotation-manager",
    ) -> None:
        self._vault = vault_client
        self._audit = audit_logger
        self._actor = actor
        self._in_progress: dict[str, RotationRecord] = {}
        self._rotation_tasks: dict[str, asyncio.Task[None]] = {}

    # ── Public API ─────────────────────────────────────────────────────────── #

    async def rotate_secret(
        self,
        path: str,
        *,
        field: str = "value",
        new_value: str | None = None,
        nbytes: int = 32,
        grace_period_s: int = DEFAULT_GRACE_PERIOD_SECONDS,
    ) -> RotationRecord:
        """Rotate a secret in Vault KV v2.

        Parameters
        ----------
        path:
            Vault KV path (e.g. ``"aegis/jwt/secret"``).
        field:
            Field name within the KV entry to rotate.
        new_value:
            New secret value; if ``None`` a cryptographically secure random
            hex string of ``nbytes`` bytes is generated.
        nbytes:
            Number of random bytes for auto-generated secrets.
        grace_period_s:
            Seconds to keep the old secret version accessible.

        Returns
        -------
        RotationRecord
            Rotation state tracker. Await ``complete_rotation(path)`` after the
            grace period to clean up the old version.
        """
        if path in self._in_progress and self._in_progress[path].in_grace_period:
            raise RuntimeError(
                f"[AEGIS-SEC-0116] Rotation already in progress for '{path}'. "
                f"Wait {self._in_progress[path].grace_remaining_s:.0f}s or call "
                f"complete_rotation('{path}') to force completion."
            )

        # 1. Read current version (for CAS)
        old_version = 0
        try:
            current = await self._vault.read_secret(path)
            old_version = current.version
        except VaultError:
            pass  # Secret may not exist yet — first write is version 0

        # 2. Generate new value
        value = new_value or secure_hex(nbytes)

        # 3. Write new version
        new_version = await self._vault.write_secret(
            path,
            {field: value},
            cas=old_version if old_version > 0 else None,
        )

        # 4. Create rotation record
        record = RotationRecord(
            path=path,
            old_version=old_version,
            new_version=new_version,
            grace_period_s=grace_period_s,
        )
        self._in_progress[path] = record

        # 5. Audit
        await self._audit_event(
            "secret.rotated",
            resource=path,
            metadata={
                "field": field,
                "old_version": old_version,
                "new_version": new_version,
                "grace_period_s": grace_period_s,
                "value_hash": hashlib.sha256(value.encode()).hexdigest()[:16],
            },
        )
        _log.info(
            "rotation.started",
            path=path,
            old_version=old_version,
            new_version=new_version,
            grace_s=grace_period_s,
        )

        # 6. Schedule automatic completion (task ref kept in _rotation_tasks to avoid GC)
        _task = asyncio.create_task(
            self._auto_complete(path, grace_period_s),
            name=f"rotation-complete-{path.replace('/', '-')}",
        )
        self._rotation_tasks[path] = _task

        return record

    async def complete_rotation(self, path: str) -> None:
        """Finalise a rotation by soft-deleting the old secret version.

        Parameters
        ----------
        path:
            Vault KV path that was rotated.
        """
        record = self._in_progress.get(path)
        if record is None:
            _log.warning("rotation.not_found", path=path)
            return
        if record.completed:
            return

        if record.old_version > 0:
            try:
                await self._vault.delete_secret(path, versions=[record.old_version])
                _log.info(
                    "rotation.old_version_deleted",
                    path=path,
                    version=record.old_version,
                )
            except VaultError as exc:
                _log.warning("rotation.delete_failed", path=path, error=str(exc))

        record.completed = True
        record.completed_at = _utcnow()
        del self._in_progress[path]

        await self._audit_event(
            "secret.rotation_complete",
            resource=path,
            metadata={
                "old_version": record.old_version,
                "new_version": record.new_version,
                "duration_s": (record.completed_at - record.started_at).total_seconds(),
            },
        )

    async def list_in_progress(self) -> list[dict[str, Any]]:
        """Return all currently in-progress rotations."""
        return [r.to_dict() for r in self._in_progress.values()]

    async def rotate_jwt_secret(
        self, vault_path: str = "aegis/security/jwt", **kwargs: Any  # noqa: ANN401
    ) -> RotationRecord:
        """Convenience: rotate the JWT signing secret (32 random bytes)."""
        return await self.rotate_secret(vault_path, field="secret", nbytes=32, **kwargs)

    async def rotate_hmac_key(
        self, vault_path: str = "aegis/security/hmac", **kwargs: Any  # noqa: ANN401
    ) -> RotationRecord:
        """Convenience: rotate the inter-service HMAC key (32 random bytes)."""
        return await self.rotate_secret(vault_path, field="key", nbytes=32, **kwargs)

    # ── Internals ──────────────────────────────────────────────────────────── #

    async def _auto_complete(self, path: str, delay_s: int) -> None:
        """Background task: wait for grace period then complete rotation."""
        await asyncio.sleep(delay_s)
        await self.complete_rotation(path)

    async def _audit_event(
        self,
        event: str,
        resource: str,
        metadata: dict[str, Any],
    ) -> None:
        if self._audit:
            try:
                await self._audit.log(
                    event,
                    actor=self._actor,
                    resource=resource,
                    outcome="success",
                    metadata=metadata,
                )
            except Exception as exc:
                _log.warning("rotation.audit_failed", error=str(exc))
