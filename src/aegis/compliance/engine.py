"""
ComplianceEngine — Phase 8 main orchestrator.

Runs all compliance checks in parallel, computes weighted composite risk,
applies BLOCK / ESCALATE / PROCEED thresholds, caches results, and writes
an immutable audit entry (Phase 12 AuditLogger integration when available).
"""

from __future__ import annotations

import asyncio
import time

import structlog

from aegis.compliance.aml import AMLChecker
from aegis.compliance.cache import ComplianceCache
from aegis.compliance.config import ComplianceSettings
from aegis.compliance.constants import ERR_ASSESSMENT_FAILED, RISK_WEIGHTS
from aegis.compliance.counterfeit import CounterfeitDetector
from aegis.compliance.fda import FDAChecker
from aegis.compliance.ftc import FTCRuleEngine
from aegis.compliance.ipr import IPRChecker
from aegis.compliance.privacy import PrivacyRiskAssessor
from aegis.compliance.schemas import (
    ComplianceRequest,
    ComplianceRiskAssessment,
    FDAEnforcement,
    FTCViolation,
    PatentMatch,
    PrivacyRisk,
    Recommendation,
    RiskBreakdown,
    SanctionMatch,
    TrademarkMatch,
)

_log = structlog.get_logger("aegis.compliance.engine")


