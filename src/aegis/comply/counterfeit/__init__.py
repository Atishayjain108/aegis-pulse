"""Counterfeit-detection subpackage."""

from __future__ import annotations

from aegis.comply.counterfeit.detector import (
    CounterfeitDetector,
    ImageEmbedder,
    price_zscore,
)

__all__ = ["CounterfeitDetector", "ImageEmbedder", "price_zscore"]
