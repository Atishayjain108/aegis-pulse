"""Phase 4 bridge: escalate compliance blocks to the alert/killswitch layer.

All collaborators are duck-typed Protocols so this module never imports Phase 4.
A ``block`` verdict is published as a compliance-escalation; a *critical* block
(simultaneous trademark + counterfeit block evidence) may optionally trip the
killswitch when running in ``live`` mode.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from aegis.comply.constants import COMPLIANCE_ESCALATION_STREAM, STREAM_MAXLEN
from aegis.comply.logging import get_logger
from aegis.comply.schemas import ComplianceVerdict, ComplianceVerdictResult, RiskCategory

_log = get_logger("aegis.comply.bridge.phase4")


@runtime_checkable
class StreamPublisher(Protocol):
    """Anything with an async ``xadd(stream, fields, *, maxlen, approximate)``."""

    async def xadd(  # pragma: no cover - structural type
        self,
        stream: str,
        fields: dict[str, Any],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> Any: ...


@runtime_checkable
class KillSwitch(Protocol):
    """Anything with an async ``trip(reason)``."""

    async def trip(self, reason: str) -> Any:  # pragma: no cover - structural type
        ...


def to_escalation_fields(result: ComplianceVerdictResult, *, tenant_id: str) -> dict[str, Any]:
    """Build the Redis-stream ``{"body": <json>}`` payload (Phase 4 convention)."""
    body = {
        "content_id": result.content_id,
        "tenant_id": tenant_id,
        "trend_id": result.trend_id,
        "verdict": result.verdict.value,
        "risk_score": result.risk_score,
        "confidence": result.confidence,
        "blocking_reasons": list(result.blocking_reasons),
        "remediation": list(result.remediation),
        "engine_version": result.engine_version,
        "checked_at": result.checked_at.isoformat(),
    }
    # Phase 4 IntakeWorker reads the "body" field (never "payload").
    return {"body": json.dumps(body, separators=(",", ":"))}


def _is_critical(result: ComplianceVerdictResult) -> bool:
    cats = {cs.category for cs in result.category_scores if cs.score >= 1.0}
    return RiskCategory.TRADEMARK in cats and RiskCategory.COUNTERFEIT in cats


async def maybe_escalate(
    result: ComplianceVerdictResult,
    *,
    tenant_id: str,
    publisher: StreamPublisher | None = None,
    killswitch: KillSwitch | None = None,
    mode: str = "advisory",
) -> bool:
    """Publish an escalation for ``block`` verdicts; optionally trip killswitch.

    Returns ``True`` if an escalation was published. Never raises — escalation is
    best-effort and must not break the calling pipeline.
    """
    if result.verdict is not ComplianceVerdict.BLOCK:
        return False
    published = False
    if publisher is not None:
        try:
            await publisher.xadd(
                COMPLIANCE_ESCALATION_STREAM,
                to_escalation_fields(result, tenant_id=tenant_id),
                maxlen=STREAM_MAXLEN,
                approximate=True,
            )
            published = True
        except Exception as exc:
            _log.warning("comply.escalation_publish_failed", error=str(exc))

    if killswitch is not None and mode == "live" and _is_critical(result):
        try:
            await killswitch.trip(
                f"compliance critical block: {result.trend_id} ({result.content_id[:12]})"
            )
            _log.error("comply.killswitch_tripped", trend_id=result.trend_id)
        except Exception as exc:
            _log.warning("comply.killswitch_trip_failed", error=str(exc))

    return published
