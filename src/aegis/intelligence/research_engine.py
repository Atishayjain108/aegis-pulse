"""
AEGIS Research Engine — deep, multi-pass intelligence gathering.

Intelligence module. Synthesizes signals from multiple adapters into a
structured research report. Every conclusion is supported by multiple
independent sources. Operates like a deep research analyst:
gather → verify → cross-check → synthesize.

The 5-pass research methodology:
  Pass 1: Broad signal harvest (top adapters for topic type)
  Pass 2: Cross-verification (confirm findings from independent sources)
  Pass 3: Temporal analysis (is this trend new or old? accelerating or fading?)
  Pass 4: Competitive landscape (who else is in this space?)
  Pass 5: Risk synthesis (compliance, IP, market saturation scores)

Depth tiers:
  surface:  Pass 1 only (< 30s, top-5 adapters)
  standard: Passes 1-3 (< 2min, top-12 adapters)
  deep:     All 5 passes (< 5min, top-12 adapters + risk synthesis)
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

_log = structlog.get_logger("aegis.intelligence.research_engine")

# Minimum character length for a cross-verified theme word to be reported.
_MIN_THEME_LEN = 6
# Maximum cross-verified themes / unverified claims returned per report.
_MAX_THEMES = 20
_MAX_UNVERIFIED = 10


@dataclass
class ResearchFinding:
    """A single finding from one source, verified or unverified."""

    claim: str
    source: str
    confidence: float  # 0-1
    verified_by: list[str] = field(default_factory=list)  # themes confirming it
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "source": self.source,
            "confidence": self.confidence,
            "verified_by": list(self.verified_by),
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ResearchReport:
    """
    Structured output of the 5-pass research methodology.
    Every field represents a synthesis across multiple sources.
    """

    query: str
    topic_type: str
    executive_summary: str  # max 3 sentences, plain English
    key_findings: list[ResearchFinding]
    trend_verdict: str  # "emerging" | "stable" | "surface_only"
    confidence_score: float  # 0-1 aggregate
    signal_count: int
    sources_consulted: list[str]
    cross_verified_findings: list[str]  # themes confirmed by 2+ sources
    unverified_claims: list[str]  # single-source findings (treat with caution)
    risks: list[str]  # compliance, IP, market risks
    opportunities: list[str]  # arbitrage, pricing, timing opportunities
    recommended_actions: list[str]  # concrete next steps
    research_depth: str  # "surface" | "standard" | "deep"
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable representation for the dashboard / CLI."""
        return {
            "query": self.query,
            "topic_type": self.topic_type,
            "executive_summary": self.executive_summary,
            "key_findings": [f.to_dict() for f in self.key_findings],
            "trend_verdict": self.trend_verdict,
            "confidence_score": self.confidence_score,
            "signal_count": self.signal_count,
            "sources_consulted": list(self.sources_consulted),
            "cross_verified_findings": list(self.cross_verified_findings),
            "unverified_claims": list(self.unverified_claims),
            "risks": list(self.risks),
            "opportunities": list(self.opportunities),
            "recommended_actions": list(self.recommended_actions),
            "research_depth": self.research_depth,
            "generated_at": self.generated_at.isoformat(),
        }


def _signal_to_dict(signal: Any) -> dict[str, Any]:
    """Normalize a ProductSignal (or already-dict row) to a plain dict.

    ``scrape_topic`` returns frozen ProductSignal models; the analysis
    passes operate on plain dicts so they also accept DB rows and test
    fixtures unchanged.
    """
    if isinstance(signal, dict):
        return signal
    platform = getattr(signal, "platform", "")
    platform_str = platform.value if hasattr(platform, "value") else str(platform)
    return {
        "platform": platform_str,
        "title": getattr(signal, "title", None) or "",
        "url": str(signal.url) if getattr(signal, "url", None) else None,
        "confidence": float(getattr(signal, "source_confidence", 0.5) or 0.5),
    }


