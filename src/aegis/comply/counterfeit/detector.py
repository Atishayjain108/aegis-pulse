"""Counterfeit-risk detector (deterministic core, optional image hook).

Combines two stdlib-only signals:

1. **Typosquat proximity** — a *near-but-not-exact* match to a protected brand
   (the typosquat band) is treated as deliberate evasion -> high risk.
2. **Price anomaly** — a price far below a protected-brand category baseline
   (Z-score <= floor) is a "too cheap to be genuine" signal.

An optional ``image_embedder`` hook enables CLIP-style visual similarity when a
GPU/model is configured, but the deterministic path never requires it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from aegis.comply.constants import (
    COUNTERFEIT_PRICE_ZSCORE_FLOOR,
    DEFAULT_PRICE_BASELINES_USD,
    TRADEMARK_TYPOSQUAT_HIGH,
    TRADEMARK_TYPOSQUAT_LOW,
)
from aegis.comply.schemas import (
    ComplianceRequest,
    CounterfeitSignal,
    TrademarkMatch,
)
from aegis.comply.trademark.brands import HIGH_COUNTERFEIT_CATEGORIES


@runtime_checkable
class ImageEmbedder(Protocol):
    """Structural type for an optional image embedder (e.g. CLIP)."""

    def embed(self, image_ref: str) -> list[float]:  # pragma: no cover - hook
        ...


def price_zscore(price: float, baseline: tuple[float, float]) -> float | None:
    """Z-score of ``price`` against a ``(mean, std)`` baseline. ``None`` if std<=0."""
    mean, std = baseline
    if std <= 0:
        return None
    return (price - mean) / std


class CounterfeitDetector:
    """Scores counterfeit risk from trademark matches + price.

    Parameters
    ----------
    baselines:
        Per-category ``(mean, std)`` price baselines in USD.
    zscore_floor:
        Price Z-score at/below which the price is "suspiciously cheap".
    image_embedder:
        Optional CLIP-style embedder; when supplied with a reference catalogue
        the detector can add a visual-similarity signal (not used by default).
    """

    def __init__(
        self,
        *,
        baselines: dict[str, tuple[float, float]] | None = None,
        zscore_floor: float = COUNTERFEIT_PRICE_ZSCORE_FLOOR,
        image_embedder: ImageEmbedder | None = None,
    ) -> None:
        self._baselines = baselines or dict(DEFAULT_PRICE_BASELINES_USD)
        self._floor = zscore_floor
        self._embedder = image_embedder

    def _baseline_for(self, request: ComplianceRequest) -> tuple[float, float]:
        if request.price_baseline is not None:
            return request.price_baseline
        cat = request.category.lower()
        for key, base in self._baselines.items():
            if key in cat:
                return base
        return self._baselines.get("general", (40.0, 30.0))

    def detect(
        self,
        request: ComplianceRequest,
        trademark_matches: list[TrademarkMatch],
    ) -> list[CounterfeitSignal]:
        """Return counterfeit signals for ``request`` given its trademark matches."""
        signals: list[CounterfeitSignal] = []
        baseline = self._baseline_for(request)
        z = price_zscore(request.price, baseline) if request.price is not None else None
        cheap = z is not None and z <= self._floor

        for match in trademark_matches:
            sim = match.similarity
            typosquat = TRADEMARK_TYPOSQUAT_LOW <= sim < TRADEMARK_TYPOSQUAT_HIGH
            owner_cat_high = self._high_exposure(match)

            risk = 0.0
            reasons: list[str] = []
            if typosquat:
                risk = max(risk, 0.85)
                reasons.append(f"typosquat of '{match.mark}' (sim={sim:.2f})")
            if cheap and sim >= TRADEMARK_TYPOSQUAT_LOW:
                risk = max(risk, 0.75)
                reasons.append(f"price Z={z:.2f} below '{match.mark}' baseline")
            if cheap and typosquat:
                risk = 0.95  # both signals -> near-certain counterfeit listing
            if owner_cat_high and sim >= 0.95 and cheap:
                risk = max(risk, 0.80)
                reasons.append("high-counterfeit category + exact brand + low price")

            if risk > 0.0:
                signals.append(
                    CounterfeitSignal(
                        brand=match.mark,
                        similarity=sim,
                        price_zscore=round(z, 3) if z is not None else None,
                        reason="; ".join(reasons),
                        risk=round(risk, 3),
                    )
                )
        return sorted(signals, key=lambda s: s.risk, reverse=True)

    @staticmethod
    def _high_exposure(match: TrademarkMatch) -> bool:
        # Category hint lives in the registry; we approximate from the mark here.
        from aegis.comply.trademark.brands import PROTECTED_BRANDS

        entry = PROTECTED_BRANDS.get(match.mark.lower())
        return bool(entry and entry[2] in HIGH_COUNTERFEIT_CATEGORIES)

    # Optional visual-similarity hook --------------------------------------
    def visual_similarity(self, image_ref: str, reference: list[float]) -> float | None:
        """Cosine similarity between ``image_ref`` and a reference embedding.

        Returns ``None`` when no embedder is configured (graceful degradation).
        """
        if self._embedder is None:
            return None
        vec = self._embedder.embed(image_ref)
        return _cosine(vec, reference)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
