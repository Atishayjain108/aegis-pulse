"""Alert composer.

The composer is the deterministic heart of Phase 4. Given a `ComposerInput`
that may carry Phase 2 only, Phase 3 only, or both, it produces exactly one
`Alert` whose verdict, score, confidence, and priority are computed from
numeric features alone.

Doctrine rules enforced here:
  1. Both inputs absent → AegisExecuteError(EXEC_COMPOSER_BOTH_INPUTS_MISSING)
  2. Phase 2 + Phase 3 trend mismatch → caller's responsibility; we don't
     re-check (bridges run on the same trend_id).
  3. Verdict is chosen by a precedence ladder:
       BLOCK (compliance / redteam veto)            — overrides everything
       EXIT  (Phase 3 high p_decline OR Phase 2 EXIT)
       ENTER (both sides agree it's a buy)
       HOLD  (default safe verdict)
       DEGRADED (only one side present AND it's weak)
  4. Score & confidence are weighted averages capped by [0, 1].
  5. LLM-generated narrative is preserved in `summary_text` but does not
     influence the verdict.
"""

from __future__ import annotations

from typing import Final

import structlog

from aegis.execute.bridge.types import ComposerInput
from aegis.execute.constants import (
    ENTER_MIN_CONFIDENCE,
    ENTER_MIN_SCORE,
    EXIT_MIN_DECLINE_PROB,
    EXIT_MIN_SATURATION,
    VERDICT_BLOCK,
    VERDICT_DEGRADED,
    VERDICT_ENTER,
    VERDICT_EXIT,
    VERDICT_HOLD,
)
from aegis.execute.errors import (
    EXEC_COMPOSER_BOTH_INPUTS_MISSING,
    EXEC_COMPOSER_INVALID_VERDICT,
    AegisExecuteError,
)
from aegis.execute.policy.classifier import classify_priority
from aegis.execute.schemas.alert import Alert, AlertSource
from aegis.execute.utils.hashing import compute_alert_id
from aegis.execute.utils.time import utc_now

_log = structlog.get_logger(__name__)

# Weights for blending Phase 2 + Phase 3 numeric outputs.
# Rationale: Phase 2 carries governance + narrative (compliance, redteam,
# hedge), Phase 3 carries quantitative forecasts. The blend leans slightly
# toward Phase 3 for confidence (quant signal usually tighter), and equally
# weights score.
_W_SCORE_P2: Final[float] = 0.5
_W_SCORE_P3: Final[float] = 0.5
_W_CONF_P2: Final[float] = 0.45
_W_CONF_P3: Final[float] = 0.55


def _has_phase2(ci: ComposerInput) -> bool:
    return ci.phase2_verdict is not None and ci.phase2_score is not None


def _has_phase3(ci: ComposerInput) -> bool:
    return (
        ci.phase3_p_breakout_24h is not None
        or ci.phase3_p_decline_6h is not None
        or ci.phase3_p_saturation is not None
        or ci.phase3_confidence is not None
    )


def _source_for(ci: ComposerInput) -> AlertSource:
    p2 = _has_phase2(ci)
    p3 = _has_phase3(ci)
    if p2 and p3:
        return AlertSource.PHASE2_AND_PHASE3
    if p2:
        return AlertSource.PHASE2_ONLY
    if p3:
        return AlertSource.PHASE3_ONLY
    raise AegisExecuteError(EXEC_COMPOSER_BOTH_INPUTS_MISSING)


def _phase3_score_proxy(ci: ComposerInput) -> float | None:
    """Phase 3 doesn't have a direct 'score' field — we synthesize one.

    score_p3 = max(p_breakout_24h, 0) * (1 - clip(p_decline_6h, 0, 1))

    This is a deterministic surrogate that aligns Phase 3 with Phase 2's
    [0, 1] score semantics. Saturated trends naturally get a lower score.
    """
    p_b = ci.phase3_p_breakout_24h
    p_d = ci.phase3_p_decline_6h or 0.0
    if p_b is None:
        return None
    p_d_clipped = max(0.0, min(1.0, float(p_d)))
    return max(0.0, min(1.0, float(p_b) * (1.0 - p_d_clipped)))


def _combined_score(ci: ComposerInput) -> float:
    p2_score = ci.phase2_score
    p3_score = _phase3_score_proxy(ci)
    if p2_score is None and p3_score is None:
        return 0.0
    if p2_score is None:
        return float(p3_score or 0.0)
    if p3_score is None:
        return float(p2_score)
    return max(0.0, min(1.0, _W_SCORE_P2 * p2_score + _W_SCORE_P3 * p3_score))


def _combined_confidence(ci: ComposerInput) -> float:
    p2_c = ci.phase2_confidence
    p3_c = ci.phase3_confidence
    if p2_c is None and p3_c is None:
        return 0.0
    if p2_c is None:
        return float(max(0.0, min(1.0, p3_c or 0.0)))
    if p3_c is None:
        return float(max(0.0, min(1.0, p2_c)))
    return max(0.0, min(1.0, _W_CONF_P2 * p2_c + _W_CONF_P3 * p3_c))


