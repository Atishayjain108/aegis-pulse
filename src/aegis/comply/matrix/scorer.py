"""Risk-matrix scorer.

Aggregates rule hits, trademark matches, and counterfeit signals into per-
category scores, a weighted aggregate risk (0..1), and a fail-closed verdict.

Determinism + fail-closed are the two invariants:
- Any ``BLOCK``-severity hit forces ``BLOCK`` regardless of the aggregate.
- The verdict can only move toward *more* restriction as evidence accumulates.
"""

from __future__ import annotations

from aegis.comply.constants import (
    BLOCK_RISK_THRESHOLD,
    CONFIDENCE_BASE,
    CONFIDENCE_LIVE_SOURCE_BONUS,
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    DEFAULT_CATEGORY_WEIGHTS,
    FLAG_RISK_THRESHOLD,
    SEVERITY_SCORE_BLOCK,
    SEVERITY_SCORE_INFO,
    SEVERITY_SCORE_WARN,
)
from aegis.comply.schemas import (
    CategoryScore,
    ComplianceVerdict,
    CounterfeitSignal,
    RiskCategory,
    RuleHit,
    Severity,
    TrademarkMatch,
)

_SEVERITY_SCORE = {
    Severity.INFO: SEVERITY_SCORE_INFO,
    Severity.WARN: SEVERITY_SCORE_WARN,
    Severity.BLOCK: SEVERITY_SCORE_BLOCK,
}


class RiskMatrixScorer:
    """Combines compliance signals into category scores, aggregate, and verdict."""

    def __init__(
        self,
        *,
        weights: dict[str, float] | None = None,
        flag_threshold: float = FLAG_RISK_THRESHOLD,
        block_threshold: float = BLOCK_RISK_THRESHOLD,
    ) -> None:
        raw = weights or dict(DEFAULT_CATEGORY_WEIGHTS)
        total = sum(raw.values()) or 1.0
        self._weights = {RiskCategory(k): v / total for k, v in raw.items()}
        self._flag = flag_threshold
        self._block = block_threshold

    def score(
        self,
        *,
        rule_hits: list[RuleHit],
        trademark_matches: list[TrademarkMatch],
        counterfeit_signals: list[CounterfeitSignal],
        live_source_used: bool = False,
    ) -> tuple[tuple[CategoryScore, ...], float, ComplianceVerdict, float, tuple[str, ...]]:
        """Return (category_scores, aggregate, verdict, confidence, blocking_reasons)."""
        per_cat: dict[RiskCategory, float] = dict.fromkeys(self._weights, 0.0)
        cat_hits: dict[RiskCategory, list[RuleHit]] = {c: [] for c in self._weights}

        # 1. Rule-derived scores (max severity per category).
        for hit in rule_hits:
            cat_hits.setdefault(hit.category, []).append(hit)
            per_cat[hit.category] = max(
                per_cat.get(hit.category, 0.0), _SEVERITY_SCORE[hit.severity]
            )

        # 2. Trademark matches -> TRADEMARK category (exact match == strong).
        if trademark_matches:
            tm_score = max(m.similarity for m in trademark_matches)
            per_cat[RiskCategory.TRADEMARK] = max(
                per_cat.get(RiskCategory.TRADEMARK, 0.0),
                # An exact (1.0) match alone is FLAG-worthy, not auto-block:
                min(tm_score, 0.70),
            )

        # 3. Counterfeit signals -> COUNTERFEIT category.
        if counterfeit_signals:
            cf_score = max(s.risk for s in counterfeit_signals)
            per_cat[RiskCategory.COUNTERFEIT] = max(
                per_cat.get(RiskCategory.COUNTERFEIT, 0.0), cf_score
            )

        category_scores = tuple(
            CategoryScore(
                category=cat,
                score=round(per_cat.get(cat, 0.0), 4),
                weight=round(self._weights.get(cat, 0.0), 4),
                hits=tuple(cat_hits.get(cat, ())),
            )
            for cat in self._weights
        )

        aggregate = round(
            sum(cs.score * cs.weight for cs in category_scores), 4
        )

        # 4. Fail-closed verdict.
        block_hits = [h for h in rule_hits if h.severity is Severity.BLOCK]
        strong_counterfeit = [s for s in counterfeit_signals if s.risk >= 0.90]
        blocking_reasons: list[str] = []
        if block_hits:
            blocking_reasons.extend(f"{h.rule_id}: {h.name}" for h in block_hits)
        if strong_counterfeit:
            blocking_reasons.extend(
                f"counterfeit:{s.brand} (risk={s.risk:.2f})" for s in strong_counterfeit
            )

        if block_hits or strong_counterfeit or aggregate >= self._block:
            verdict = ComplianceVerdict.BLOCK
        elif aggregate >= self._flag or any(
            h.severity is Severity.WARN for h in rule_hits
        ) or trademark_matches:
            verdict = ComplianceVerdict.FLAG
        else:
            verdict = ComplianceVerdict.CLEAR

        # 5. Deterministic confidence.
        confidence = CONFIDENCE_BASE
        if live_source_used:
            confidence += CONFIDENCE_LIVE_SOURCE_BONUS
        # More corroborating signals -> more confident in a non-clear verdict.
        n_signals = len(rule_hits) + len(trademark_matches) + len(counterfeit_signals)
        if verdict is not ComplianceVerdict.CLEAR and n_signals >= 2:
            confidence += 0.10
        confidence = round(max(CONFIDENCE_MIN, min(CONFIDENCE_MAX, confidence)), 4)

        return category_scores, aggregate, verdict, confidence, tuple(blocking_reasons)
