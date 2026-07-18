"""
Counterfeit product detector — Phase 8.

Multi-layer approach:
  Layer 1 (instant):  Exact luxury brand name in product title.
  Layer 2 (instant):  Fuzzy brand match (typosquatting, "Guci" → "Gucci").
  Layer 3 (instant):  Price anomaly (price << category median = suspicious).
  Layer 4 (instant):  Suspicious keyword patterns ("replica", "knockoff", "A+ grade").
  Layer 5 (optional): CLIP image-embedding similarity vs brand reference images.
                      Enable with AEGIS_COMPLY_CLIP_ENABLED=true + openai-clip installed.

No external API calls in layers 1–4.  CLIP requires network access only to
download model weights on first run (then cached locally).
"""

from __future__ import annotations

import difflib
import io
import re
from typing import Any

import structlog

from aegis.compliance.config import ComplianceSettings
from aegis.compliance.constants import (
    COUNTERFEIT_PRONE_CATEGORIES,
    KNOWN_TRADEMARK_BRANDS,
)
from aegis.compliance.schemas import CounterfeitSignal

_log = structlog.get_logger("aegis.compliance.counterfeit")

# Replica / counterfeit indicator keywords
_REPLICA_KEYWORDS: list[str] = [
    "replica", "knockoff", "knock-off", "knock off", "aaa grade", "a+ grade",
    "a++ grade", "super copy", "mirror copy", "1:1 copy", "high quality copy",
    "inspired by", "look-alike", "look alike", "same as original",
    "oem quality", "factory quality", "export quality",
]

# Compiled for speed
_REPLICA_RE = re.compile(
    "|".join(re.escape(kw) for kw in _REPLICA_KEYWORDS),
    re.IGNORECASE,
)

# Category median prices (USD) — same as Phase 7 CATEGORY_MEDIAN_PRICES_USD
# Products priced >85% below these thresholds trigger a price anomaly flag
_CATEGORY_MEDIANS_USD: dict[str, float] = {
    "apparel": 35.0,
    "footwear": 65.0,
    "luxury": 450.0,
    "electronics": 180.0,
    "watches": 200.0,
    "jewellery": 120.0,
    "jewelry": 120.0,
    "handbags": 95.0,
    "bags": 55.0,
    "cosmetics": 28.0,
    "beauty": 28.0,
    "perfume": 55.0,
    "fragrance": 55.0,
    "sunglasses": 40.0,
    "eyewear": 45.0,
    "toys": 22.0,
    "general": 40.0,
}

_PRICE_ANOMALY_THRESHOLD = 0.15  # price < 15% of category median = suspicious