def _pick_verdict(
    ci: ComposerInput, *, combined_score: float, combined_confidence: float
) -> str:
    # 1. Veto: Phase 2 BLOCK is final.
    if ci.phase2_verdict == VERDICT_BLOCK:
        return VERDICT_BLOCK

    # 2. EXIT precedence: Phase 3 high decline OR saturation OR Phase 2 EXIT.
    p_decline = ci.phase3_p_decline_6h or 0.0
    p_saturation = ci.phase3_p_saturation or 0.0
    if (
        ci.phase2_verdict == VERDICT_EXIT
        or p_decline >= EXIT_MIN_DECLINE_PROB
        or p_saturation >= EXIT_MIN_SATURATION
    ):
        return VERDICT_EXIT

    # 3. ENTER requires score + confidence floors AND non-conflicting Phase 3.
    if (
        combined_score >= ENTER_MIN_SCORE
        and combined_confidence >= ENTER_MIN_CONFIDENCE
        and ci.phase2_verdict in (VERDICT_ENTER, None)
        and ci.phase3_policy_action in ("enter", None)
    ):
        return VERDICT_ENTER

    # 4. DEGRADED if only one side present and floors not met.
    only_phase2 = _has_phase2(ci) and not _has_phase3(ci)
    only_phase3 = _has_phase3(ci) and not _has_phase2(ci)
    if (only_phase2 or only_phase3) and combined_confidence < ENTER_MIN_CONFIDENCE:
        return VERDICT_DEGRADED

    # 5. Default: HOLD.
    return VERDICT_HOLD


def _build_title(ci: ComposerInput, verdict: str) -> str:
    if ci.phase2_title:
        return ci.phase2_title[:200]
    return f"Trend {ci.trend_id} — {verdict}"[:200]


def _build_summary(ci: ComposerInput, verdict: str) -> str:
    """Build the human-readable summary.

    Concatenates the LLM-generated narrative (if any) with deterministic
    numeric annotations. Never empty.
    """
    parts: list[str] = []
    if ci.phase2_narrative:
        parts.append(ci.phase2_narrative.strip())

    quant_bits: list[str] = []
    if ci.phase3_p_breakout_24h is not None:
        quant_bits.append(f"p_breakout(24h)={ci.phase3_p_breakout_24h:.2f}")
    if ci.phase3_p_decline_6h is not None:
        quant_bits.append(f"p_decline(6h)={ci.phase3_p_decline_6h:.2f}")
    if ci.phase3_expected_margin_usd is not None:
        quant_bits.append(f"E[margin]=${ci.phase3_expected_margin_usd:.2f}")
    if ci.phase3_loss_probability is not None:
        quant_bits.append(f"P[loss]={ci.phase3_loss_probability:.2f}")
    if quant_bits:
        parts.append("[" + " | ".join(quant_bits) + "]")

    parts.append(f"verdict={verdict}")
    summary = " ".join(parts).strip()
    return summary[:2000]


def compose(ci: ComposerInput) -> Alert:
    """Compose a deterministic `Alert` from a `ComposerInput`.

    Raises:
        AegisExecuteError(EXEC_COMPOSER_BOTH_INPUTS_MISSING) if neither side
        is present.
        AegisExecuteError(EXEC_COMPOSER_INVALID_VERDICT) if the picked
        verdict is outside the allowed set (defensive; should never fire).
    """
    if not _has_phase2(ci) and not _has_phase3(ci):
        raise AegisExecuteError(
            EXEC_COMPOSER_BOTH_INPUTS_MISSING,
            context={"trend_id": ci.trend_id},
        )

    source = _source_for(ci)
    combined_score = _combined_score(ci)
    combined_conf = _combined_confidence(ci)
    verdict = _pick_verdict(
        ci, combined_score=combined_score, combined_confidence=combined_conf
    )

    priority = classify_priority(
        verdict=verdict,
        composer_input=ci,
        combined_confidence=combined_conf,
        upstream_priority=ci.phase2_priority,
    )

    alert_id = compute_alert_id(
        tenant_id=ci.tenant_id,
        trend_id=ci.trend_id,
        decision_window=ci.decision_window,
        verdict=verdict,
        priority=priority,
    )

    halt_reason = ci.phase2_halt_reason if verdict == VERDICT_BLOCK else None
    blocked_by = ci.phase2_blocked_by if verdict == VERDICT_BLOCK else ()

    # Defensive guarantee for the BLOCK invariant in the Alert model.
    if verdict == VERDICT_BLOCK and not halt_reason and not blocked_by:
        halt_reason = "policy_block"

    try:
        alert = Alert(
            alert_id=alert_id,
            tenant_id=ci.tenant_id,
            trend_id=ci.trend_id,
            decision_window=ci.decision_window,
            verdict=verdict,
            priority=priority,
            score=combined_score,
            confidence=combined_conf,
            source=source,
            p_breakout_24h=ci.phase3_p_breakout_24h,
            p_decline_6h=ci.phase3_p_decline_6h,
            p_saturation=ci.phase3_p_saturation,
            expected_margin_usd=ci.phase3_expected_margin_usd,
            loss_probability=ci.phase3_loss_probability,
            halt_reason=halt_reason,
            blocked_by=blocked_by,
            title=_build_title(ci, verdict),
            summary_text=_build_summary(ci, verdict),
            correlation_id=ci.correlation_id,
            created_at=utc_now(),
        )
    except ValueError as exc:
        raise AegisExecuteError(
            EXEC_COMPOSER_INVALID_VERDICT,
            cause=exc,
            context={"trend_id": ci.trend_id, "verdict": verdict},
        ) from exc

    _log.info(
        "execute.compose",
        trend_id=ci.trend_id,
        verdict=verdict,
        priority=priority,
        score=round(combined_score, 4),
        confidence=round(combined_conf, 4),
        source=str(source),
        correlation_id=ci.correlation_id,
    )
    return alert


__all__ = ["compose"]
