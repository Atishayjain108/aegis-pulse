"""Canonical data schemas for AEGIS Pulse.

The canonical ``ProductSignal`` type lives in ``signal.py``; enums used across
the system live in ``enums.py``. This ``__init__`` re-exports the commonly-
used names so downstream code can do ``from aegis.schemas import ProductSignal``.
"""

from __future__ import annotations

from aegis.schemas.enums import (
    ConfidenceBand,
    ContentModality,
    IntentType,
    Platform,
    ScrapeMethod,
    SourceTier,
    ToSRisk,
    confidence_band,
    platform_tier,
)
from aegis.schemas.signal import (
    Author,
    ConfidenceMetadata,
    CrossModalCoherence,
    EngagementMetrics,
    Location,
    MediaRef,
    Price,
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.schemas.swarm import SwarmResult, WaveStats

__all__ = [
    "Author",
    "ConfidenceBand",
    "ConfidenceMetadata",
    "ContentModality",
    "CrossModalCoherence",
    "EngagementMetrics",
    "IntentType",
    "Location",
    "MediaRef",
    "Platform",
    "Price",
    "ProductSignal",
    "ScrapeMethod",
    "ScrapeProvenance",
    "SourceTier",
    "SwarmResult",
    "ToSRisk",
    "WaveStats",
    "compute_content_hash",
    "confidence_band",
    "platform_tier",
]
