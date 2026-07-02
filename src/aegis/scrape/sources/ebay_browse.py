"""eBay Browse API adapter — free, official, key-gated, fail-open.

Uses eBay's free **Browse API** (`buy/browse/v1/item_summary/search`) which
returns real marketplace listings: title, price, condition, image, item URL,
and the **total listing count** for a query (a genuine supply/competition
proxy). Auth is OAuth2 *client-credentials* (application token) — no user
login, no scraping, never gets WAF-blocked.

Free setup (founder, zero prior knowledge — see docs/FREE_COMMERCE_SETUP.md):
  1. Create a free account at https://developer.ebay.com/ (eBay account works).
  2. Go to "Application access keys" → create a keyset (Production).
  3. Copy the **App ID (Client ID)** and **Cert ID (Client Secret)**.
  4. Export them:
        export AEGIS_EBAY_CLIENT_ID=...        # App ID / Client ID
        export AEGIS_EBAY_CLIENT_SECRET=...    # Cert ID / Client Secret
  No keys? The adapter logs `ebay.no_credentials` and fail-opens to [] so the
  rest of the pipeline runs untouched.

PLATFORM: ebay | TIER: T2_commerce
ToS Risk: GREEN — official public API used within its documented terms.
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass, field
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

_log = structlog.get_logger("aegis.scrape.ebay_browse")

SCRAPER_VERSION = "ebay_browse-0.1.0"

_OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
_SCOPE = "https://api.ebay.com/oauth/api_scope"

# Module-level application-token cache (mutable holder to avoid a `global`):
# {"token": str, "exp": monotonic_deadline}. Shared across adapter instances;
# refreshed ~60s before TTL.
_token_cache: dict[str, Any] = {}


def _credentials() -> tuple[str, str] | None:
    cid = os.environ.get("AEGIS_EBAY_CLIENT_ID", "").strip()
    secret = os.environ.get("AEGIS_EBAY_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        return None
    return cid, secret


async def _get_app_token(client: httpx.AsyncClient) -> str | None:
    """Client-credentials OAuth flow with in-process caching. None on failure."""
    now = time.monotonic()
    if _token_cache and _token_cache.get("exp", 0.0) > now:
        return _token_cache["token"]

    creds = _credentials()
    if creds is None:
        return None
    cid, secret = creds
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    try:
        resp = await client.post(
            _OAUTH_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": _SCOPE},
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        _log.warning("ebay.oauth_failed", error=str(exc))
        return None

    token = payload.get("access_token")
    if not token:
        return None
    # expires_in is seconds (typically 7200); refresh 60s early.
    ttl = float(payload.get("expires_in", 7200)) - 60.0
    _token_cache["token"] = token
    _token_cache["exp"] = now + max(60.0, ttl)
    return token


def _parse_price(raw: dict[str, Any]) -> tuple[Decimal | None, str]:
    price = raw.get("price") or {}
    try:
        value = Decimal(str(price.get("value")))
    except (InvalidOperation, TypeError):
        value = None
    currency = str(price.get("currency") or "USD").upper()
    return value, currency


@dataclass(frozen=True, slots=True)
class EbayBrowseConfig(AdapterConfig):
    """eBay Browse API adapter configuration."""

    name: str = "ebay"
    per_source_rps: float = 2.0  # Browse API quota is generous (5k calls/day free)
    timeout_seconds: float = 20.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False
    marketplace_id: str = field(
        default_factory=lambda: os.environ.get("AEGIS_EBAY_MARKETPLACE_ID", "EBAY_US")
    )


class EbayBrowseAdapter(SourceAdapter[dict[str, Any]]):
    """eBay Browse search adapter — real listings + listing-count demand proxy."""

    def __init__(self, config: EbayBrowseConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._cfg = config if isinstance(config, EbayBrowseConfig) else EbayBrowseConfig()
        self._client: httpx.AsyncClient | None = None
        self._fx: Any | None = None

    @property
    def name(self) -> str:
        return "ebay"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = await get_or_create_client(
            "api.ebay.com",
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
            return float(value) if currency == "USD" else None

    async def _search(self, query: str, limit: int) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        token = await _get_app_token(self._client)
        if token is None:
            _log.warning("ebay.no_credentials", hint="set AEGIS_EBAY_CLIENT_ID/SECRET")
            return []

        await self._rate_limit()
        self._record_request_metric(method="browse_search")
        try:
            resp = await self._client.get(
                _SEARCH_URL,
                params={"q": query[:100], "limit": str(min(limit, 50))},
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": self._cfg.marketplace_id,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPStatusError as e:
            _log.warning("ebay.search.http_error", status=e.response.status_code)
            return []
        except Exception as e:
            _log.warning("ebay.search.failed", error=str(e))
            return []

        total = int(payload.get("total") or 0)
        scraped_at = datetime.now(UTC).isoformat()
        out: list[dict[str, Any]] = []
        for it in payload.get("itemSummaries") or []:
            value, currency = _parse_price(it)
            price_usd = await self._to_usd(value, currency)
            out.append({
                "item_id": str(it.get("itemId") or ""),
                "title": (it.get("title") or "").strip(),
                "url": it.get("itemWebUrl") or "",
                "scraped_at": scraped_at,
                "raw_json": {
                    "currency": currency,
                    "price": float(value) if value is not None else None,
                    "price_usd": price_usd,
                    "image_url": (it.get("image") or {}).get("imageUrl"),
                    "condition": it.get("condition"),
                    "seller_feedback": (it.get("seller") or {}).get("feedbackScore"),
                    "listing_total": total,
                    "marketplace": self._cfg.marketplace_id,
                },
            })
        _log.info("ebay.search", query=query, results=len(out), listing_total=total)
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
            _log.debug("ebay.no_query")
            return
        for item in (await self._search(q, limit))[:limit]:
            if self.is_cancelled:
                return
            yield item

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            title = (raw.get("title") or "").strip()
            url = raw.get("url") or ""
            item_id = str(raw.get("item_id") or "")
            if not title or not url:
                return None
            raw_json = raw.get("raw_json") or {}

            external_id = hashlib.sha1(  # noqa: S324
                f"ebay:{item_id or url}".encode()
            ).hexdigest()[:24]

            # Listing total is a supply/competition proxy → fold into a likes-ish
            # engagement magnitude so it survives into downstream features.
            total = int(raw_json.get("listing_total") or 0)
            likes = int(log1p(total) * 10) if total else None

            # T2 commerce requires a real Price; skip a price-less listing
            # rather than misclassify its tier.
            amount = raw_json.get("price")
            currency = str(raw_json.get("currency") or "USD").upper()
            if amount is None:
                return None
            try:
                price_obj = Price(amount=Decimal(str(amount)), currency=currency)
            except Exception:
                return None

            h = compute_content_hash(
                platform=Platform.EBAY,
                external_id=external_id,
                url=url,
                title=title[:512],
                raw_text=None,
                posted_at=None,
            )
            return ProductSignal(
                platform=Platform.EBAY,
                tier=SourceTier.TIER_2_COMMERCE,
                price=price_obj,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512],
                raw_text=None,
                modality=ContentModality.STRUCTURED,
                tags=frozenset({"ebay"}),
                intent=IntentType.PURCHASE,
                engagement=EngagementMetrics(likes=likes),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.OFFICIAL_API,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.85 if raw_json.get("price_usd") is not None else 0.6,
                    source_confidence=0.95,  # official API → high trust
                ),
                content_hash=h,
                platform_specific={
                    "item_id": item_id,
                    "currency": raw_json.get("currency", "USD"),
                    "price": raw_json.get("price"),
                    "price_usd": raw_json.get("price_usd"),
                    "image_url": raw_json.get("image_url"),
                    "condition": raw_json.get("condition"),
                    "listing_total": total,
                    "seller_feedback": raw_json.get("seller_feedback"),
                    "marketplace": raw_json.get("marketplace"),
                },
            )
        except Exception as e:
            _log.warning("ebay.parse.failed", error=str(e))
            return None


__all__ = ["SCRAPER_VERSION", "EbayBrowseAdapter", "EbayBrowseConfig"]
