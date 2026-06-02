"""Deterministic, network-free trademark screener.

The local screen fuzzy-matches the candidate's title and brand mentions against
the protected-brand registry using stdlib ``difflib``. It is the always-on path
and the one covered by tests; live lookups (``clients.py``) are an opt-in
enrichment layered on top.

ASSUMPTIONS
-----------
- Token windows of length 1-2 are checked, so multi-word marks
  ("louis vuitton") match.
- A *near* match (typosquat band) is still surfaced as a match — deliberate
  misspellings are the dominant counterfeit-listing tactic.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from aegis.comply.constants import TRADEMARK_SIMILARITY_THRESHOLD
from aegis.comply.schemas import ComplianceRequest, TrademarkMatch
from aegis.comply.trademark.brands import PROTECTED_BRANDS


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _windows(text: str) -> set[str]:
    """Return 1- and 2-gram token windows from ``text`` (lowercased)."""
    tokens = [t for t in text.lower().replace("/", " ").split() if t]
    grams: set[str] = set(tokens)
    for i in range(len(tokens) - 1):
        grams.add(f"{tokens[i]} {tokens[i + 1]}")
    return grams


class TrademarkScreener:
    """Screens candidate text against the protected-brand registry.

    Parameters
    ----------
    threshold:
        Minimum ``difflib`` ratio (or exact substring) to count as a match.
    registry:
        Optional override of the protected-brand mapping (for tests).
    """

    def __init__(
        self,
        *,
        threshold: float = TRADEMARK_SIMILARITY_THRESHOLD,
        registry: dict[str, tuple] | None = None,
    ) -> None:
        self._threshold = threshold
        self._registry = registry if registry is not None else PROTECTED_BRANDS

    def screen(self, request: ComplianceRequest) -> list[TrademarkMatch]:
        """Return all protected-mark matches for ``request`` (deduped by mark)."""
        haystack = f"{request.title} {' '.join(request.brand_mentions)}"
        grams = _windows(haystack)
        best: dict[str, TrademarkMatch] = {}

        for mark, (owner, jur, _cat) in self._registry.items():
            mark_l = mark.lower()
            # Exact substring -> similarity 1.0.
            if mark_l in haystack.lower():
                best[mark] = TrademarkMatch(
                    mark=mark,
                    owner=owner,
                    jurisdiction=jur,
                    similarity=1.0,
                    source="local",
                    matched_token=mark_l,
                )
                continue
            # Otherwise fuzzy-match against comparable-length token windows.
            top_sim = 0.0
            top_tok = ""
            n_words = mark_l.count(" ") + 1
            for gram in grams:
                if abs((gram.count(" ") + 1) - n_words) > 0:
                    continue
                sim = _similarity(gram, mark_l)
                if sim > top_sim:
                    top_sim, top_tok = sim, gram
            if top_sim >= self._threshold:
                best[mark] = TrademarkMatch(
                    mark=mark,
                    owner=owner,
                    jurisdiction=jur,
                    similarity=round(top_sim, 4),
                    source="local",
                    matched_token=top_tok,
                )
        return sorted(best.values(), key=lambda m: m.similarity, reverse=True)
