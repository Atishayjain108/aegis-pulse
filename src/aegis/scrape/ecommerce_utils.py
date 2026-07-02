"""Shared utilities for Indian e-commerce adapters.

Provides:
- ``USER_AGENTS`` pool + ``random_ua()`` helper
- ``flaresolverr_get()`` — headless-browser bypass via FlareSolverr
"""
from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    pass

_log = structlog.get_logger("aegis.scrape.ecommerce_utils")


def extract_card_image(card: Any) -> str | None:
    """Best-effort product-image URL from a BeautifulSoup product card.

    Tries the first ``<img>`` element and reads, in priority order:
    ``src`` → ``data-src`` → ``data-img`` → ``data-original`` → first entry of
    ``srcset``. Returns an absolute ``http(s)`` URL, or ``None`` when no usable
    image is found. Never raises — image enrichment must never break a scrape.
    """
    try:
        img = card.find("img")
        if img is None:
            return None
        for attr in ("src", "data-src", "data-img", "data-original"):
            val = img.get(attr)
            if val and isinstance(val, str) and val.strip().startswith("http"):
                return val.strip()
        srcset = img.get("srcset")
        if srcset and isinstance(srcset, str):
            first = srcset.split(",")[0].strip().split(" ")[0]
            if first.startswith("http"):
                return first
    except Exception:  # pragma: no cover - defensive, never break scrape
        return None
    return None


USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]


def random_ua() -> str:
    """Return a random User-Agent string from the pool."""
    return random.choice(USER_AGENTS)


def join_brand_title(brand: str, name: str) -> str:
    """Combine a brand + product name into a clean title without doubling.

    Many marketplace APIs return ``brand`` separately AND already prefix the
    product name with the brand (e.g. brand="ETUDE", name="ETUDE Lip Gloss").
    A naive ``f"{brand} {name}"`` then yields "ETUDE ETUDE Lip Gloss". This
    skips the brand prefix when ``name`` already starts with it (case- and
    whitespace-insensitive). Returns ``name`` alone when ``brand`` is empty.
    """
    brand = (brand or "").strip()
    name = (name or "").strip()
    if not brand:
        return name
    if not name:
        return brand
    if name.lower() == brand.lower() or name.lower().startswith(brand.lower() + " "):
        return name
    return f"{brand} {name}"


async def flaresolverr_get(
    url: str,
    flaresolverr_url: str,
    http: httpx.AsyncClient,
    governor: Any | None = None,
    timeout: float = 60.0,
) -> str | None:
    """Request a URL through FlareSolverr headless browser.

    Returns HTML string or None on failure.
    Uses governor.flaresolverr_slot() semaphore to cap at 2 concurrent bypass
    requests when a governor is provided; falls back to a local semaphore
    otherwise (useful in unit tests).
    """
    slot: asyncio.Semaphore = (
        governor.flaresolverr_slot() if governor is not None else asyncio.Semaphore(1)
    )
    async with slot:
        try:
            resp = await http.post(
                flaresolverr_url,
                json={"cmd": "request.get", "url": url, "maxTimeout": int(timeout * 1000)},
                timeout=timeout + 5,
            )
            data = resp.json()
            if data.get("status") == "ok":
                return data["solution"]["response"]
            _log.warning("flaresolverr_status_not_ok", url=url, status=data.get("status"))
            return None
        except httpx.ConnectError:
            _log.warning(
                "flaresolverr_unavailable",
                url=url,
                flaresolverr_url=flaresolverr_url,
                hint="Is the FlareSolverr container running? "
                     "Start with: docker compose up -d aegis-flaresolverr",
            )
            return None
        except Exception as e:
            _log.warning("flaresolverr_failed", url=url, error=str(e))
            return None


def _looks_like_captcha(html: str) -> bool:
    """Return True if the response body looks like a CAPTCHA / bot-block page."""
    lower = html.lower()
    return any(
        kw in lower
        for kw in (
            "captcha",
            "cf-browser-verification",
            "verify you are human",
            "enable javascript",
            "access denied",
            "bot detected",
        )
    )