class CounterfeitDetector:
    """Multi-layer counterfeit product detector."""

    def __init__(self, settings: ComplianceSettings | None = None) -> None:
        self._cfg = settings or ComplianceSettings()
        self._clip_model: Any = None
        self._clip_preprocess: Any = None
        self._clip_initialized = False

    async def detect(
        self,
        product_title: str,
        product_description: str = "",
        category: str = "general",
        price_usd: float | None = None,
        image_url: str | None = None,
    ) -> tuple[list[CounterfeitSignal], float]:
        """Return (signals, risk_score 0–1)."""
        signals: list[CounterfeitSignal] = []

        # Layer 1: exact brand name match
        exact_signals = self._check_exact_brands(product_title)
        signals.extend(exact_signals)

        # Layer 2: fuzzy brand match (if no exact hit already)
        if not exact_signals:
            fuzzy_signals = self._check_fuzzy_brands(product_title)
            signals.extend(fuzzy_signals)

        # Layer 3: price anomaly
        if price_usd is not None and price_usd > 0:
            price_signal = self._check_price_anomaly(price_usd, category)
            if price_signal:
                signals.append(price_signal)

        # Layer 4: replica keyword patterns
        replica_signals = self._check_replica_keywords(
            product_title, product_description
        )
        signals.extend(replica_signals)

        # Layer 5: CLIP image similarity (optional)
        if (
            image_url
            and self._cfg.clip_enabled
        ):
            clip_signal = await self._check_clip_similarity(image_url, product_title)
            if clip_signal:
                signals.append(clip_signal)

        risk = self._compute_risk(signals, category)

        if signals:
            _log.info(
                "compliance.counterfeit_detected",
                product=product_title[:60],
                signals=[s.signal_type for s in signals],
                risk_score=round(risk, 3),
            )

        return signals, risk

    # ------------------------------------------------------------------
    # Layer 1: exact brand match
    # ------------------------------------------------------------------

    def _check_exact_brands(self, title: str) -> list[CounterfeitSignal]:
        title_lower = title.lower()
        signals: list[CounterfeitSignal] = []
        for brand_name, owner, _category in KNOWN_TRADEMARK_BRANDS:
            if brand_name in title_lower and len(brand_name) >= 3:
                signals.append(
                    CounterfeitSignal(
                        signal_type="brand_match",
                        description=f'Product title contains registered mark "{brand_name.title()}" (owned by {owner}).',
                        matched_brand=brand_name.title(),
                        confidence=0.90,
                    )
                )
                break  # one strong exact hit is enough
        return signals

    # ------------------------------------------------------------------
    # Layer 2: fuzzy brand match
    # ------------------------------------------------------------------

    def _check_fuzzy_brands(self, title: str) -> list[CounterfeitSignal]:
        title_words = title.lower().split()
        signals: list[CounterfeitSignal] = []

        for brand_name, _owner, _ in KNOWN_TRADEMARK_BRANDS:
            brand_words = brand_name.split()
            if len(brand_words) == 1 and len(brand_name) >= 4:
                # Single-word brand — check each word in title
                for word in title_words:
                    ratio = difflib.SequenceMatcher(None, brand_name, word).ratio()
                    if 0.78 <= ratio < 0.99:  # fuzzy but not exact
                        signals.append(
                            CounterfeitSignal(
                                signal_type="fuzzy_brand",
                                description=(
                                    f'Word "{word}" in title is suspiciously similar to '
                                    f'registered mark "{brand_name.title()}" (similarity {ratio:.0%}).'
                                ),
                                matched_brand=brand_name.title(),
                                confidence=round(ratio * 0.75, 3),
                            )
                        )
                        break
            elif len(brand_words) > 1:
                # Multi-word brand — compare phrase similarity
                title_phrase = " ".join(title_words[:len(brand_words)])
                ratio = difflib.SequenceMatcher(None, brand_name, title_phrase).ratio()
                if ratio >= 0.80:
                    signals.append(
                        CounterfeitSignal(
                            signal_type="fuzzy_brand",
                            description=(
                                f'Title phrase "{title_phrase}" resembles registered mark '
                                f'"{brand_name.title()}" (similarity {ratio:.0%}).'
                            ),
                            matched_brand=brand_name.title(),
                            confidence=round(ratio * 0.70, 3),
                        )
                    )

        return signals[:3]  # cap fuzzy hits to avoid noise

    # ------------------------------------------------------------------
    # Layer 3: price anomaly
    # ------------------------------------------------------------------

    def _check_price_anomaly(self, price_usd: float, category: str) -> CounterfeitSignal | None:
        median = _CATEGORY_MEDIANS_USD.get(category.lower(), _CATEGORY_MEDIANS_USD["general"])
        if price_usd < median * _PRICE_ANOMALY_THRESHOLD:
            ratio = price_usd / median
            return CounterfeitSignal(
                signal_type="price_anomaly",
                description=(
                    f"Price ${price_usd:.2f} is {ratio:.0%} of the {category} category "
                    f"median (${median:.2f}). Extreme underpricing is a counterfeit indicator."
                ),
                confidence=min(0.80, 0.90 - ratio * 2),
            )
        return None

    # ------------------------------------------------------------------
    # Layer 4: replica keyword patterns
    # ------------------------------------------------------------------

    def _check_replica_keywords(
        self, title: str, description: str
    ) -> list[CounterfeitSignal]:
        combined = f"{title} {description}"
        match = _REPLICA_RE.search(combined)
        if match:
            return [
                CounterfeitSignal(
                    signal_type="replica_keyword",
                    description=(
                        f'Product text contains counterfeit indicator: "{match.group(0)}". '
                        "Selling replicas violates trademark law in all jurisdictions."
                    ),
                    confidence=0.95,
                )
            ]
        return []

    # ------------------------------------------------------------------
    # Layer 5: CLIP image similarity (optional)
    # ------------------------------------------------------------------

    async def _check_clip_similarity(
        self, image_url: str, product_title: str
    ) -> CounterfeitSignal | None:
        """Compare product image against luxury brand text descriptors using CLIP."""
        try:
            import clip  # type: ignore[import-untyped]
            import httpx as _httpx
            import torch
            from PIL import Image  # type: ignore[import-untyped]

            if not self._clip_initialized:
                self._clip_model, self._clip_preprocess = clip.load(self._cfg.clip_model)
                self._clip_model.eval()
                self._clip_initialized = True

            # Fetch image
            async with _httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(image_url)
            if resp.status_code != 200:
                return None

            image = Image.open(io.BytesIO(resp.content)).convert("RGB")
            image_input = self._clip_preprocess(image).unsqueeze(0)  # type: ignore[operator]

            # Text prompts representing known brand products
            brand_prompts = [
                f"authentic {brand} product" for brand, _, _ in KNOWN_TRADEMARK_BRANDS[:10]
            ] + ["counterfeit replica product", "generic unbranded product"]

            with torch.no_grad():
                image_features = self._clip_model.encode_image(image_input)
                text_tokens = clip.tokenize(brand_prompts)
                text_features = self._clip_model.encode_text(text_tokens)

                image_features = image_features / image_features.norm(dim=-1, keepdim=True)
                text_features = text_features / text_features.norm(dim=-1, keepdim=True)

                similarities = (image_features @ text_features.T).squeeze(0)
                brand_sims = similarities[:-2]
                max_brand_sim = float(brand_sims.max())
                float(similarities[-1])

            # High brand similarity + not generic = potential counterfeit
            if max_brand_sim > self._cfg.clip_threshold:
                best_brand_idx = int(brand_sims.argmax())
                best_brand = KNOWN_TRADEMARK_BRANDS[best_brand_idx][0]
                confidence = min(0.95, max_brand_sim)

                _log.info(
                    "compliance.clip_match",
                    product=product_title[:60],
                    brand=best_brand,
                    similarity=round(max_brand_sim, 3),
                )

                return CounterfeitSignal(
                    signal_type="clip_similarity",
                    description=(
                        f"CLIP image similarity {max_brand_sim:.2f} to "
                        f'"{best_brand.title()}" brand imagery exceeds threshold {self._cfg.clip_threshold}.'
                    ),
                    matched_brand=best_brand.title(),
                    confidence=confidence,
                )

        except ImportError:
            _log.debug("compliance.clip_not_installed")
        except Exception as exc:
            _log.debug("compliance.clip_error", error=str(exc)[:120])

        return None

    # ------------------------------------------------------------------
    # Risk computation
    # ------------------------------------------------------------------

    def _compute_risk(self, signals: list[CounterfeitSignal], category: str) -> float:
        if not signals:
            return 0.0

        # Base risk from highest-confidence signal
        base = max(s.confidence for s in signals)

        # Escalate for counterfeit-prone categories
        category_multiplier = 1.2 if category.lower() in COUNTERFEIT_PRONE_CATEGORIES else 1.0

        # Multiple independent signals are more damning
        multi_signal_bonus = min(0.15, (len(signals) - 1) * 0.07)

        return min(1.0, base * category_multiplier + multi_signal_bonus)
