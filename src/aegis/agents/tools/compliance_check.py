"""
Tool: compliance_check.

A rule-engine pass over a candidate trend. No external calls — pure
Python. Flags include:
  * trademarked-brand mentions (configurable list).
  * regulated-category words (FDA: drug claims, supplements;
    FTC: "guaranteed earnings", "lose 10 lbs in 7 days").
  * EU GPSR/DSA-flagged categories (children's products, electronics,
    toys, cosmetics) → mark as `requires_due_diligence`.
  * counterfeit-likelihood heuristics (price too low + brand mention).

The flag list is intentionally English-language only at this stage;
i18n is a Phase-3 milestone. It's still useful because most of our
sources are English-dominant.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import re

from .base import ToolResult, tool_call

# A conservative, easily-extended trademark fingerprint list. These
# are *examples*: the real system will load this from a YAML file
# in `config/compliance/`.
_TRADEMARK_PATTERNS: tuple[str, ...] = (
    r"\bnike\b",
    r"\badidas\b",
    r"\bgucci\b",
    r"\blouis\s+vuitton\b",
    r"\brolex\b",
    r"\bapple\b(?!\s+(?:juice|pie|sauce|cider|tree))",
    r"\bsamsung\b",
    r"\bsupreme\b",
    r"\bcoca[\s-]?cola\b",
    r"\bdisney\b",
    r"\bmarvel\b",
    r"\bharry\s+potter\b",
    r"\bchanel\b",
    r"\bhermes\b",
    r"\bprada\b",
    r"\byeezy\b",
    r"\btiffany\b",
    r"\blego\b",
)

_REGULATED_CLAIMS: tuple[tuple[str, str], ...] = (
    (r"\b(cure|cures|cured)\b", "ftc.health_claim"),
    (r"\bguaranteed\s+(?:earnings|income|results)\b", "ftc.earnings_claim"),
    (r"\blose\s+\d+\s*(?:lbs?|pounds?|kgs?)\s+in\s+\d+\s*(?:days?|weeks?)\b", "ftc.weight_loss"),
    (r"\bfda[-\s]?approved\b", "fda.approval_claim"),
    (r"\bclinically\s+proven\b", "ftc.clinical_claim"),
    (r"\b(?:treats?|prevents?)\s+(?:cancer|diabetes|alzheimer'?s)\b", "fda.disease_claim"),
    (r"\bmade\s+in\s+(?:usa|america)\b", "ftc.origin_claim"),  # only if false
    (r"\b(?:cbd|thc|delta[-\s]?[89])\b", "regulated.cannabis"),
    (r"\bvape\b|\bnicotine\b|\bvaping\b", "regulated.tobacco"),
    (r"\b(?:weapon|firearm|ammunition|silencer)\b", "regulated.firearms"),
)

_DUE_DILIGENCE_CATEGORIES: tuple[tuple[str, str], ...] = (
    (r"\b(?:toy|toys|stuffed\s+animal)\b", "category.children"),
    (r"\b(?:baby|infant|toddler)\b", "category.children"),
    (r"\bcosmetic\b|\bskincare\b|\bsunscreen\b", "category.cosmetics"),
    (r"\b(?:electronic|electrical|charger|battery|powerbank)\b", "category.electronics"),
    (r"\b(?:supplement|vitamin|protein\s+powder)\b", "category.supplements"),
    (r"\b(?:medical\s+device|thermometer|blood\s+pressure)\b", "category.medical_device"),
    (r"\b(?:laser\s+pointer|laser\s+pen)\b", "category.laser"),
)


def _compile(patterns: tuple) -> tuple:
    """Pre-compile regex patterns. Returns a list of (compiled_re, *meta)."""
    compiled = []
    for entry in patterns:
        if isinstance(entry, tuple):
            compiled.append((re.compile(entry[0], re.IGNORECASE), *entry[1:]))
        else:
            compiled.append((re.compile(entry, re.IGNORECASE),))
    return tuple(compiled)


_TRADEMARK_RX = _compile(_TRADEMARK_PATTERNS)
_REGULATED_RX = _compile(_REGULATED_CLAIMS)
_DUE_DILIGENCE_RX = _compile(_DUE_DILIGENCE_CATEGORIES)


@tool_call("compliance_check")
async def check(
    *,
    title: str,
    summary: str = "",
    representative_text: str = "",
    detected_price: float | None = None,
) -> ToolResult:
    """Run rule-engine over text. Returns flags and a recommendation."""
    text = " ".join((title, summary, representative_text)).lower()

    trademark_hits: list[str] = []
    for entry in _TRADEMARK_RX:
        rx = entry[0]
        m = rx.search(text)
        if m:
            trademark_hits.append(m.group(0))

    regulated_hits: list[dict[str, str]] = []
    for entry in _REGULATED_RX:
        rx, code = entry[0], entry[1]
        m = rx.search(text)
        if m:
            regulated_hits.append({"match": m.group(0), "code": code})

    dd_hits: list[dict[str, str]] = []
    for entry in _DUE_DILIGENCE_RX:
        rx, code = entry[0], entry[1]
        m = rx.search(text)
        if m:
            dd_hits.append({"match": m.group(0), "code": code})

    # Counterfeit heuristic: trademark match AND suspiciously low price.
    counterfeit_risk = bool(trademark_hits) and (
        detected_price is not None and detected_price <= 25.0
    )

    # Verdict: BLOCK on trademark or regulated; HOLD on due diligence;
    # PROCEED otherwise.
    if trademark_hits:
        verdict = "block"
        reason = f"trademark: {trademark_hits[0]}"
    elif regulated_hits:
        verdict = "block"
        reason = f"regulated: {regulated_hits[0]['code']}"
    elif dd_hits:
        verdict = "hold"
        reason = f"due_diligence: {dd_hits[0]['code']}"
    else:
        verdict = "proceed"
        reason = "no flags"

    return ToolResult.success(
        {
            "verdict": verdict,
            "reason": reason,
            "trademark_hits": trademark_hits,
            "regulated_hits": regulated_hits,
            "due_diligence_hits": dd_hits,
            "counterfeit_risk": counterfeit_risk,
            "flags": (
                [f"tm:{t}" for t in trademark_hits]
                + [f"reg:{h['code']}" for h in regulated_hits]
                + [f"dd:{h['code']}" for h in dd_hits]
            ),
        }
    )
