"""
aegis.security.audit.logger — Append-only, HMAC-signed audit log.

Every audit event is written to a local JSONL file *and* queued for
archival to a MinIO WORM bucket.  Events are chained: each entry includes
the SHA-256 hash of the previous entry, forming a tamper-evident log.

Schema (one JSON object per line)::

    {
        "seq":      1,
        "ts":       "2026-05-19T12:34:56.789Z",
        "event":    "secret.read",
        "actor":    "service:aegis-predict",
        "resource": "aegis/db/password",
        "outcome":  "success",
        "metadata": { ... },
        "prev_hash": "sha256 of previous line",
        "hmac":     "HMAC-SHA256 of (seq+ts+event+...+prev_hash)"
    }

Error codes: AEGIS-SEC-0041..0050
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from aegis.security.config import SecurityConfig, get_security_config

_log = structlog.get_logger(__name__)


def _utcnow() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="milliseconds")


class AuditLogger:
    """Append-only, HMAC-signed, chain-hashed audit logger.

    Parameters
    ----------
    config:
        Injected config; defaults to global singleton.

    Thread / async safety
    ----------------------
    ``log()`` is async-safe: calls are serialised through an internal
    asyncio.Lock so concurrent coroutines never interleave lines.
    """

    def __init__(self, config: SecurityConfig | None = None) -> None:
        self._cfg = config or get_security_config()
        self._lock = asyncio.Lock()
        self._seq: int = 0
        self._prev_hash: str = "0" * 64  # genesis hash
        self._log_path: Path | None = None
        self._file_handle: Any = None
        self._upload_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._upload_task: asyncio.Task[None] | None = None
        self._started: bool = False

    # ── Lifecycle ──────────────────────────────────────────────────────────── #

    async def start(self) -> None:
        """Open the log file and start the MinIO upload worker."""
        if self._started:
            return
        log_path = Path(self._cfg.audit_log_path).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing state (seq + last hash) for continuity
        self._seq, self._prev_hash = await self._load_state(log_path)

        self._log_path = log_path
        self._file_handle = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        self._upload_task = asyncio.create_task(
            self._upload_worker(), name="audit-minio-upload"
        )
        self._started = True
        _log.info("audit.started", path=str(log_path), seq=self._seq)

    async def stop(self) -> None:
        """Flush, close, and cancel background tasks."""
        if not self._started:
            return
        if self._upload_task and not self._upload_task.done():
            # Drain the queue before cancelling
            await asyncio.wait_for(self._upload_queue.join(), timeout=10.0)
            self._upload_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._upload_task
        if self._file_handle:
            self._file_handle.flush()
            self._file_handle.close()
        self._started = False
        _log.info("audit.stopped")

    async def __aenter__(self) -> AuditLogger:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ── Public API ─────────────────────────────────────────────────────────── #

    async def log(
        self,
        event: str,
        *,
        actor: str = "system",
        resource: str = "",
        outcome: str = "success",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append a signed audit event.

        Parameters
        ----------
        event:
            Dot-separated event name, e.g. ``"secret.read"`` or ``"jwt.issued"``.
        actor:
            Who performed the action (``"user:<id>"`` / ``"service:<name>"``).
        resource:
            What was acted upon (secret path, table name, URL, etc.).
        outcome:
            ``"success"`` | ``"failure"`` | ``"blocked"`` | ``"partial"``.
        metadata:
            Arbitrary additional context (PII-free!).

        Returns
        -------
        dict[str, Any]
            The signed audit entry that was written.
        """
        async with self._lock:
            if not self._started:
                await self.start()

            self._seq += 1
            ts = _utcnow()
            entry: dict[str, Any] = {
                "seq": self._seq,
                "ts": ts,
                "event": event,
                "actor": actor,
                "resource": resource,
                "outcome": outcome,
                "metadata": metadata or {},
                "prev_hash": self._prev_hash,
            }
            # Compute HMAC over the canonical representation
            entry["hmac"] = self._sign(entry)
            # Chain hash (SHA-256 of the full signed entry)
            line = json.dumps(entry, separators=(",", ":"), sort_keys=True)
            self._prev_hash = hashlib.sha256(line.encode()).hexdigest()

            if self._file_handle:
                self._file_handle.write(line + "\n")

            # Queue for async MinIO upload (non-blocking)
            try:
                self._upload_queue.put_nowait(entry)
            except asyncio.QueueFull:
                _log.warning("audit.upload_queue_full", seq=self._seq)

            return entry

    async def verify_integrity(self) -> tuple[bool, int, str | None]:
        """Verify the chain integrity of the current log file.

        Returns
        -------
        tuple[bool, int, str | None]
            ``(is_valid, last_seq_checked, first_error_description)``
        """
        # Resolve log path from config if not yet started
        log_path = self._log_path or Path(self._cfg.audit_log_path).expanduser()
        if not log_path.exists():
            return True, 0, None
        # Temporarily set for the check
        self._log_path = log_path

        prev = "0" * 64
        seq_checked = 0
        try:
            with open(log_path, encoding="utf-8") as f:
                for raw_line in f:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    entry = json.loads(raw_line)
                    seq_checked += 1

                    # Verify prev_hash
                    if entry.get("prev_hash") != prev:
                        return (
                            False,
                            seq_checked,
                            f"Chain broken at seq={entry.get('seq')}",
                        )

                    # Verify HMAC
                    stored_hmac = entry.pop("hmac", "")
                    expected = self._sign(entry)
                    entry["hmac"] = stored_hmac
                    if not hmac.compare_digest(stored_hmac, expected):
                        return (
                            False,
                            seq_checked,
                            f"HMAC mismatch at seq={entry.get('seq')}",
                        )

                    # Chain hash is over the full raw line as stored on disk
                    prev = hashlib.sha256(raw_line.encode()).hexdigest()

            return True, seq_checked, None
        except Exception as exc:
            return False, seq_checked, str(exc)

    # ── Internal ──────────────────────────────────────────────────────────── #

    def _sign(self, entry: dict[str, Any]) -> str:
        """Compute HMAC-SHA256 over the entry (excluding the ``hmac`` field)."""
        key = self._cfg.hmac_key.get_secret_value().encode()
        # Canonical representation: sorted keys, no spaces
        data = json.dumps(
            {k: v for k, v in entry.items() if k != "hmac"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hmac.new(key, data, hashlib.sha256).hexdigest()

    async def _upload_worker(self) -> None:
        """Background task: upload queued entries to MinIO WORM bucket."""
        while True:
            try:
                entries: list[dict[str, Any]] = []
                # Batch up to 100 entries or wait 5s
                try:
                    entry = await asyncio.wait_for(
                        self._upload_queue.get(), timeout=5.0
                    )
                    entries.append(entry)
                    self._upload_queue.task_done()
                    # Drain remaining without waiting
                    while not self._upload_queue.empty() and len(entries) < 100:
                        entry = self._upload_queue.get_nowait()
                        entries.append(entry)
                        self._upload_queue.task_done()
                except TimeoutError:
                    continue

                await self._upload_to_minio(entries)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                _log.error("audit.upload_worker_error", error=str(exc))
                await asyncio.sleep(5)

    async def _upload_to_minio(self, entries: list[dict[str, Any]]) -> None:
        """Upload a batch of audit entries to MinIO.

        Uses aiobotocore / boto3 async-compatible client.
        Falls back to a no-op log message if MinIO is unavailable.
        """
        try:
            import aiobotocore.session  # type: ignore[import-untyped]

            # Connection settings come from the main AEGIS config (not security config)
            minio_endpoint = os.environ.get("AEGIS_MINIO_ENDPOINT", "http://localhost:9002")
            minio_key = os.environ.get("AEGIS_MINIO_ACCESS_KEY", "aegis-dev-key")
            minio_secret = os.environ.get("AEGIS_MINIO_SECRET_KEY", "")

            if not minio_secret:
                return  # MinIO not configured

            batch_ts = _utcnow().replace(":", "-").replace(".", "-")
            first_seq = entries[0]["seq"]
            last_seq = entries[-1]["seq"]
            key = f"audit/{batch_ts}_{first_seq}_{last_seq}.jsonl"
            body = "\n".join(
                json.dumps(e, separators=(",", ":"), sort_keys=True) for e in entries
            ).encode()

            session = aiobotocore.session.get_session()
            async with session.create_client(
                "s3",
                endpoint_url=minio_endpoint,
                aws_access_key_id=minio_key,
                aws_secret_access_key=minio_secret,
                region_name="us-east-1",
            ) as client:
                await client.put_object(
                    Bucket=self._cfg.audit_minio_bucket,
                    Key=key,
                    Body=body,
                    ContentType="application/x-ndjson",
                    # Object-lock retention (WORM)
                    ObjectLockMode="COMPLIANCE",
                    ObjectLockRetainUntilDate=datetime(
                        2099, 1, 1, tzinfo=UTC
                    ),
                )
                _log.debug(
                    "audit.uploaded",
                    bucket=self._cfg.audit_minio_bucket,
                    key=key,
                    entries=len(entries),
                )
        except ImportError:
            _log.debug("audit.aiobotocore_not_available", note="pip install aiobotocore")
        except Exception as exc:
            _log.warning("audit.minio_upload_failed", error=str(exc))

    async def _load_state(self, log_path: Path) -> tuple[int, str]:
        """Read the last seq and prev_hash from an existing log file."""
        if not log_path.exists():
            return 0, "0" * 64
        last_seq = 0
        last_hash = "0" * 64
        try:
            with open(log_path, encoding="utf-8") as f:
                for raw_line in f:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        entry = json.loads(raw_line)
                        last_seq = entry.get("seq", last_seq)
                        last_hash = hashlib.sha256(raw_line.encode()).hexdigest()
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return last_seq, last_hash
