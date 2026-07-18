"""Real-time Phase 0 → Phase 2+3 stream consumer.

Subscribes to ``aegis:phase0:raw_signals`` via XREADGROUP and dispatches
each signal batch through the full Phase 2 LangGraph + Phase 3 InferenceRunner
pipeline without polling TimescaleDB.

Topology:
    scrape_topic() ──XADD──► aegis:phase0:raw_signals
                                        │
                              XREADGROUP (this module)
                                        │
                              TrendCandidate assembly
                                        │
                              run_trend()  ──XADD──► aegis:phase2:graph_results
                                                            │
                                                    Dashboard SSE consumer

Usage (long-running service mode):
    consumer = SignalStreamConsumer(redis_client=r, redis_client_p4=r)
    await consumer.run()

Usage (CLI / test dispatch):
    await consumer._process_entry(entry_id, data)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from aegis.agents.runner import run_trend  # type: ignore[reportUnknownVariableType]
from aegis.agents.schemas import TrendCandidate
from aegis.scrape.stream_bridge import CONSUMER_GROUP, PHASE0_STREAM

_log = structlog.get_logger("aegis.scrape.realtime_consumer")

_CONSUMER_NAME = "realtime-consumer-0"
_BLOCK_MS = 2_000
_BATCH_COUNT = 10


def _build_candidate(signals: list[dict[str, Any]], topic: str) -> TrendCandidate:
    """Assemble a TrendCandidate from a raw signal batch.

    O(n) pass — computes velocity windows, platform set, sentiment mean.
    Falls back to safe zeros on missing fields.
    """
    now = datetime.now(UTC)

    def _ts(s: dict[str, Any]) -> float:
        raw = s.get("scraped_at") or s.get("created_at") or s.get("published_at")
        if raw is None:
            return 0.0
        try:
            dt = datetime.fromisoformat(str(raw)) if isinstance(raw, str) else raw
            return dt.timestamp()
        except (ValueError, TypeError, AttributeError) as exc:
            _log.warning(
                "consumer.malformed_timestamp",
                raw_value=str(raw)[:120],
                error=str(exc),
            )
            return 0.0

    now_ts = now.timestamp()
    counts = {1: 0, 6: 0, 24: 0}
    for s in signals:
        age_h = (now_ts - _ts(s)) / 3600.0
        for h in (1, 6, 24):
            if age_h <= h:
                counts[h] += 1

    n = max(len(signals), 1)
    platforms: list[str] = list({str(s.get("platform", "unknown")) for s in signals})
    authors: set[str] = {str(s.get("author", "")) for s in signals if s.get("author")}
    sentiment = sum(float(s.get("sentiment_score", 0.0)) for s in signals) / n
    commercial = sum(float(s.get("commercial_intent", 0.3)) for s in signals) / n

    return TrendCandidate(
        trend_id=f"rt-{uuid.uuid4().hex[:12]}",
        title=topic or "realtime-harvest",
        signal_count=len(signals),
        unique_authors=len(authors),
        platforms=platforms,
        velocity_1h=float(counts[1]),
        velocity_6h=float(counts[6]),
        velocity_24h=float(counts[24]),
        sentiment=round(float(sentiment), 4),
        commercial_intent=round(float(commercial), 4),
        novelty=0.5,
        coordination_risk=0.0,
    )


class SignalStreamConsumer:
    """Async XREADGROUP consumer for the Phase 0 raw-signal stream.

    Args:
        redis_client:    Client used for XREADGROUP + XACK on PHASE0_STREAM.
        redis_client_p4: Client passed to run_trend for XADD to phase2 stream.
                         May be the same object as redis_client.
        use_llm:         Forwarded to run_trend.
        timeout_s:       Per-trend graph timeout forwarded to run_trend.
    """

    def __init__(
        self,
        *,
        redis_client: Any,
        redis_client_p4: Any,
        use_llm: bool = False,
        timeout_s: float = 60.0,
    ) -> None:
        self._redis = redis_client
        self._redis_p4 = redis_client_p4
        self._use_llm = use_llm
        self._timeout_s = timeout_s

    async def _process_entry(self, entry_id: str, data: dict[str, Any]) -> None:
        """Decode one stream entry, build a TrendCandidate, fire run_trend.

        XACK on success. On failure: logs and returns (entry stays in PEL
        for redelivery by the next consumer or pending-entry claim sweep).
        """
        try:
            raw = data.get("body", "{}")
            payload: dict[str, Any] = json.loads(raw)
            signals: list[dict[str, Any]] = payload.get("signals", [])
            topic: str = payload.get("topic", "unknown")
            tenant_id: str = payload.get("tenant_id", "default")

            if not signals:
                await self._redis.xack(PHASE0_STREAM, CONSUMER_GROUP, entry_id)
                return

            candidate = _build_candidate(signals, topic)
            _log.info(
                "consumer.dispatching",
                entry_id=entry_id,
                topic=topic,
                signals=len(signals),
                trend_id=candidate.trend_id,
            )

            await run_trend(
                candidate,
                tenant_id=tenant_id,
                signals=signals,
                use_llm=self._use_llm,
                timeout_s=self._timeout_s,
                stream_client=self._redis_p4,
            )
            await self._redis.xack(PHASE0_STREAM, CONSUMER_GROUP, entry_id)
            _log.info("consumer.dispatched", entry_id=entry_id, trend_id=candidate.trend_id)

        except Exception as exc:
            _log.warning(
                "consumer.dispatch_failed",
                entry_id=entry_id,
                error=str(exc),
            )

    async def run(self) -> None:
        """Block indefinitely, reading and dispatching stream entries.

        Runs until the event loop is cancelled.  Re-creates the consumer
        group if it disappears (e.g. Redis FLUSHDB in dev).
        """
        _log.info("consumer.started", stream=PHASE0_STREAM, group=CONSUMER_GROUP)
        while True:
            try:
                entries = await self._redis.xreadgroup(
                    CONSUMER_GROUP,
                    _CONSUMER_NAME,
                    {PHASE0_STREAM: ">"},
                    count=_BATCH_COUNT,
                    block=_BLOCK_MS,
                )
                if not entries:
                    continue
                for _, msgs in entries:
                    tasks = [
                        asyncio.create_task(self._process_entry(mid, data))
                        for mid, data in msgs
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)

            except asyncio.CancelledError:
                _log.info("consumer.stopped")
                return
            except Exception as exc:
                if "NOGROUP" in str(exc):
                    _log.warning("consumer.group_missing_recreating")
                    from aegis.scrape.stream_bridge import ensure_consumer_group
                    await ensure_consumer_group(self._redis)
                else:
                    _log.warning("consumer.xreadgroup_error", error=str(exc))
                await asyncio.sleep(1.0)
