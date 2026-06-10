"""
Honeypot detector.

Two complementary detectors:
  * `score_url(url)` — URL-level patterns (paths known to be traps).
  * `score_element(element)` — DOM-level patterns (hidden links, trap classes).

Both produce a `HoneypotVerdict` with a score in [0,1] and a list of reasons.
Verdicts are produced from cheap deterministic checks — no LLM, no network,
no JavaScript execution.

`HoneypotElement` is a deliberately minimal duck-typed dict. The Phase 1
Playwright integration calls `score_element({...})` with the relevant fields
extracted from the live page. This keeps the detector pure and unit-testable
without needing a real browser.
"""

from __future__ import annotations

from typing import TypedDict
from urllib.parse import urlparse

from aegis.harden.constants import (
    HONEYPOT_ATTR_TOKENS,
    HONEYPOT_BLOCK_THRESHOLD,
    HONEYPOT_CLASS_TOKENS,
    HONEYPOT_STYLE_PATTERNS,
)
from aegis.harden.metrics import M
from aegis.harden.schemas import HoneypotVerdict


class HoneypotElement(TypedDict, total=False):
    """Minimum data the detector needs about a DOM element."""

    href: str
    tag: str
    text: str
    classes: list[str]
    attrs: dict[str, str]
    style: str
    rect: dict[str, float]  # {"x":..,"y":..,"w":..,"h":..}


# ---------------------------------------------------------------------------
# URL-level
# ---------------------------------------------------------------------------

# Paths that public corpora have flagged as honeypots / bot traps.
_URL_TRAP_TOKENS: tuple[str, ...] = (
    "/honeypot",
    "/trap",
    "/donotvisit",
    "/do-not-visit",
    "/bot-trap",
    "/.well-known/security-trap",
    "?bot=trap",
    "?donotclick=1",
)


def score_url(url: str) -> HoneypotVerdict:
    """Score a URL on a [0,1] honeypot scale."""
    url_l = (url or "").lower()
    reasons: list[str] = []
    score = 0.0

    if not url_l:
        return HoneypotVerdict.make(url="", score=0.0, reasons=())

    # Token hits — each adds 0.8; one explicit trap token blocks on its own.
    for tok in _URL_TRAP_TOKENS:
        if tok in url_l:
            reasons.append(f"url-token:{tok}")
            score += 0.8

    # Suspicious path: paths that contain words like "ad", "track" alone
    # are *not* honeypots, but combinations of trap-y substrings are.
    try:
        parsed = urlparse(url)
    except ValueError:
        parsed = None
    if parsed is not None:
        path = (parsed.path or "").lower()
        if path.startswith("/.") and "trap" in path:
            reasons.append("hidden-path-trap")
            score += 0.4
        if path.endswith("/donotclick") or path.endswith("/honeypot"):
            reasons.append("trap-suffix")
            score += 0.7

    score = min(1.0, score)
    verdict = HoneypotVerdict.make(url=url, score=score, reasons=tuple(reasons))
    _emit_metric(verdict)
    return verdict


# ---------------------------------------------------------------------------
# DOM-level
# ---------------------------------------------------------------------------


def score_element(el: HoneypotElement) -> HoneypotVerdict:
    """Score a DOM-element snapshot.

    The score is the sum of independent contributions, clipped to 1.0. Each
    contribution is calibrated so a single weak signal (e.g. opacity:0) is a
    WARN and two or more signals are a BLOCK.
    """
    reasons: list[str] = []
    score = 0.0
    href = (el.get("href") or "").strip()

    # 1. Class names containing known trap tokens (each: +0.75 — strong enough
    #    on its own to block. Trap class names like "donotclick" or "honeypot"
    #    are unambiguous: legitimate sites do not use them.)
    classes = [c.lower() for c in el.get("classes") or []]
    for tok in HONEYPOT_CLASS_TOKENS:
        for c in classes:
            if tok in c:
                reasons.append(f"class:{c}")
                score += 0.75
                break

    # 2. Custom data attributes signaling traps (each: +0.5)
    attrs = el.get("attrs") or {}
    for attr_name in attrs:
        al = attr_name.lower()
        for tok in HONEYPOT_ATTR_TOKENS:
            if tok in al:
                reasons.append(f"attr:{attr_name}")
                score += 0.5
                break

    # 3. Inline style hiding the element (each pattern: +0.45)
    style = (el.get("style") or "").lower().replace(" ", "")
    for pat in HONEYPOT_STYLE_PATTERNS:
        pat_n = pat.replace(" ", "")
        if pat_n in style:
            reasons.append(f"style:{pat}")
            score += 0.45
            break  # one style signal is enough; don't double-count similar rules

    # 4. Zero-area bounding rect (+0.5)
    rect = el.get("rect")
    if isinstance(rect, dict):
        w = float(rect.get("w", 0))
        h = float(rect.get("h", 0))
        if w <= 0.1 or h <= 0.1:
            reasons.append("rect:zero-area")
            score += 0.5
        # Offscreen: large negative coordinates
        x = float(rect.get("x", 0))
        y = float(rect.get("y", 0))
        if x < -5_000 or y < -5_000:
            reasons.append("rect:offscreen")
            score += 0.45

    # 5. The link itself looks like a trap (combine URL signal: +0.5)
    if href:
        url_verdict = score_url(href)
        if url_verdict.blocked:
            reasons.extend([f"href:{r}" for r in url_verdict.reasons])
            score += 0.5

    # 6. Empty visible text + presence of href is mildly suspicious (+0.15)
    text = (el.get("text") or "").strip()
    if href and not text:
        reasons.append("empty-text")
        score += 0.15

    score = min(1.0, score)
    verdict = HoneypotVerdict.make(url=href, score=score, reasons=tuple(reasons))
    _emit_metric(verdict)
    return verdict


# ---------------------------------------------------------------------------
# Batch helper — useful when Playwright dumps the whole anchor list
# ---------------------------------------------------------------------------


def filter_safe(elements: list[HoneypotElement]) -> list[HoneypotElement]:
    """Return only the elements whose verdict is NOT blocked."""
    return [el for el in elements if not score_element(el).blocked]


# ---------------------------------------------------------------------------
# Metric emission
# ---------------------------------------------------------------------------


def _emit_metric(v: HoneypotVerdict) -> None:
    if v.blocked:
        # Pick the strongest reason for the metric label
        label = v.reasons[0] if v.reasons else "other"
        # Tame the cardinality: collapse to category prefix
        cat = label.split(":", 1)[0] if ":" in label else label
        M.honeypot_blocks_total.labels(reason=cat).inc()
    elif v.score >= HONEYPOT_BLOCK_THRESHOLD - 0.001 or v.warn:  # near miss for completeness
        M.honeypot_warns_total.inc()


__all__ = ["HoneypotElement", "filter_safe", "score_element", "score_url"]
