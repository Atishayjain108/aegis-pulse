"""Cover aegis.comply.counterfeit.detector.CounterfeitDetector (deterministic)."""

from __future__ import annotations

from aegis.comply.counterfeit.detector import CounterfeitDetector, _cosine, price_zscore
from aegis.comply.schemas import ComplianceRequest, TrademarkMatch


def _req(price: float | None = None, category: str = "general") -> ComplianceRequest:
    return ComplianceRequest(
        trend_id="t1", title="Item", category=category, price=price,
        price_baseline=(100.0, 20.0),
    )


def test_price_zscore_and_none_on_zero_std() -> None:
    assert price_zscore(60.0, (100.0, 20.0)) == -2.0
    assert price_zscore(60.0, (100.0, 0.0)) is None


def test_cosine_edge_and_value() -> None:
    assert _cosine([], [1.0]) == 0.0
    assert _cosine([0.0], [0.0]) == 0.0
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_detect_typosquat() -> None:
    det = CounterfeitDetector()
    # similarity 0.9 sits in the typosquat band [0.82, 0.995)
    matches = [TrademarkMatch(mark="Gucci", similarity=0.9)]
    out = det.detect(_req(category="luxury"), matches)
    assert out
    assert out[0].risk >= 0.85


def test_detect_cheap_and_typosquat_combined() -> None:
    det = CounterfeitDetector()
    matches = [TrademarkMatch(mark="Gucci", similarity=0.9)]
    # price 40 vs baseline (100,20) → z=-3 ≤ floor(-2) → cheap
    out = det.detect(_req(price=40.0, category="luxury"), matches)
    assert out[0].risk == 0.95


def test_detect_no_signal_for_exact_full_price() -> None:
    det = CounterfeitDetector()
    matches = [TrademarkMatch(mark="Gucci", similarity=0.999)]  # exact, above typosquat band
    out = det.detect(_req(price=100.0), matches)
    assert out == []


def test_visual_similarity_none_without_embedder() -> None:
    det = CounterfeitDetector()
    assert det.visual_similarity("img", [1.0, 0.0]) is None


def test_visual_similarity_with_embedder() -> None:
    class _Emb:
        def embed(self, image_ref: str) -> list[float]:
            return [1.0, 0.0]

    det = CounterfeitDetector(image_embedder=_Emb())
    assert det.visual_similarity("img", [1.0, 0.0]) == 1.0


def test_baseline_for_category_match() -> None:
    det = CounterfeitDetector(baselines={"luxury": (500.0, 100.0), "general": (40.0, 30.0)})
    # category contains "luxury" → uses that baseline; no explicit price_baseline
    req = ComplianceRequest(trend_id="t", title="x", category="luxury bags", price=10.0)
    out = det.detect(req, [TrademarkMatch(mark="Gucci", similarity=0.9)])
    assert out  # cheap vs luxury baseline + typosquat
