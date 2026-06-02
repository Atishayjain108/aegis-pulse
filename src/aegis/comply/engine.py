"""``ComplianceEngine`` — the single Phase 8 entry point.

``evaluate`` is synchronous, network-free, and deterministic: it produces a
complete verdict from rules + trademark + counterfeit + matrix using only the
local registry. ``aevaluate`` optionally enriches with live trademark lookups
and LLM-augmented reasoning, but always begins from the deterministic result and
can only make it *more* restrictive.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from aegis.comply import VERSION
from aegis.comply.counterfeit.detector import CounterfeitDetector
from aegis.comply.logging import get_logger
from aegis.comply.matrix.scorer import RiskMatrixScorer
from aegis.comply.rules.base import RuleRegistry
from aegis.comply.rules.loader import default_registry
from aegis.comply.schemas import (
    ComplianceRequest,
    ComplianceVerdictResult,
    CounterfeitSignal,
    RuleHit,
    TrademarkMatch,
)
from aegis.comply.settings import ComplySettings, get_settings
from aegis.comply.trademark.screener import TrademarkScreener

_log = get_logger("aegis.comply.engine")


class ComplianceEngine:
    """Deterministic, fail-closed compliance gate.

    All collaborators are injectable for testing/reproducibility, including the
    clock (``clock`` returns a tz-aware UTC ``datetime``).
    """

    def __init__(
        self,
        *,
        settings: ComplySettings | None = None,
        registry: RuleRegistry | None = None,
        screener: TrademarkScreener | None = None,
        detector: CounterfeitDetector | None = None,
        scorer: RiskMatrixScorer | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._registry = registry or default_registry()
        self._screener = screener or TrademarkScreener(
            threshold=self._settings.trademark_similarity_threshold
        )
        self._detector = detector or CounterfeitDetector(
            zscore_floor=self._settings.counterfeit_price_zscore_floor
        )
        self._scorer = scorer or RiskMatrixScorer(
            flag_threshold=self._settings.flag_risk_threshold,
            block_threshold=self._settings.block_risk_threshold,
        )
        self._clock = clock or (lambda: datetime.now(UTC))

    def evaluate(self, request: ComplianceRequest) -> ComplianceVerdictResult:
        """Deterministic, network-free compliance verdict for ``request``."""
        rule_hits = self._registry.evaluate(request)
        tm_matches = self._screener.screen(request)
        cf_signals = self._detector.detect(request, tm_matches)

        (
            category_scores,
            aggregate,
            verdict,
            confidence,
            blocking_reasons,
        ) = self._scorer.score(
            rule_hits=rule_hits,
            trademark_matches=tm_matches,
            counterfeit_signals=cf_signals,
            live_source_used=False,
        )

        remediation = self._collect_remediation(rule_hits, tm_matches, cf_signals)
        reasoning = self._deterministic_reasoning(
            verdict, rule_hits, tm_matches, cf_signals
        )

        result = ComplianceVerdictResult(
            trend_id=request.trend_id,
            verdict=verdict,
            risk_score=aggregate,
            confidence=confidence,
            category_scores=category_scores,
            rule_hits=tuple(rule_hits),
            trademark_matches=tuple(tm_matches),
            counterfeit_signals=tuple(cf_signals),
            blocking_reasons=blocking_reasons,
            remediation=remediation,
            reasoning=reasoning,
            jurisdictions=request.target_jurisdictions,
            engine_version=VERSION,
            augmented=False,
            checked_at=self._clock(),
        )
        _log.info(
            "comply.evaluated",
            trend_id=request.trend_id,
            verdict=verdict.value,
            risk=aggregate,
            rules=len(rule_hits),
            trademarks=len(tm_matches),
            counterfeit=len(cf_signals),
        )
        return result

    async def aevaluate(
        self,
        request: ComplianceRequest,
        *,
        augmentor: object | None = None,
    ) -> ComplianceVerdictResult:
        """Async path: deterministic core, then optional LLM augmentation.

        Live trademark enrichment is intentionally not wired here by default
        (``enable_live_trademark_lookup`` is opt-in and would be injected via the
        screener). The augmentor may only *escalate* the verdict.
        """
        result = self.evaluate(request)
        if augmentor is not None and self._settings.enable_llm_augmentation:
            try:
                result = await augmentor.augment(result, request)  # type: ignore[attr-defined]
            except Exception as exc:
                _log.warning("comply.augment_failed", trend_id=request.trend_id, error=str(exc))
        return result

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _collect_remediation(
        rule_hits: Sequence[RuleHit],
        tm_matches: Sequence[TrademarkMatch],
        cf_signals: Sequence[CounterfeitSignal],
    ) -> tuple[str, ...]:
        out: list[str] = []
        for h in rule_hits:
            if h.remediation and h.remediation not in out:
                out.append(h.remediation)
        if tm_matches:
            out.append(
                "Verify you hold rights or authorisation to sell branded goods; "
                "remove protected marks from titles/imagery otherwise."
            )
        if cf_signals:
            out.append(
                "Counterfeit risk detected — confirm supplier authenticity and "
                "documentation before listing."
            )
        return tuple(out)

    @staticmethod
    def _deterministic_reasoning(
        verdict: object,
        rule_hits: Sequence[RuleHit],
        tm_matches: Sequence[TrademarkMatch],
        cf_signals: Sequence[CounterfeitSignal],
    ) -> str:
        bits: list[str] = [f"Verdict={verdict.value}."]
        if rule_hits:
            top = sorted(rule_hits, key=lambda h: h.severity.value, reverse=True)[:3]
            bits.append("Rules: " + ", ".join(f"{h.rule_id}({h.severity.value})" for h in top))
        if tm_matches:
            bits.append("Trademarks: " + ", ".join(m.mark for m in tm_matches[:3]))
        if cf_signals:
            bits.append(
                "Counterfeit: " + ", ".join(f"{s.brand}({s.risk:.2f})" for s in cf_signals[:3])
            )
        if not (rule_hits or tm_matches or cf_signals):
            bits.append("No compliance signals detected.")
        return " ".join(bits)