class ComplianceEngine:
    """
    Phase 8 Regulatory & Compliance Engine.

    Singleton-friendly: hold one instance per process to reuse HTTP client
    connection pools and in-process caches.

    Usage::

        engine = ComplianceEngine()
        assessment = await engine.assess(ComplianceRequest(
            product_sku="SKU-001",
            product_title="Classic Cotton T-Shirt",
            origin_country="CN",
            destination_country="US",
        ))
        if assessment.recommendation == Recommendation.BLOCK:
            raise ComplianceBlockError(str(assessment.reasons))
    """

    def __init__(self, settings: ComplianceSettings | None = None) -> None:
        self._cfg = settings or ComplianceSettings()
        self._ipr = IPRChecker(self._cfg)
        self._fda = FDAChecker(self._cfg)
        self._ftc = FTCRuleEngine()
        self._privacy = PrivacyRiskAssessor()
        self._aml = AMLChecker(self._cfg)
        self._counterfeit = CounterfeitDetector(self._cfg)
        self._cache = ComplianceCache(
            ttl_seconds=self._cfg.result_cache_ttl_hours * 3600
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def assess(self, request: ComplianceRequest) -> ComplianceRiskAssessment:
        """Run full compliance assessment for a product + trade route.

        Returns a cached result when the same (sku, title, origin, dest) was
        assessed within the TTL window.

        Never raises — on unexpected error returns ESCALATE assessment with
        error_code set so the caller can decide.
        """
        t0 = time.monotonic()

        cache_key = ComplianceCache.make_key(
            request.product_sku,
            request.product_title,
            request.origin_country,
            request.destination_country,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            _log.debug("compliance.cache_hit", sku=request.product_sku)
            # Return a copy marked as cached (frozen model → model_copy)
            return cached.model_copy(update={"cached": True})  # type: ignore[return-value]

        try:
            assessment = await self._run_checks(request, t0)
        except Exception as exc:
            _log.error(
                "compliance.assessment_error",
                sku=request.product_sku,
                error=str(exc)[:200],
                error_code=ERR_ASSESSMENT_FAILED,
            )
            assessment = ComplianceRiskAssessment(
                product_sku=request.product_sku,
                overall_risk_score=0.5,
                risk_breakdown=RiskBreakdown(),
                recommendation=Recommendation.ESCALATE,
                reasons=[f"Assessment error: {exc!s}"],
                duration_ms=(time.monotonic() - t0) * 1000,
                error_code=ERR_ASSESSMENT_FAILED,
            )

        self._cache.set(cache_key, assessment)
        self._emit_audit(request, assessment)
        return assessment

    async def assess_many(
        self, requests: list[ComplianceRequest]
    ) -> list[ComplianceRiskAssessment]:
        """Assess multiple products concurrently."""
        return await asyncio.gather(*[self.assess(r) for r in requests])

    # ------------------------------------------------------------------
    # Core check orchestration
    # ------------------------------------------------------------------

    async def _run_checks(
        self, req: ComplianceRequest, t0: float
    ) -> ComplianceRiskAssessment:
        # Run I/O-bound checks concurrently; sync checks are fast (< 1 ms)
        (
            (tm_matches, tm_risk),
            (patent_matches, patent_risk),
            (fda_enforcements, fda_risk),
            (sanction_matches, aml_risk),
            (counterfeit_signals, counterfeit_risk),
        ) = await asyncio.gather(
            self._ipr.check_trademark(req.product_title, req.product_description),
            self._ipr.check_patent(req.product_title, req.product_description),
            self._fda.check_product(req.product_title, req.origin_country, req.category),
            self._aml.check_transaction(req.origin_country, req.destination_country),
            self._counterfeit.detect(
                req.product_title,
                req.product_description,
                req.category,
                req.price_usd,
                req.product_image_url,
            ),
        )

        # Sync checks (pure rule engines — no I/O)
        ftc_violations, ftc_risk = self._ftc.assess(
            req.product_title, req.product_description
        )
        privacy_risk_obj, privacy_risk = self._privacy.assess(
            req.product_title,
            req.product_description,
            req.origin_country,
            req.destination_country,
            req.category,
        )

        # Weighted composite risk
        breakdown = RiskBreakdown(
            trademark=round(tm_risk, 4),
            patent=round(patent_risk, 4),
            fda=round(fda_risk, 4),
            counterfeit=round(counterfeit_risk, 4),
            ftc=round(ftc_risk, 4),
            privacy=round(privacy_risk, 4),
            aml=round(aml_risk, 4),
        )
        overall = (
            RISK_WEIGHTS["trademark"]   * tm_risk
            + RISK_WEIGHTS["patent"]    * patent_risk
            + RISK_WEIGHTS["fda"]       * fda_risk
            + RISK_WEIGHTS["counterfeit"] * counterfeit_risk
            + RISK_WEIGHTS["ftc"]       * ftc_risk
            + RISK_WEIGHTS["privacy"]   * privacy_risk
            + RISK_WEIGHTS["aml"]       * aml_risk
        )
        overall = round(min(1.0, overall), 4)

        # Hard overrides: OFAC sanction or FDA ban always forces BLOCK
        hard_block = (
            any(m.risk_score >= 0.99 for m in sanction_matches)
            or fda_risk >= 0.90
            or any(s.confidence >= 0.90 and s.signal_type == "replica_keyword"
                   for s in counterfeit_signals)
        )
        # Hard escalate: severe FTC violations require human review even if composite
        # score is below the escalate threshold (FTC weight is 10% — a solo FTC score
        # of 1.0 only contributes 0.10 to the composite, not enough to hit 0.50).
        hard_escalate = ftc_risk >= 0.80 and not hard_block

        # PASS2-2C: outcome-adapted block threshold (falls back to the static
        # config on any failure). Hard overrides above are unaffected — an
        # OFAC hit or FDA ban blocks regardless of where the threshold sits.
        block_threshold = self._cfg.block_threshold
        try:
            from aegis.core.dynamic_thresholds import get_thresholds

            block_threshold = await (await get_thresholds()).get_comply_block(
                fallback=self._cfg.block_threshold
            )
        except Exception:
            block_threshold = self._cfg.block_threshold

        if hard_block or overall >= block_threshold:
            recommendation = Recommendation.BLOCK
        elif hard_escalate or overall >= self._cfg.escalate_threshold:
            recommendation = Recommendation.ESCALATE
        else:
            recommendation = Recommendation.PROCEED

        reasons = self._build_reasons(
            tm_risk, tm_matches,
            patent_risk, patent_matches,
            fda_risk, fda_enforcements,
            counterfeit_risk, counterfeit_signals,
            ftc_risk, ftc_violations,
            privacy_risk, privacy_risk_obj,
            aml_risk, sanction_matches,
        )

        duration_ms = (time.monotonic() - t0) * 1000

        _log.info(
            "compliance.assessment_complete",
            sku=req.product_sku,
            product=req.product_title[:60],
            route=f"{req.origin_country}→{req.destination_country}",
            overall_risk=overall,
            recommendation=recommendation.value,
            duration_ms=round(duration_ms, 1),
        )

        # Provenance (HALLU-2): record which dimensions queried a live external
        # API vs fell back to an offline rule engine / hardcoded list.
        data_sources = {
            "trademark": "live",   # IPRChecker → USPTO PatentsView + EUIPO TMview
            "patent": "live",      # IPRChecker → USPTO PatentsView
            "fda": "live",         # FDAChecker → OpenFDA enforcement API
            "counterfeit": "live" if self._cfg.clip_enabled else "static",
            "ftc": "static",       # zero-I/O regex rule engine
            "privacy": "static",   # offline jurisdiction rule engine
            "aml": "live" if self._cfg.trade_gov_api_key else "static",
        }

        return ComplianceRiskAssessment(
            product_sku=req.product_sku,
            overall_risk_score=overall,
            risk_breakdown=breakdown,
            recommendation=recommendation,
            reasons=reasons,
            trademark_matches=list(tm_matches),
            patent_matches=list(patent_matches),
            fda_enforcements=list(fda_enforcements),
            sanction_matches=list(sanction_matches),
            counterfeit_signals=list(counterfeit_signals),
            ftc_violations=list(ftc_violations),
            privacy_risk=privacy_risk_obj,
            duration_ms=round(duration_ms, 1),
            data_sources=data_sources,
        )

    # ------------------------------------------------------------------
    # Reason builder
    # ------------------------------------------------------------------

    def _build_reasons(
        self,
        tm_risk: float, tm_matches: list[TrademarkMatch],
        patent_risk: float, patent_matches: list[PatentMatch],
        fda_risk: float, fda_enforcements: list[FDAEnforcement],
        counterfeit_risk: float, counterfeit_signals: list,
        ftc_risk: float, ftc_violations: list[FTCViolation],
        privacy_risk: float, privacy_risk_obj: PrivacyRisk,
        aml_risk: float, sanction_matches: list[SanctionMatch],
    ) -> list[str]:
        reasons: list[str] = []

        if tm_matches and tm_risk > 0.3:
            top = tm_matches[0]
            reasons.append(
                f"Trademark risk {tm_risk:.0%}: "
                f'"{top.registered_mark}" owned by {top.owner} ({top.jurisdiction})'
            )

        if patent_matches and patent_risk > 0.2:
            top = patent_matches[0]
            reasons.append(
                f"Patent risk {patent_risk:.0%}: {top.patent_title[:80]} "
                f"[{top.patent_number}] status={top.status}"
            )

        if fda_risk > 0.1:
            if fda_enforcements:
                top = fda_enforcements[0]
                reasons.append(
                    f"FDA risk {fda_risk:.0%}: enforcement {top.recall_number} — {top.reason[:100]}"
                )
            else:
                reasons.append(f"FDA risk {fda_risk:.0%}: product matches banned-keyword list")

        if counterfeit_signals and counterfeit_risk > 0.2:
            top = counterfeit_signals[0]
            reasons.append(f"Counterfeit risk {counterfeit_risk:.0%}: {top.description[:120]}")

        for v in ftc_violations[:3]:
            reasons.append(
                f"FTC violation [{v.violation_type}] severity {v.severity:.0%}: "
                f'"{v.matched_text[:80]}" — {v.rule_reference}'
            )

        if privacy_risk_obj and privacy_risk_obj.regulations_triggered:
            reasons.append(
                f"Privacy risk {privacy_risk:.0%}: {', '.join(privacy_risk_obj.regulations_triggered)} "
                f"— {privacy_risk_obj.details[:120]}"
            )

        for s in sanction_matches:
            reasons.append(
                f"Sanctions risk {s.risk_score:.0%}: {s.match_type} {s.matched_value} "
                f"[{s.program}] source={s.source}"
            )

        return reasons

    # ------------------------------------------------------------------
    # Audit integration (Phase 12 AuditLogger — optional)
    # ------------------------------------------------------------------

    def _emit_audit(
        self,
        request: ComplianceRequest,
        assessment: ComplianceRiskAssessment,
    ) -> None:
        try:
            from aegis.security import AuditLogger  # type: ignore[import-not-found]

            # Fire-and-forget; errors silently logged, never re-raised
            async def _write() -> None:
                try:
                    async with AuditLogger() as logger:
                        await logger.log(
                            event="compliance.assessment",
                            actor=request.tenant_id,
                            resource=f"sku:{request.product_sku}",
                            outcome=assessment.recommendation.value,
                            metadata={
                                "overall_risk": assessment.overall_risk_score,
                                "assessment_id": assessment.assessment_id,
                                "reasons": assessment.reasons[:5],
                            },
                        )
                except Exception as exc:
                    _log.debug("compliance.audit_write_failed", error=str(exc)[:80])

            import asyncio as _asyncio
            try:
                loop = _asyncio.get_running_loop()
                _task = loop.create_task(_write())
                _task  # noqa: B018  # keep reference to prevent GC before completion
            except RuntimeError:
                pass

        except (ImportError, ModuleNotFoundError):
            pass  # Phase 12 not installed — audit is optional
