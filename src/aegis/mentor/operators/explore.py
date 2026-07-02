"""ExploreOp — discover new markets & niches.

Wraps the autonomous :class:`aegis.scheduler.sentinel.MarketSentinel`, which
sweeps the keyless global-radar adapters, detects breakout patterns, and
auto-investigates the strongest into grounded reports. The operator surfaces
those discovered niches as findings — never invented, always from a real scan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from aegis.mentor.operators.base import OperatorContext, OperatorResult

if TYPE_CHECKING:
    from aegis.mentor.schemas import UserProfile

_log = structlog.get_logger("aegis.mentor.operators.explore")


class ExploreOp:
    """Market-discovery operator."""

    name = "explore"

    def __init__(self, sentinel: Any | None = None) -> None:
        self._sentinel = sentinel

    def _get_sentinel(self, context: OperatorContext) -> Any:
        if self._sentinel is not None:
            return self._sentinel
        from aegis.scheduler.sentinel import MarketSentinel

        return MarketSentinel(pool=context.pool, redis=context.redis)

    async def run(
        self,
        profile: UserProfile,
        request: str,
        context: OperatorContext,
    ) -> OperatorResult:
        sentinel = self._get_sentinel(context)
        try:
            scan = await sentinel.scan()
        except Exception as exc:  # never crash the fleet
            _log.warning("mentor.explore.failed", error=str(exc)[:200])
            return OperatorResult(
                operator=self.name,
                reasoning=f"Market scan failed: {type(exc).__name__}.",
                confidence=0.0,
            )

        reports = list(getattr(scan, "reports", []) or [])
        findings: list[str] = []
        sources: set[str] = set()
        for rep in reports:
            label = rep.get("label") or rep.get("query") or rep.get("topic")
            if not label:
                continue
            verdict = rep.get("verdict") or rep.get("trend_verdict") or ""
            findings.append(f"Emerging niche: {label}{f' ({verdict})' if verdict else ''}")
            for s in rep.get("sources_consulted", []) or rep.get("sources", []) or []:
                sources.add(str(s))

        breakouts = int(getattr(scan, "breakouts", 0) or 0)
        radar = int(getattr(scan, "radar_signals", 0) or 0)
        # Confidence reflects how much real signal the scan actually saw.
        confidence = min(1.0, 0.3 * bool(findings) + 0.1 * breakouts + min(0.4, radar / 150.0))

        reasoning = (
            f"Radar swept {radar} signals; {breakouts} breakout patterns; "
            f"{len(reports)} investigated niches."
            if radar
            else "Radar returned no signals (sources may be blocked)."
        )

        return OperatorResult(
            operator=self.name,
            findings=findings,
            actions=(
                ["Pick one emerging niche and run deep research on it."]
                if findings
                else ["Re-run discovery later — no breakouts surfaced this scan."]
            ),
            sources=sorted(sources),
            reasoning=reasoning,
            confidence=round(confidence, 3),
            data={
                "radar_signals": radar,
                "breakouts": breakouts,
                "niches": [r.get("label") or r.get("query") for r in reports],
            },
        )
