"""
aegis.execution_intel.explain — Decision Explainability (Rule 9).

Turns an ``ExecutionScoreVector`` + ``SurvivabilityScore`` into plain-language
answers to the six required questions. Every answer is grounded: it cites the
measured value, or it says the input is UNVERIFIED — it never invents a reason.

This is pure, read-only formatting over already-computed evidence. No I/O.
"""

from __future__ import annotations

from aegis.execution_intel.schemas import (
    ExecutionExplanation,
    ExecutionScoreVector,
    SurvivabilityScore,
)


def _pct(v: float | None) -> str:
    return "UNVERIFIED" if v is None else f"{v * 100:.0f}%"


def explain_plan(
    vector: ExecutionScoreVector,
    survivability: SurvivabilityScore,
    *,
    supplier_name: str | None = None,
    region: str = "GLOBAL",
    category: str = "general",
) -> ExecutionExplanation:
    """Answer the six Rule 9 questions from measured evidence only."""

    # Why now — confidence is the time-sensitivity signal we actually measure.
    if vector.confidence is None:
        why_now = (
            "Timing confidence is UNVERIFIED — no predictor confidence is "
            "attached to this plan, so 'why now' cannot be justified."
        )
    else:
        why_now = (
            f"Predictor confidence is {_pct(vector.confidence)}; "
            f"evidence strength is {_pct(vector.evidence)}."
        )

    # Why this opportunity — evidence + historical execution success.
    why_opp = (
        f"Underlying evidence score {_pct(vector.evidence)}; similar settled "
        f"plans succeeded {_pct(vector.execution)} of the time"
        + (
            "."
            if vector.execution is not None
            else " (no settled history yet — UNVERIFIED)."
        )
    )

    # Why this supplier — measured fulfillment trust, or honest abstention.
    if supplier_name is None:
        why_supplier = "No supplier is attached — supplier reliability UNVERIFIED."
    elif vector.trust is None:
        why_supplier = (
            f"Supplier '{supplier_name}' has no settled fulfillment history — "
            f"trust UNVERIFIED; reliability cannot be claimed (Rule 1)."
        )
    else:
        why_supplier = (
            f"Supplier '{supplier_name}' has a measured fulfillment trust of "
            f"{_pct(vector.trust)} from settled orders."
        )

    # Why this buyer — demand is a proxy; buyer trust is structurally UNVERIFIED.
    why_buyer = (
        "Buyer trust is UNVERIFIED: AEGIS has no real order/customer data. "
        "Demand is a signal-derived PROXY only "
        f"(survivability demand mode: {survivability.mode_survival.get('demand', 'n/a')})."
    )

    # Why this region — region drives the demand proxy lookup.
    why_region = (
        f"Region '{region}' / category '{category}' selected the demand proxy "
        f"and any region-specific signals used above."
    )

    # Why this risk level — risk + survivability, kept separate (Rule 6).
    why_risk = (
        f"Risk {_pct(vector.risk)} and survivability {_pct(vector.survivability)} "
        f"are reported on separate axes (never merged). "
        + (
            survivability.reason
            if survivability.abstained
            else f"Survivability rests on: {survivability.basis}."
        )
    )

    return ExecutionExplanation(
        plan_id=vector.plan_id,
        why_now=why_now,
        why_this_opportunity=why_opp,
        why_this_supplier=why_supplier,
        why_this_buyer=why_buyer,
        why_this_region=why_region,
        why_this_risk_level=why_risk,
    )
