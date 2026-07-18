"""
Phase 4 bridge.

The Phase 4 `IntakeWorker` reads from a Redis stream and produces
`ComposerInput` records that feed the alert composer. Phase 5 publishes
its verdicts onto a sibling stream (`aegis:phase5:verdicts`) using the same
JSON envelope shape so the existing intake logic can be reused with a
trivial config tweak.

This module never imports `aegis.execute.*` — it only emits a JSON payload
that conforms to the Phase 4 ComposerInput protocol. Doing it this way:
  * Keeps Phase 5 free of Phase 4 import dependencies.
  * Means a Phase 5 instance can run with no Phase 4 code on the box
    (e.g., a stand-alone scrape worker that just dumps verdicts).
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from aegis.harden.schemas import HardenVerdict
from aegis.harden.utils.logging import get_logger

_log = get_logger("aegis.harden.bridge.phase4")


class _StreamClientLike(Protocol):
    """Duck-typed Redis-streams client. Compatible with `redis.asyncio.Redis`."""

    async def xadd(  # type: ignore[no-untyped-def]
        self, name: str, fields: dict, *args, **kwargs
    ) -> Any: ...


# Mapping HardenVerdict.verdict → Phase 4 ComposerInput vocabulary.
# Phase 4 uses ENTER / HOLD / BLOCK upper-case strings.
_VERDICT_TO_PHASE4: dict[str, str] = {
    "proceed": "ENTER",
    "warn": "HOLD",
    "block": "BLOCK",
}


def to_phase4_payload(v: HardenVerdict) -> dict[str, str]:
    """Build the JSON-stringified Redis fields for a single verdict.

    Mirrors the shape Phase 4's intake worker expects. The payload is a
    *single* `data` field whose value is a JSON document, plus a few
    top-level routing fields kept as strings for Redis Streams compatibility.
    """
    doc = {
        "schema": "harden_verdict.v1",
        "trend_id": v.trend_id,
        "phase5_source": v.source,
        # Phase 4 expects uppercase vocab — same convention as the Phase 2 → 4
        # mapping documented in CLAUDE.md.
        "verdict": _VERDICT_TO_PHASE4[v.verdict],
        "score": v.score,
        "confidence": v.confidence,
        "reason_code": v.reason_code,
        "detail": v.detail,
        "generated_at": v.generated_at.isoformat(),
    }
    return {
        "data": json.dumps(doc, separators=(",", ":"), sort_keys=True),
        "trend_id": v.trend_id or "",
        "source": f"phase5/{v.source}",
    }


async def publish(
    client: _StreamClientLike,
    stream: str,
    v: HardenVerdict,
    *,
    maxlen: int = 10_000,
) -> str | None:
    """XADD the verdict onto `stream`. Returns the stream entry id or None on failure.

    Errors are swallowed and logged — publishing is best-effort and must never
    prevent the underlying scrape/inference work from completing.
    """
    try:
        fields = to_phase4_payload(v)
        entry_id = await client.xadd(stream, fields, maxlen=maxlen, approximate=True)
        _log.info("phase5_verdict_published", stream=stream, source=v.source, verdict=v.verdict)
        return str(entry_id) if entry_id is not None else None
    except Exception as exc:  # noqa: BLE001 — best-effort
        _log.warning("phase5_verdict_publish_failed", error=str(exc), source=v.source)
        return None


__all__ = ["publish", "to_phase4_payload"]
