"""
aegis.memory.verify — Reality Verification Layer (Rule 8).

Before a major recommendation, compose what AEGIS already knows into four
falsifiable scores: evidence, trust, reality (historical consistency), and
unknowns. ``passed`` is an ADVISORY gate — it does not block. The headline
success metric (Rule 12) is that opportunities passing the gate realise at a
higher rate than those that don't, measured out-of-sample.

No new model: evidence/unknowns are pure deterministic functions of the inputs;
trust reads Source Memory; reality reads the opportunity ledger's realized rate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from aegis.memory.opportunity import OpportunityMemory
from aegis.memory.schemas import RealityAssessment
from aegis.memory.source import SourceMemory
from aegis.memory.taxonomy import OpportunityType

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.memory.verify")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

# Saturation points for the evidence sub-scores.
_SIG_FULL = 50.0
_AUTH_FULL = 20.0
_PLAT_FULL = 5.0

# Advisory gate thresholds.
_MIN_REALITY = 0.5
_MIN_EVIDENCE = 0.4
_MIN_TRUST = 0.4

_PRIOR_REALITY = 0.5   # unknown history → neutral
_PRIOR_TRUST = 0.3


def score_evidence(
    *, signal_count: int, author_count: int, platform_count: int
) -> float:
    """Strength of the underlying signal evidence, weighted volume/diversity."""
    vol = min(1.0, signal_count / _SIG_FULL)
    div = min(1.0, author_count / _AUTH_FULL)
    breadth = min(1.0, platform_count / _PLAT_FULL)
    return round(0.5 * vol + 0.3 * div + 0.2 * breadth, 4)


def score_unknowns(
    *,
    signal_count: int | None,
    author_count: int | None,
    platform_count: int | None,
    source_platforms: list[str] | None,
    prediction_confidence: float | None,
) -> float:
    """1.0 when every expected input is present; lower as inputs go unknown."""
    fields = [
        signal_count,
        author_count,
        platform_count,
        source_platforms if source_platforms else None,
        prediction_confidence,
    ]
    known = sum(1 for f in fields if f is not None)
    return round(known / len(fields), 4)


class RealityVerifier:
    """Compose accumulated knowledge into Reality/Trust/Evidence/Unknowns."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id
        self._opps = OpportunityMemory(db_pool, tenant_id=tenant_id)
        self._sources = SourceMemory(db_pool, tenant_id=tenant_id)

    async def _historical_reality(
        self, opportunity_type: OpportunityType, category: str
    ) -> float:
        """Realized rate for this (type, category) from the ledger; prior if none."""
        try:
            patterns = await self._opps.patterns(days_back=365, limit=200)
        except Exception as exc:
            _log.debug("memory.reality_patterns_failed", error=str(exc))
            return _PRIOR_REALITY
        for p in patterns:
            if (
                p["opportunity_type"] == opportunity_type.value
                and p["category"] == category
                and p.get("realized_rate") is not None
            ):
                return float(p["realized_rate"])
        return _PRIOR_REALITY

    async def assess(
        self,
        *,
        trend_key: str,
        opportunity_type: OpportunityType = OpportunityType.INFO_ARB,
        category: str = "general",
        signal_count: int | None = None,
        author_count: int | None = None,
        platform_count: int | None = None,
        source_platforms: list[str] | None = None,
        prediction_confidence: float | None = None,
    ) -> RealityAssessment:
        """Produce the four advisory scores for a candidate opportunity."""
        evidence = score_evidence(
            signal_count=signal_count or 0,
            author_count=author_count or 0,
            platform_count=platform_count or (len(source_platforms or []) or 0),
        )
        unknowns = score_unknowns(
            signal_count=signal_count,
            author_count=author_count,
            platform_count=platform_count,
            source_platforms=source_platforms,
            prediction_confidence=prediction_confidence,
        )

        trust = _PRIOR_TRUST
        if source_platforms:
            try:
                tmap = await self._sources.trust_for(source_platforms)
                if tmap:
                    trust = round(sum(tmap.values()) / len(tmap), 4)
            except Exception as exc:
                _log.debug("memory.reality_trust_failed", error=str(exc))

        reality = await self._historical_reality(opportunity_type, category)

        passed = (
            reality >= _MIN_REALITY
            and evidence >= _MIN_EVIDENCE
            and trust >= _MIN_TRUST
        )
        notes = (
            f"evidence={evidence} trust={trust} reality={reality} "
            f"unknowns={unknowns}"
        )
        return RealityAssessment(
            trend_key=trend_key,
            opportunity_type=opportunity_type,
            category=category,
            evidence_score=evidence,
            trust_score=trust,
            reality_score=round(reality, 4),
            unknowns_score=unknowns,
            passed=passed,
            notes=notes,
        )