def _walk_ld_products(node: Any, out: list[dict[str, Any]]) -> None:
    """Recursively collect Schema.org Product entities from parsed JSON-LD."""
    if isinstance(node, list):
        for item in node:
            _walk_ld_products(item, out)
        return
    if not isinstance(node, dict):
        return
    typ = node.get("@type") or node.get("type")
    types = {typ} if isinstance(typ, str) else set(typ) if isinstance(typ, list) else set()
    if "Product" in types:
        offers = node.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price = None
        if isinstance(offers, dict):
            price = offers.get("price") or offers.get("lowPrice")
        brand = node.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        rating = None
        agg = node.get("aggregateRating")
        if isinstance(agg, dict):
            rating = agg.get("ratingValue")
        out.append({
            "title": str(node.get("name") or "").strip(),
            "url": str(node.get("url") or node.get("@id") or ""),
            "price": price,
            "brand": brand,
            "rating": rating,
            "image_url": node.get("image") if isinstance(node.get("image"), str) else None,
            "currency": (offers.get("priceCurrency") if isinstance(offers, dict) else None),
        })
    # ItemList → walk itemListElement
    for key in ("itemListElement", "item", "mainEntity", "@graph"):
        if key in node:
            _walk_ld_products(node[key], out)


def extract_products_from_structured(html: str) -> list[dict[str, Any]]:
    """Generic, site-agnostic product extractor.

    Pulls products from the two structured-data formats modern e-commerce
    SPAs ship in their server HTML — ``<script type="application/ld+json">``
    (Schema.org Product/ItemList) and Next.js ``__NEXT_DATA__`` JSON. This
    works across many marketplaces without per-site DOM selectors, which is
    what makes the harvest an all-rounder rather than one-site-at-a-time.

    Returns normalized raw dicts: ``{title, url, raw_json:{price_inr,...}}``.
    Never raises — returns ``[]`` on any failure.
    """
    import json
    import re

    products: list[dict[str, Any]] = []
    # --- JSON-LD blocks ---
    try:
        for m in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.DOTALL | re.IGNORECASE,
        ):
            try:
                data = json.loads(m.group(1).strip())
            except Exception:
                continue
            _walk_ld_products(data, products)
    except Exception as e:
        _log.debug("structured.ld_json_failed", error=str(e))

    # --- Next.js __NEXT_DATA__ (best-effort name/price scan) ---
    if not products:
        try:
            m = re.search(
                r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
                html, re.DOTALL,
            )
            if m:
                data = json.loads(m.group(1))
                _walk_next_products(data, products)
        except Exception as e:
            _log.debug("structured.next_data_failed", error=str(e))

    # Normalize to the adapter raw-dict shape, de-duped by title.
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for p in products:
        title = (p.get("title") or "").strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        price = p.get("price")
        try:
            price_f = float(str(price).replace(",", "")) if price not in (None, "") else None
        except (ValueError, TypeError):
            price_f = None
        out.append({
            "title": title[:512],
            "url": p.get("url") or "",
            "raw_json": {
                "currency": p.get("currency") or "INR",
                "price_inr": price_f,
                "brand": p.get("brand"),
                "rating": p.get("rating"),
                "image_url": p.get("image_url"),
                "source": "structured_data",
            },
        })
    return out


def _walk_next_products(node: Any, out: list[dict[str, Any]], depth: int = 0) -> None:
    """Heuristically collect product-like dicts from a __NEXT_DATA__ tree."""
    if depth > 12:
        return
    if isinstance(node, list):
        for item in node:
            _walk_next_products(item, out, depth + 1)
        return
    if not isinstance(node, dict):
        return
    name = node.get("name") or node.get("title") or node.get("product_name")
    price = (
        node.get("price") or node.get("sellingPrice") or node.get("finalPrice")
        or node.get("discountedPrice") or node.get("mrp")
    )
    if isinstance(name, str) and name.strip() and price not in (None, "", 0):
        out.append({
            "title": name,
            "url": str(node.get("url") or node.get("slug") or ""),
            "price": price.get("value") if isinstance(price, dict) else price,
            "brand": node.get("brand") if isinstance(node.get("brand"), str) else None,
            "rating": node.get("rating") or node.get("avg_rating"),
            "image_url": node.get("image") if isinstance(node.get("image"), str) else None,
            "currency": "INR",
        })
    for v in node.values():
        if isinstance(v, dict | list):
            _walk_next_products(v, out, depth + 1)


__all__ = [
    "USER_AGENTS",
    "_looks_like_captcha",
    "extract_products_from_structured",
    "flaresolverr_get",
    "join_brand_title",
    "random_ua",
]
