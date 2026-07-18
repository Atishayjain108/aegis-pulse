"""Phase 0 → Redis Stream bridge.

Emits deduped signal batches to ``aegis:phase0:raw_signals`` after every
successful topic scrape.  Consumers (realtime_consumer.py) subscribe via
XREADGROUP to trigger the Phase 2+3 pipeline without polling TimescaleDB.

Complexity: O(n) serialisation per call, O(1) amortised XADD (approximate
maxlen trimming).  Never raises — best-effort contract.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

PHASE0_STREAM = "aegis:phase0:raw_signals"
CONSUMER_GROUP = "aegis:phase2:intake"

_log = structlog.get_logger("aegis.scrape.stream_bridge")


async def emit_to_stream(
    redis_client: Any,
    signals: list[dict[str, Any]],
    *,
    topic: str,
    tenant_id: str,
    maxlen: int = 10_000,
) -> None:
    """XADD a deduped signal batch to the Phase 0 ingestion stream.

    Args:
        redis_client: Live async Redis client.  Noop when None.
        signals:      Serialisable signal dicts (already deduped by caller).
        topic:        The topic keyword that produced these signals.
        tenant_id:    Tenant UUID string for downstream routing.
        maxlen:       Approximate stream cap (XADD MAXLEN ~ maxlen).
    """
    if not signals or redis_client is None:
        return
    try:
        body = json.dumps(
            {
                "topic": topic,
                "tenant_id": tenant_id,
                "count": len(signals),
                "signals": signals,
            },
            default=str,
        )
        await redis_client.xadd(
            PHASE0_STREAM,
            {"body": body},
            maxlen=maxlen,
            approximate=True,
        )
        _log.debug("stream_bridge.emitted", topic=topic, count=len(signals))
    except Exception as exc:
        _log.warning("stream_bridge.emit_failed", topic=topic, error=str(exc))


async def ensure_consumer_group(redis_client: Any) -> None:
    """Create the consumer group on PHASE0_STREAM if it does not exist.

    Idempotent — safe to call on every startup.
    """
    if redis_client is None:
        return
    try:
        await redis_client.xgroup_create(
            PHASE0_STREAM, CONSUMER_GROUP, id="0", mkstream=True
        )
    except Exception as exc:
        if "BUSYGROUP" in str(exc):
            return  # group already exists — expected after restart
        _log.warning("stream_bridge.group_create_failed", error=str(exc))
