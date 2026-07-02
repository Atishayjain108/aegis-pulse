"""Cover aegis.compliance.counterfeit.CounterfeitDetector layers 1-4 + risk."""

from __future__ import annotations

import pytest

from aegis.compliance.counterfeit import CounterfeitDetector


@pytest.fixture()
def det() -> CounterfeitDetector:
    return CounterfeitDetector()


async def test_exact_brand_match(det: CounterfeitDetector) -> None:
    signals, risk = await det.detect("Gucci Leather Handbag", category="luxury")
    assert any(s.signal_type == "brand_match" for s in signals)
    assert risk > 0.5


async def test_replica_keyword(det: CounterfeitDetector) -> None:
    signals, risk = await det.detect(
        "Designer Bag", product_description="AAA grade replica, 1:1 copy", category="bags"
    )
    assert any(s.signal_type == "replica_keyword" for s in signals)
    assert risk >= 0.9


async def test_price_anomaly(det: CounterfeitDetector) -> None:
    # luxury median 450; $5 is < 15% of it
    signals, risk = await det.detect("Generic Tote", category="luxury", price_usd=5.0)
    assert any(s.signal_type == "price_anomaly" for s in signals)
    assert risk > 0


async def test_clean_product_no_signals(det: CounterfeitDetector) -> None:
    signals, risk = await det.detect(
        "Plain Cotton Hand Towel", category="general", price_usd=12.0
    )
    assert signals == []
    assert risk == 0.0


async def test_fuzzy_brand_typosquat(det: CounterfeitDetector) -> None:
    # "guccii" is a near-miss of "gucci" (single-word brand fuzzy path)
    signals, _ = await det.detect("Guccii Premium Wallet", category="luxury")
    assert any(s.signal_type in ("fuzzy_brand", "brand_match") for s in signals)


async def test_prone_category_multiplier(det: CounterfeitDetector) -> None:
    # footwear is a counterfeit-prone category → 1.2x multiplier
    signals, risk = await det.detect(
        "Sneakers replica", category="footwear"
    )
    assert signals
    assert 0 < risk <= 1.0
