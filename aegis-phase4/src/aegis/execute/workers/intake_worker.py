"""Intake worker.

Consumes upstream signals from Phase 2 + Phase 3 via the **shared Redis
Stream** convention established in Phase 2 (HMAC-signed inter-agent
messages over Redis Streams). For Phase 4, we consume two streams:

    aegis:phase2:graph_results
    aegis:phase3:inference_results

Each message is a JSON blob containing enough information to reconstruct
a `ComposerInput` via the appropriate bridge. If both Phase 2 and Phase 3
results arrive for the same `trend_id` within `MERGE_WINDOW_S`, the
intake worker merges them into a single ComposerInput before submitting
to the pipeline.

This file is intentionally tolerant: a malformed message is logged and
skipped — it must never crash the worker.

Note on independence: this module does not import any Phase 2 or Phase 3
package at top level. It works purely from the JSON shapes specified in
the upstream payload contracts. Tests can drive it with dicts directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

import structlog

from aegis.execute.bridge.types import ComposerInput
from aegis.execute.pipeline import Pipeline

_log = structlog.get_logger(__name__)

# Streams we consume.
STREAM_PHASE2: Final[str] = "aegis:phase2:graph_results"
STREAM_PHASE3: Final[str] = "aegis:phase3:inference_results"

# Merge window: if Phase 2 and Phase 3 results for the same trend_id
# arrive within this many seconds, combine them.
MERGE_WINDOW_S: Final[float] = 30.0

# Stream consumer config
CONSUMER_GROUP: Final[str] = "aegis-execute-intake"
CONSUMER_NAME_PREFIX: Final[str] = "intake-"
READ_BLOCK_MS: Final[int] = 1000  # blocking read budget
READ_COUNT: Final[int] = 10


@dataclass
class _PendingMerge:
    """Buffered partial-input awaiting its counterpart."""

    composer_input: ComposerInput
    received_at: float


class IntakeWorker:
    """Background task that maps stream messages to pipeline submissions.

    Implementation note: the Redis Streams *consumer group* APIs differ
    slightly between aioredis-py and redis-py. To stay simple, this
    worker uses only `XREADGROUP`, `XACK`, and `XGROUP CREATE` — the
    intersection that both implementations support.

    Callers that don't want Redis at all can use `submit_phase2_dict`
    and `submit_phase3_dict` directly — this is the same path used by
    unit tests.
    """

    __slots__ = (
        "_consumer_name",
        "_lock",
        "_pending",
        "_pipeline",
        "_redis",
        "_stop",
        "_task",
        "_tenant_id",
    )

    def __init__(
        self,
        *,
        pipeline: Pipeline,
        tenant_id: UUID,
        redis_client: Any | None = None,
        consumer_name: str | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._redis = redis_client
        self._tenant_id = tenant_id
        self._consumer_name = consumer_name or f"{CONSUMER_NAME_PREFIX}{int(time.time())}"
        # trend_id → pending half-input
        self._pending: dict[str, _PendingMerge] = {}
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ public
    async def start(self) -> None:
        if self._redis is None:
            _log.info("execute.intake.no_redis_no_loop")
            return
        if self._task is not None and not self._task.done():
            return
        await self._ensure_groups()
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="execute-intake")
        _log.info(
            "execute.intake.started",
            consumer_name=self._consumer_name,
            tenant_id=str(self._tenant_id),
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        _log.info("execute.intake.stopped")

    async def submit_phase2_dict(self, msg: dict[str, Any]) -> None:
        """Public API used by tests and direct integrations."""
        ci = _parse_phase2_dict(msg, tenant_id=self._tenant_id)
        await self._merge_or_submit(ci)

    async def submit_phase3_dict(self, msg: dict[str, Any]) -> None:
        ci = _parse_phase3_dict(msg, tenant_id=self._tenant_id)
        await self._merge_or_submit(ci)

    # ------------------------------------------------------------------ internal
    async def _ensure_groups(self) -> None:
        """Create the consumer group on both streams. Idempotent."""
        for stream in (STREAM_PHASE2, STREAM_PHASE3):
            try:
                # `mkstream=True` creates the stream if it doesn't exist.
                await self._redis.xgroup_create(
                    name=stream, groupname=CONSUMER_GROUP, id="$", mkstream=True
                )
                _log.info("execute.intake.group_created", stream=stream)
            except Exception as exc:
                # redis-py raises ResponseError on BUSYGROUP; we treat any
                # non-fatal exception here as "already exists".
                _log.debug("execute.intake.group_exists", stream=stream, error=str(exc))

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._read_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.error("execute.intake.read_failed", error=str(exc))
                await asyncio.sleep(1.0)

    async def _read_once(self) -> None:
        streams = {STREAM_PHASE2: ">", STREAM_PHASE3: ">"}
        try:
            res = await self._redis.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=self._consumer_name,
                streams=streams,
                count=READ_COUNT,
                block=READ_BLOCK_MS,
            )
        except Exception as exc:
            _log.error("execute.intake.xreadgroup_failed", error=str(exc))
            await asyncio.sleep(1.0)
            return
        if not res:
            return
        for stream_name, entries in res:
            if isinstance(stream_name, bytes | bytearray):
                stream_str = stream_name.decode()
            else:
                stream_str = str(stream_name)
            for entry_id, payload in entries:
                await self._handle(stream_str, entry_id, payload)

    async def _handle(self, stream: str, entry_id: Any, payload: dict[Any, Any]) -> None:
        try:
            decoded = _decode_payload(payload)
        except Exception as exc:
            _log.warning("execute.intake.decode_failed", stream=stream, error=str(exc))
            await self._ack(stream, entry_id)
            return
        try:
            if stream == STREAM_PHASE2:
                await self.submit_phase2_dict(decoded)
            elif stream == STREAM_PHASE3:
                await self.submit_phase3_dict(decoded)
            else:
                _log.warning("execute.intake.unknown_stream", stream=stream)
        except Exception as exc:
            _log.error(
                "execute.intake.handle_failed",
                stream=stream,
                error=str(exc),
            )
        finally:
            await self._ack(stream, entry_id)

    async def _ack(self, stream: str, entry_id: Any) -> None:
        try:
            await self._redis.xack(stream, CONSUMER_GROUP, entry_id)
        except Exception as exc:
            _log.warning("execute.intake.ack_failed", stream=stream, error=str(exc))

    async def _merge_or_submit(self, ci: ComposerInput) -> None:
        """Merge with a pending counterpart if any, otherwise buffer."""
        async with self._lock:
            now = time.monotonic()
            evicted = self._evict_expired(now)
            existing = self._pending.pop(ci.trend_id, None)
            if existing is None:
                # Buffer and wait for the other side, but submit immediately
                # if this side alone is already sufficient (Phase 2 carries
                # a complete verdict, Phase 3 alone is sufficient for DEGRADED).
                self._pending[ci.trend_id] = _PendingMerge(
                    composer_input=ci, received_at=now
                )
                merged = None
            else:
                merged = _merge_inputs(existing.composer_input, ci)
        # Submit outside the lock to avoid holding it during I/O.
        for stale_ci in evicted:
            try:
                await self._pipeline.submit(stale_ci)
            except Exception as exc:
                _log.warning(
                    "execute.intake.evict_submit_failed",
                    trend_id=stale_ci.trend_id,
                    error=str(exc),
                )
        if merged is not None:
            await self._pipeline.submit(merged)

    def _evict_expired(self, now: float) -> list[ComposerInput]:
        """Remove timed-out pending merges and return them for submission."""
        expired = [k for k, p in self._pending.items() if now - p.received_at > MERGE_WINDOW_S]
        evicted: list[ComposerInput] = []
        for k in expired:
            stale = self._pending.pop(k, None)
            if stale is not None:
                _log.info(
                    "execute.intake.evicting_partial",
                    trend_id=k,
                    age_s=round(now - stale.received_at, 2),
                )
                evicted.append(stale.composer_input)
        return evicted

    async def flush_pending(self) -> int:
        """Submit any half-inputs that have been buffered (for shutdown)."""
        async with self._lock:
            items = list(self._pending.values())
            self._pending.clear()
        for p in items:
            try:
                await self._pipeline.submit(p.composer_input)
            except Exception as exc:
                _log.warning(
                    "execute.intake.flush_failed",
                    trend_id=p.composer_input.trend_id,
                    error=str(exc),
                )
        return len(items)


# ---------------------------------------------------------------------------
# Pure helpers (testable without Redis)
# ---------------------------------------------------------------------------
def _decode_payload(payload: dict[Any, Any]) -> dict[str, Any]:
    """Decode a Redis Streams payload.

    A stream entry is a flat field map. Phase 2/3 emit a single field
    `body` containing a JSON-encoded message. We tolerate both bytes and
    str keys/values for compatibility with both redis-py versions.
    """
    def _s(v: Any) -> str:
        return v.decode() if isinstance(v, bytes | bytearray) else str(v)

    flat: dict[str, str] = {_s(k): _s(v) for k, v in payload.items()}
    if "body" in flat:
        return json.loads(flat["body"])
    # Tolerate already-flattened messages.
    return flat  # type: ignore[return-value]


def _parse_phase2_dict(msg: dict[str, Any], *, tenant_id: UUID) -> ComposerInput:
    """Build a ComposerInput from a Phase 2 message dict.

    Tolerates missing keys: numeric defaults to None, string defaults to "".
    """

    def _f(k: str) -> float | None:
        v = msg.get(k)
        return float(v) if v is not None else None

    def _i(k: str) -> int | None:
        v = msg.get(k)
        return int(v) if v is not None else None

    def _tuple(k: str) -> tuple[str, ...]:
        v = msg.get(k) or []
        return tuple(str(x) for x in v)

    return ComposerInput(
        tenant_id=tenant_id,
        trend_id=str(msg.get("trend_id", "unknown")),
        decision_window=str(msg.get("decision_window", "default")),
        correlation_id=msg.get("correlation_id"),
        phase2_verdict=(msg.get("final_verdict") or msg.get("verdict")),
        phase2_score=_f("final_score") if msg.get("final_score") is not None else _f("score"),
        phase2_confidence=(
            _f("final_confidence")
            if msg.get("final_confidence") is not None
            else _f("confidence")
        ),
        phase2_priority=(
            _i("final_priority") if msg.get("final_priority") is not None else _i("priority")
        ),
        phase2_halt_reason=msg.get("halt_reason"),
        phase2_blocked_by=_tuple("blocked_by"),
        phase2_title=msg.get("title"),
        phase2_narrative=str(msg.get("narrative", "")),
        capital_budget_usd=_f("capital_budget_usd"),
    )


def _parse_phase3_dict(msg: dict[str, Any], *, tenant_id: UUID) -> ComposerInput:
    """Build a ComposerInput from a Phase 3 InferenceResult message.

    Two accepted shapes:
      A. {"trend_id":..., "bundle": {"predictions": [...]}, "policy_action":...}
      B. {"trend_id":..., "predictions": [...], "policy_action":...}
    """
    predictions = []
    if isinstance(msg.get("bundle"), dict):
        predictions = msg["bundle"].get("predictions") or []
    elif isinstance(msg.get("predictions"), list):
        predictions = msg["predictions"]

    def _pick_by_horizon(target: int) -> dict[str, Any] | None:
        if not predictions:
            return None
        return min(predictions, key=lambda p: abs(int(p.get("horizon_hours", 0)) - target))

    pred_24 = _pick_by_horizon(24)
    pred_6 = _pick_by_horizon(6)
    pred_max = max(predictions, key=lambda p: int(p.get("horizon_hours", 0))) if predictions else None

    def _avg(field: str) -> float | None:
        xs = [float(p[field]) for p in predictions if p.get(field) is not None]
        return sum(xs) / len(xs) if xs else None

    return ComposerInput(
        tenant_id=tenant_id,
        trend_id=str(msg.get("trend_id", "unknown")),
        decision_window=str(msg.get("decision_window", "default")),
        correlation_id=msg.get("correlation_id"),
        phase3_p_breakout_24h=(
            float(pred_24["p_breakout"]) if pred_24 and "p_breakout" in pred_24 else None
        ),
        phase3_p_decline_6h=(
            float(pred_6["p_decline"]) if pred_6 and "p_decline" in pred_6 else None
        ),
        phase3_p_saturation=(
            float(pred_max["p_saturation"])
            if pred_max and "p_saturation" in pred_max
            else None
        ),
        phase3_expected_margin_usd=_avg("expected_margin_usd"),
        phase3_loss_probability=_avg("loss_probability"),
        phase3_confidence=_avg("confidence"),
        phase3_policy_action=(
            str(msg["policy_action"]).lower() if msg.get("policy_action") is not None else None
        ),
    )


def _merge_inputs(a: ComposerInput, b: ComposerInput) -> ComposerInput:
    """Combine two ComposerInputs for the same trend_id.

    Phase 2 fields prefer the one that has them; same for Phase 3. If both
    sides set a Phase 2 field, the later input wins (b).
    """
    if a.trend_id != b.trend_id:
        raise ValueError(f"merge: trend_id mismatch {a.trend_id} vs {b.trend_id}")

    def pick(field_a, field_b):
        return field_b if field_b is not None else field_a

    return ComposerInput(
        tenant_id=a.tenant_id,
        trend_id=a.trend_id,
        decision_window=b.decision_window or a.decision_window,
        correlation_id=pick(a.correlation_id, b.correlation_id),
        phase2_verdict=pick(a.phase2_verdict, b.phase2_verdict),
        phase2_score=pick(a.phase2_score, b.phase2_score),
        phase2_confidence=pick(a.phase2_confidence, b.phase2_confidence),
        phase2_priority=pick(a.phase2_priority, b.phase2_priority),
        phase2_halt_reason=pick(a.phase2_halt_reason, b.phase2_halt_reason),
        phase2_blocked_by=b.phase2_blocked_by or a.phase2_blocked_by,
        phase2_title=pick(a.phase2_title, b.phase2_title),
        phase2_narrative=b.phase2_narrative or a.phase2_narrative,
        phase3_p_breakout_24h=pick(a.phase3_p_breakout_24h, b.phase3_p_breakout_24h),
        phase3_p_decline_6h=pick(a.phase3_p_decline_6h, b.phase3_p_decline_6h),
        phase3_p_saturation=pick(a.phase3_p_saturation, b.phase3_p_saturation),
        phase3_expected_margin_usd=pick(
            a.phase3_expected_margin_usd, b.phase3_expected_margin_usd
        ),
        phase3_loss_probability=pick(a.phase3_loss_probability, b.phase3_loss_probability),
        phase3_confidence=pick(a.phase3_confidence, b.phase3_confidence),
        phase3_policy_action=pick(a.phase3_policy_action, b.phase3_policy_action),
        capital_budget_usd=pick(a.capital_budget_usd, b.capital_budget_usd),
    )


__all__ = [
    "CONSUMER_GROUP",
    "MERGE_WINDOW_S",
    "STREAM_PHASE2",
    "STREAM_PHASE3",
    "IntakeWorker",
]