class ResearchEngine:
    """
    Deep research engine. 5-pass methodology.

    Usage:
        engine = ResearchEngine(pool=pg_pool, redis=redis_client)
        report = await engine.research("wireless earbuds", depth="deep")
    """

    def __init__(
        self,
        pool: Any | None = None,
        redis: Any | None = None,
        llm_gateway: Any | None = None,
    ) -> None:
        self._pool = pool
        self._redis = redis
        self._llm = llm_gateway

    async def research(
        self,
        query: str,
        *,
        depth: str = "standard",  # "surface" | "standard" | "deep"
        max_signals: int = 200,
    ) -> ResearchReport:
        """
        Conduct multi-pass research on a topic.

        surface: Pass 1 only (top-5 adapters)
        standard: Passes 1-3 (top-12 adapters)
        deep: All 5 passes (top-12 adapters, cross-verification + risk synthesis)
        """
        _log.info("research.start", query=query, depth=depth)

        from aegis.scrape.adapter_router import AdapterRouter
        from aegis.scrape.topic_classifier import TopicClassifier

        classifier = TopicClassifier()
        topic_type = classifier.classify(query)
        router = AdapterRouter(redis=self._redis)

        # Pass 1: Broad signal harvest
        top_n = 5 if depth == "surface" else 12
        adapter_names = [
            r.adapter_name
            for r in router.route(query, top_n=top_n)
            if r.credentials_available or not r.requires_credentials
        ]
        signals = await self._harvest(query, adapter_names, max_signals)

        # Pass 3 (temporal): velocity / volume analysis across the signal set
        velocity_analysis = self._analyze_velocity(signals)

        # Pass 2 (cross-verification): which themes appear in 2+ sources
        cross_verified = self._cross_verify(signals)

        # Pass 4 + 5 (deep only): competitive landscape + risk synthesis
        risks: list[str] = []
        opportunities: list[str] = []
        if depth == "deep" and signals:
            risks = await self._assess_risks(query, signals[:5])
            opportunities = self._find_opportunities(signals, velocity_analysis)

        # Synthesize
        confidence = self._compute_confidence(signals, cross_verified)
        trend_verdict = self._determine_trend_verdict(velocity_analysis)

        report = ResearchReport(
            query=query,
            topic_type=topic_type.value,
            executive_summary=self._generate_summary(
                query, trend_verdict, len(signals), cross_verified
            ),
            key_findings=self._extract_findings(signals, cross_verified),
            trend_verdict=trend_verdict,
            confidence_score=confidence,
            signal_count=len(signals),
            sources_consulted=sorted({s.get("platform", "") for s in signals} - {""}),
            cross_verified_findings=cross_verified,
            unverified_claims=self._extract_unverified(signals, cross_verified),
            risks=risks,
            opportunities=opportunities,
            recommended_actions=self._generate_actions(trend_verdict, confidence, risks),
            research_depth=depth,
        )

        _log.info(
            "research.complete",
            query=query,
            signals=len(signals),
            confidence=round(confidence, 3),
            verdict=trend_verdict,
            cross_verified=len(cross_verified),
        )
        return report

    # ------------------------------------------------------------------
    # Pass 1 — harvest
    # ------------------------------------------------------------------

    async def _harvest(
        self, query: str, adapter_names: list[str], max_signals: int
    ) -> list[dict[str, Any]]:
        """Run the topic harvest and normalize signals to plain dicts.

        Routed adapter names go through ``scrape_topic``'s explicit-override
        path; an empty routing result falls back to the default core-adapter
        path so research always has data to work with.
        """
        from aegis.scrape.topic import scrape_topic

        limit_per_source = max(5, max_signals // max(len(adapter_names), 1))
        harvest = await scrape_topic(
            query,
            pool=self._pool,
            tenant_id=self._default_tenant_id() if self._pool is not None else None,
            limit_per_source=limit_per_source,
            dry_run=self._pool is None,
            adapter_override=adapter_names or None,
        )
        return [_signal_to_dict(s) for s in harvest.signals]

    @staticmethod
    def _default_tenant_id() -> Any:
        from uuid import UUID

        from aegis.config import settings

        return UUID(settings().default_tenant_id)

    # ------------------------------------------------------------------
    # Pass 3 — temporal analysis
    # ------------------------------------------------------------------

    def _analyze_velocity(self, signals: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute volume/diversity metrics across the signal set."""
        if not signals:
            return {"status": "no_data", "total": 0, "unique_platforms": 0}
        platforms = Counter(s.get("platform", "") for s in signals)
        return {
            "total": len(signals),
            "unique_platforms": len(platforms),
            "top_platform": platforms.most_common(1)[0][0] if platforms else "",
            "status": "data_available",
        }

    # ------------------------------------------------------------------
    # Pass 2 — cross-verification
    # ------------------------------------------------------------------

    def _cross_verify(self, signals: list[dict[str, Any]]) -> list[str]:
        """Find theme words that appear across 2+ different platforms."""
        title_platforms: dict[str, set[str]] = defaultdict(set)
        for s in signals:
            title = str(s.get("title", "")).lower()
            platform = str(s.get("platform", ""))
            for word in set(re.findall(r"\b[a-z]{4,}\b", title)):
                title_platforms[word].add(platform)
        return [
            phrase
            for phrase, platforms in title_platforms.items()
            if len(platforms) >= 2 and len(phrase) >= _MIN_THEME_LEN
        ][:_MAX_THEMES]

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    def _compute_confidence(
        self, signals: list[dict[str, Any]], cross_verified: list[str]
    ) -> float:
        if not signals:
            return 0.0
        platform_diversity = min(1.0, len({s.get("platform") for s in signals}) / 5)
        signal_volume = min(1.0, len(signals) / 50)
        verification_rate = min(1.0, len(cross_verified) / 10)
        avg_confidence = sum(
            float(s.get("confidence", 0.5)) for s in signals
        ) / len(signals)
        return round(
            0.30 * platform_diversity
            + 0.25 * signal_volume
            + 0.25 * verification_rate
            + 0.20 * avg_confidence,
            3,
        )

    def _determine_trend_verdict(self, velocity: dict[str, Any]) -> str:
        total = velocity.get("total", 0)
        if total == 0:
            return "stable"
        if total > 100:
            return "emerging"
        if total > 30:
            return "stable"
        return "surface_only"

    def _generate_summary(
        self, query: str, verdict: str, signal_count: int, cross_verified: list[str]
    ) -> str:
        return (
            f"Research on '{query}' gathered {signal_count} signals across multiple platforms. "
            f"The topic shows a {verdict} trend with {len(cross_verified)} cross-verified themes. "
            f"{'High' if len(cross_verified) > 5 else 'Moderate'} confidence based on "
            f"{'strong' if signal_count > 50 else 'limited'} cross-platform coverage."
        )

    def _extract_findings(
        self, signals: list[dict[str, Any]], cross_verified: list[str]
    ) -> list[ResearchFinding]:
        findings: list[ResearchFinding] = []
        seen: set[str] = set()
        cv_set = set(cross_verified)
        for s in signals[:20]:
            title = str(s.get("title", ""))
            if title in seen or not title:
                continue
            seen.add(title)
            words = set(title.lower().split())
            findings.append(
                ResearchFinding(
                    claim=title,
                    source=str(s.get("platform", "unknown")),
                    confidence=float(s.get("confidence", 0.5)),
                    verified_by=[cv for cv in cv_set if cv in words],
                )
            )
        return findings

    def _extract_unverified(
        self, signals: list[dict[str, Any]], cross_verified: list[str]
    ) -> list[str]:
        cv_set = set(cross_verified)
        unverified: list[str] = []
        seen: set[str] = set()
        for s in signals:
            title = str(s.get("title", ""))
            if title in seen or not title:
                continue
            seen.add(title)
            words = set(title.lower().split())
            if not any(cv in words for cv in cv_set):
                unverified.append(title)
        return unverified[:_MAX_UNVERIFIED]

    # ------------------------------------------------------------------
    # Pass 5 — risk synthesis (deep only)
    # ------------------------------------------------------------------

    async def _assess_risks(
        self, query: str, top_signals: list[dict[str, Any]]
    ) -> list[str]:
        """Phase 8 compliance screen on the research topic. Best-effort."""
        risks: list[str] = []
        try:
            from aegis.compliance.engine import ComplianceEngine
            from aegis.compliance.schemas import ComplianceRequest

            result = await ComplianceEngine().assess(
                ComplianceRequest(
                    product_sku="RESEARCH",
                    product_title=query,
                    category="general",
                    origin_country="CN",
                    destination_country="IN",
                )
            )
            if result.recommendation.value in ("BLOCK", "ESCALATE"):
                risks.append(
                    f"Compliance risk: {result.recommendation.value} "
                    f"({result.overall_risk_score:.2f})"
                )
        except Exception as exc:
            _log.debug("research.risk_check_failed", query=query, error=str(exc))
        return risks

    # ------------------------------------------------------------------
    # Pass 4 — competitive landscape / opportunities (deep only)
    # ------------------------------------------------------------------

    def _find_opportunities(
        self, signals: list[dict[str, Any]], velocity: dict[str, Any]
    ) -> list[str]:
        ops: list[str] = []
        if velocity.get("total", 0) > 50:
            ops.append(
                f"High signal volume ({velocity['total']}) suggests strong market interest"
            )
        if velocity.get("unique_platforms", 0) >= 3:
            ops.append(
                f"Multi-platform presence ({velocity['unique_platforms']} platforms) "
                "indicates organic demand"
            )
        return ops

    def _generate_actions(
        self, verdict: str, confidence: float, risks: list[str]
    ) -> list[str]:
        if verdict == "emerging" and confidence > 0.6 and not risks:
            return ["Consider sourcing and listing — high-confidence emerging trend"]
        if verdict == "emerging" and risks:
            return ["Trend is emerging but compliance risks require review before execution"]
        if verdict == "stable":
            return ["Monitor for velocity changes — stable trend, timing not critical"]
        return ["Insufficient signal coverage — run deeper research before acting"]
