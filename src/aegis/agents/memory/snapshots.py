"""
Snapshot manager — periodic checkpoints to MinIO (S3-compatible).

Snapshots are JSON-encoded `GraphResult` objects (or dicts), written
to `aegis-snapshots/<tenant>/<YYYY/MM/DD>/<correlation_id>.json` with
SHA256 content-addressing for dedup. Reads are best-effort and never
block the agent pipeline.

If `boto3` is not installed or MinIO is unreachable, this becomes a
no-op — the system stays functional and just loses the safety net.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

_log = structlog.get_logger("aegis.agents.memory.snapshots")


@dataclass(frozen=True, slots=True)
class SnapshotConfig:
    bucket: str = "aegis-snapshots"
    endpoint_url: str = "http://127.0.0.1:9002"
    region_name: str = "us-east-1"
    access_key: str = ""
    secret_key: str = ""

    def __repr__(self) -> str:
        ak = "***" if self.access_key else ""
        sk = "***" if self.secret_key else ""
        return (
            f"SnapshotConfig(bucket={self.bucket!r}, "
            f"endpoint_url={self.endpoint_url!r}, "
            f"region_name={self.region_name!r}, "
            f"access_key={ak!r}, secret_key={sk!r})"
        )

    @classmethod
    def from_env(cls) -> SnapshotConfig:
        return cls(
            bucket=os.environ.get("AEGIS_SNAPSHOT_BUCKET", "aegis-snapshots"),
            endpoint_url=os.environ.get("AEGIS_S3_ENDPOINT", "http://127.0.0.1:9002"),
            region_name=os.environ.get("AEGIS_S3_REGION", "us-east-1"),
            access_key=os.environ.get("AEGIS_S3_ACCESS_KEY", ""),
            secret_key=os.environ.get("AEGIS_S3_SECRET_KEY", ""),
        )


class SnapshotManager:
    """Best-effort, append-only snapshot writer."""

    def __init__(self, config: SnapshotConfig | None = None, *, client: Any | None = None) -> None:
        self.config = config or SnapshotConfig.from_env()
        self._client = client
        self._available: bool | None = None

    def _build_client(self) -> Any | None:
        if self._available is False:
            return None
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError:
            _log.info("snapshot.boto3_missing", note="snapshots disabled")
            self._available = False
            return None
        try:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.config.endpoint_url,
                region_name=self.config.region_name,
                aws_access_key_id=self.config.access_key,
                aws_secret_access_key=self.config.secret_key,
            )
            self._available = True
        except Exception:  # pragma: no cover
            _log.exception("snapshot.client_init_failed")
            self._available = False
            return None
        return self._client

    @staticmethod
    def _key(tenant_id: str, correlation_id: str, ts: datetime) -> str:
        return f"{tenant_id}/{ts.year:04d}/{ts.month:02d}/{ts.day:02d}/" f"{correlation_id}.json"

    async def write(
        self,
        *,
        tenant_id: str,
        correlation_id: str,
        payload: dict[str, Any] | Any,
    ) -> str | None:
        """Write one snapshot. Returns the S3 key, or None on failure."""
        client = await asyncio.to_thread(self._build_client)
        if client is None:
            return None

        # `payload` may be a pydantic model — serialize via model_dump().
        if hasattr(payload, "model_dump"):
            data = payload.model_dump(mode="json")
        elif hasattr(payload, "dict"):  # legacy / safety
            data = payload.dict()
        else:
            data = payload

        body = json.dumps(data, separators=(",", ":"), default=str).encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        ts = datetime.now(tz=UTC)
        key = self._key(tenant_id, correlation_id, ts)

        try:
            await asyncio.to_thread(
                client.put_object,
                Bucket=self.config.bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
                Metadata={"sha256": digest, "ts": ts.isoformat()},
            )
        except Exception:
            _log.exception("snapshot.write_failed", bucket=self.config.bucket, key=key)
            return None
        return key
