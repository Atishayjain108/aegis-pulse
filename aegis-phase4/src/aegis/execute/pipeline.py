"""Pipeline: the single high-level Phase 4 entry point.

A `Pipeline` is the boring orchestrator that takes a `ComposerInput`
(produced by the Phase 2 and/or Phase 3 bridges) and runs it through:

    1. compose()              → Alert
    2. KellyAdvisor.advise()  → SizingResult (attached to Alert)
    3. GateChain.run()        → maybe-BLOCKed Alert
    4. Deduper.should_emit()  → drop duplicates (alert already in flight)
    5. OutboxWriter.enqueue() → persist + outbox row
    6. EventBus.publish()     → fan to live SSE subscribers
    7. (optional) insert ExecutionIntent

Callers (CLI, REST endpoint, intake worker) construct one Pipeline once
and call `submit(composer_input)` on every input.

The pipeline is fully synchronous w.r.t. delivery — it returns once the
alert is durable (in Postgres). Actual notifications happen later via the
drainer worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import structlog

from aegis.execute.bridge.types import ComposerInput
from aegis.execute.bus import EventBus
from aegis.execute.constants import (
    KELLY_CAPITAL_DEFAULT_USD,
    VERDICT_BLOCK,
    VERDICT_DEGRADED,
    VERDICT_ENTER,
    VERDICT_EXIT,
    VERDICT_HOLD,
)
from aegis.execute.killswitch.switch import KillSwitch
from aegis.execute.metrics import metrics
from aegis.execute.outbox.writer import OutboxWriter
from aegis.execute.policy.composer import compose
from aegis.execute.policy.deduper import Deduper
from aegis.execute.risk.gates import GateChain, default_chain
from aegis.execute.schemas.alert import Alert
from aegis.execute.schemas.dashboard import SSEEvent
from aegis.execute.schemas.intent import ExecutionIntent, IntentKind
from aegis.execute.sizing.kelly import KellyAdvisor, SizingResult
from aegis.execute.store.repository import AlertRepository
from aegis.execute.utils.hashing import compute_intent_id
from aegis.execute.utils.time import utc_now

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SubmitOutcome:
    """Result of one pipeline submission.

    A submit may legitimately produce an Alert that is NOT persisted —
    e.g. when the killswitch is tripped, or the deduper says it's a
    repeat. The outcome flags distinguish these cases for the caller.
    """

    alert: Alert | None
    persisted: bool
    deduplicated: bool
    killswitch_tripped: bool
    intent: ExecutionIntent | None
    sizing: SizingResult | None


class Pipeline:
    """The deterministic Phase 4 pipeline."""

    __slots__ = (
        "_bus",
        "_deduper",
        "_default_capital_usd",
        "_gates",
        "_kelly",
        "_killswitch",
        "_repo",
        "_writer",
    )

    def __init__(
        self,
        *,
        repository: AlertRepository,
        gates: GateChain | None = None,
        kelly: KellyAdvisor | None = None,
        deduper: Deduper | None = None,
        killswitch: KillSwitch | None = None,
        bus: EventBus | None = None,
        default_capital_usd: float = KELLY_CAPITAL_DEFAULT_USD,
    ) -> None:
        self._repo = repository
        self._writer = OutboxWriter(repository)
        self._gates = gates or default_chain()
        self._kelly = kelly or KellyAdvisor()
        self._deduper = deduper or Deduper()
        self._killswitch = killswitch
        self._bus = bus
        self._default_capital_usd = float(default_capital_usd)

    async def submit(
        self,
        ci: ComposerInput,
        *,
        unit_cost_usd: float | None = None,
    ) -> SubmitOutcome:
        """Run a single composer input through the full pipeline."""
        # 0. Killswitch
        ks_tripped = False
        if self._killswitch is not None:
            ks_tripped = await self._killswitch.is_tripped()
            # Always reflect current state in the gauge.
            metrics.killswitch_tripped.set(1.0 if ks_tripped else 0.0)
            if ks_tripped:
                _log.warning(
                    "execute.pipeline.killswitch_tripped",
                    trend_id=ci.trend_id,
                )
                return SubmitOutcome(
                    alert=None,
                    persisted=False,
                    deduplicated=False,
                    killswitch_tripped=True,
                    intent=None,
                    sizing=None,
                )

        # 1. Compose (timed)
        with metrics.compose_latency_ms.time():
            alert = compose(ci)

        # 2. Sizing (advisory, attached to alert before persistence)
        sizing = self._kelly.advise(
            expected_margin_usd=alert.expected_margin_usd,
            loss_probability=alert.loss_probability,
            unit_cost_usd=unit_cost_usd,
            capital_usd=ci.capital_budget_usd or self._default_capital_usd,
        )
        # Only attach sizing if it produced > 0 units.
        if sizing.units > 0:
            alert = alert.model_copy(
                update={
                    "advised_units": sizing.units,
                    "advised_capital_usd": round(sizing.capital_usd, 2),
                }
            )

        # 3. Risk gates (may downgrade ENTER → BLOCK)
        verdict_before = alert.verdict
        alert = self._gates.run(alert)
        if alert.verdict == VERDICT_BLOCK and verdict_before != VERDICT_BLOCK:
            # Gate downgrade — record the reason (halt_reason carries the code).
            metrics.gate_blocks_total.labels(reason=alert.halt_reason or "unknown").inc()

        # 4. Dedupe (best-effort; the outbox is the durable dedup layer)
        unique = await self._deduper.should_emit(alert.alert_id)
        if not unique:
            _log.info(
                "execute.pipeline.deduplicated",
                alert_id=alert.alert_id,
                trend_id=alert.trend_id,
            )
            return SubmitOutcome(
                alert=alert,
                persisted=False,
                deduplicated=True,
                killswitch_tripped=False,
                intent=None,
                sizing=sizing,
            )

        # 5. Persist (alert + outbox row)
        await self._writer.enqueue(alert)

        # Emit a metric NOW — after persistence, before fan-out — so
        # dashboards see "what made it to the outbox".
        metrics.alerts_total.labels(
            verdict=alert.verdict,
            source=str(alert.source),
            priority=str(alert.priority),
        ).inc()

        # 6. Optionally write an ExecutionIntent for ENTER/EXIT
        intent: ExecutionIntent | None = None
        if alert.verdict in (VERDICT_ENTER, VERDICT_EXIT):
            intent = _build_intent_for(alert, sizing)
            try:
                await self._repo.insert_intent(intent)
            except Exception as exc:
                _log.warning(
                    "execute.pipeline.intent_insert_failed",
                    alert_id=alert.alert_id,
                    error=str(exc),
                )

        # 7. Live event fan-out
        if self._bus is not None:
            await self._bus.publish(
                SSEEvent(
                    event="alert.created",
                    data={
                        "alert_id": alert.alert_id,
                        "trend_id": alert.trend_id,
                        "verdict": alert.verdict,
                        "priority": alert.priority,
                        "score": alert.score,
                        "confidence": alert.confidence,
                        "source": str(alert.source),
                        "tenant_id": str(alert.tenant_id),
                        "created_at": alert.created_at.isoformat(),
                    },
                )
            )

        return SubmitOutcome(
            alert=alert,
            persisted=True,
            deduplicated=False,
            killswitch_tripped=False,
            intent=intent,
            sizing=sizing,
        )


def _build_intent_for(alert: Alert, sizing: SizingResult) -> ExecutionIntent:
    kind = (
        IntentKind.ENTER_POSITION
        if alert.verdict == VERDICT_ENTER
        else IntentKind.EXIT_POSITION
        if alert.verdict == VERDICT_EXIT
        else IntentKind.HOLD_POSITION
    )
    return ExecutionIntent(
        intent_id=compute_intent_id(alert_id=alert.alert_id, kind=str(kind)),
        alert_id=alert.alert_id,
        tenant_id=alert.tenant_id,
        trend_id=alert.trend_id,
        kind=kind,
        advised_units=max(0, int(sizing.units)),
        advised_capital_usd=max(0.0, float(sizing.capital_usd)),
        expected_margin_usd=alert.expected_margin_usd,
        loss_probability=alert.loss_probability,
        horizon_hours=24,
        rationale=sizing.rationale,
        created_at=utc_now(),
    )


# A small note re: HOLD/BLOCK/DEGRADED — we deliberately do not produce
# intents for those verdicts, because they do not direct any action.
_NON_ACTION_VERDICTS: Final = frozenset({VERDICT_HOLD, VERDICT_BLOCK, VERDICT_DEGRADED})


__all__ = ["Pipeline", "SubmitOutcome"]
