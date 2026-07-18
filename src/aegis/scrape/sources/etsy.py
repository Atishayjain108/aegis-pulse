"""Etsy Open API v3 adapter — free, official, key-gated, fail-open.

Uses Etsy's free **Open API v3** active-listings search: title, price (with
currency → price_usd via FX), `num_favorers` and `views` (genuine niche /
handmade demand signals), URL and tags. Simple API-key (keystring) auth — no
OAuth needed for public listing reads.

Free setup (founder, zero prior knowledge — see docs/FREE_COMMERCE_SETUP.md):
  1. Sign in at https://www.etsy.com/developers/ and "Create a New App".
  2. Fill the form (name + short description); approval is instant for read-only.
  3. Copy the **Keystring** (this is your API key).
  4. Export it:
        export AEGIS_ETSY_API_KEY=...
  No key? Logs `etsy.no_credentials` and fail-opens to [].

PLATFORM: etsy | TIER: T2_commerce
ToS Risk: GREEN — official public API within documented terms.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from math import log1p
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from aegis.schemas.enums import (
    ContentModality,
    IntentType,
    Platform,
    ScrapeMethod,
    SourceTier,
    ToSRisk,
)
from aegis.schemas.signal import (
    ConfidenceMetadata,
    EngagementMetrics,
    Price,
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter
from aegis.scrape.http_client import get_or_create_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.etsy")

SCRAPER_VERSION = "etsy-0.1.0"

_SEARCH_URL = "https://openapi.etsy.com/v3/application/listings/active"


def _api_key() -> str | None:
    key = os.environ.get("AEGIS_ETSY_API_KEY", "").strip()
    return key or None


def _price(raw: dict[str, Any]) -> tuple[Decimal | None, str]:
    price = raw.get("price") or {}
    try:
        amount = Decimal(str(price.get("amount")))
        divisor = Decimal(str(price.get("divisor") or 100))
        value = amount / divisor if divisor else amount
    except (InvalidOperation, TypeError):
        value = None
    currency = str(price.get("currency_code") or "USD").upper()
    return value, currency


@dataclass(frozen=True, slots=True)
class EtsyConfig(AdapterConfig):
    """Etsy Open API v3 adapter configuration."""

    name: str = "etsy"
    per_source_rps: float = 2.0  # free tier: 10 req/s, 10k/day
    timeout_seconds: float = 20.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False


class EtsyAdapter(SourceAdapter[dict[str, Any]]):
    """Etsy active-listings search adapter — price + favorites/views demand."""

    def __init__(self, config: EtsyConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._cfg = config if isinstance(config, EtsyConfig) else EtsyConfig()
        self._client: httpx.AsyncClient | None = None
        self._fx: Any | None = None

    @property
    def name(self) -> str:
        return "etsy"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = await get_or_create_client(
            "openapi.etsy.com",
            http2=True,
            timeout=self._cfg.timeout_seconds,
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )
        try:
            from aegis.geo.fx import FXRateFetcher

            self._fx = FXRateFetcher()
        except Exception:
            self._fx = None

    async def teardown(self, ctx: ScrapeContext) -> None:
        self._client = None  # shared pooled client — release reference, never close
        self._fx = None

    async def _to_usd(self, value: Decimal | None, currency: str) -> float | None:
        if value is None:
            return None
        if currency == "USD" or self._fx is None:
            return float(value)
        try:
            return float(await self._fx.to_usd(value, currency))
        except Exception:
            return None

    async def _search(self, query: str, limit: int) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        key = _api_key()
        if key is None:
            _log.warning("etsy.no_credentials", hint="set AEGIS_ETSY_API_KEY")
            return []

        await self._rate_limit()
        self._record_request_metric(method="listings_active")
        try:
            resp = await self._client.get(
                _SEARCH_URL,
                params={
                    "keywords": query[:100],
                    "limit": str(min(limit, 100)),
                    "sort_on": "score",
                },
                headers={"x-api-key": key},
            )
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPStatusError as e:
            _log.warning("etsy.search.http_error", status=e.response.status_code)
            return []
        except Exception as e:
            _log.warning("etsy.search.failed", error=str(e))
            return []

        total = int(payload.get("count") or 0)
        scraped_at = datetime.now(UTC).isoformat()
        out: list[dict[str, Any]] = []
        for it in payload.get("results") or []:
            value, currency = _price(it)
            price_usd = await self._to_usd(value, currency)
            out.append({
                "listing_id": str(it.get("listing_id") or ""),
                "title": (it.get("title") or "").strip(),
                "url": it.get("url") or "",
                "scraped_at": scraped_at,
                "raw_json": {
                    "currency": currency,
                    "price": float(value) if value is not None else None,
                    "price_usd": price_usd,
                    "num_favorers": it.get("num_favorers"),
                    "views": it.get("views"),
                    "tags": it.get("tags") or [],
                    "listing_total": total,
                },
            })
        _log.info("etsy.search", query=query, results=len(out), total=total)
        return out

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        query: str | None = None,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            _log.debug("etsy.no_query")
            return
        for item in (await self._search(q, limit))[:limit]:
            if self.is_cancelled:
                return
            yield item

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            title = (raw.get("title") or "").strip()
            url = raw.get("url") or ""
            listing_id = str(raw.get("listing_id") or "")
            if not title or not url:
                return None
            raw_json = raw.get("raw_json") or {}
            favorers = raw_json.get("num_favorers")
            views = raw_json.get("views")
            tags_raw = raw_json.get("tags") or []

            external_id = hashlib.sha1(  # noqa: S324
                f"etsy:{listing_id or url}".encode()
            ).hexdigest()[:24]

            tags = frozenset(
                t.lower().replace(" ", "-") for t in tags_raw[:10] if t
            ) | {"etsy"}

            amount = raw_json.get("price")
            currency = str(raw_json.get("currency") or "USD").upper()
            if amount is None:
                return None
            try:
                price_obj = Price(amount=Decimal(str(amount)), currency=currency)
            except Exception:
                return None

            h = compute_content_hash(
                platform=Platform.ETSY,
                external_id=external_id,
                url=url,
                title=title[:512],
                raw_text=None,
                posted_at=None,
            )
            return ProductSignal(
                platform=Platform.ETSY,
                tier=SourceTier.TIER_2_COMMERCE,
                price=price_obj,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512],
                raw_text=None,
                modality=ContentModality.STRUCTURED,
                tags=tags,
                intent=IntentType.PURCHASE,
                engagement=EngagementMetrics(
                    likes=int(favorers) if isinstance(favorers, int | float) else None,
                    views=int(views) if isinstance(views, int | float) else None,
                ),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.OFFICIAL_API,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.85 if raw_json.get("price_usd") is not None else 0.6,
                    source_confidence=0.95,
                ),
                content_hash=h,
                platform_specific={
                    "listing_id": listing_id,
                    "currency": raw_json.get("currency", "USD"),
                    "price": raw_json.get("price"),
                    "price_usd": raw_json.get("price_usd"),
                    "num_favorers": favorers,
                    "views": views,
                    "listing_total": raw_json.get("listing_total"),
                    "demand_proxy": int(log1p(favorers)) if isinstance(favorers, int | float) and favorers else None,
                },
            )
        except Exception as e:
            _log.warning("etsy.parse.failed", error=str(e))
            return None


__all__ = ["SCRAPER_VERSION", "EtsyAdapter", "EtsyConfig"]
