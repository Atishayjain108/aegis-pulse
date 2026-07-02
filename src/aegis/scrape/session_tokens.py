"""Session-token harvester for sites with dynamic-auth JSON APIs.

Some e-commerce platforms (Myntra, Meesho, …) gate their internal JSON
endpoints behind a session token / tracking cookie that the homepage's
JavaScript mints on first load. A bare ``httpx`` request to the JSON API
therefore gets ``401 Unauthorized`` (Myntra) or a redirect/404 because it
never went through the browser handshake.

This module spins up a headless browser (reusing the shared Chromium pool in
:mod:`aegis.scrape.playwright_fetcher`), visits the homepage, and:

1. Captures all cookies the site sets (``context.cookies()``).
2. Intercepts the site's own XHR/fetch requests and captures the auth-relevant
   request headers it sends (``authorization``, ``x-*``, ``cookie``, …).

The resulting :class:`SessionBundle` (cookies + headers) is injected into the
adapter's ``httpx`` client so the JSON path works with the rich structured
response instead of falling back to brittle HTML scraping.

Bundles are cached per host with a TTL so we pay the ~2–4s browser handshake
once per harvest window, not once per request.

Graceful degradation: when Playwright is unavailable or the harvest fails,
:func:`get_session_bundle` returns ``None`` and callers fall back to their
existing FlareSolverr / HTML / Playwright-render paths.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from aegis.scrape.playwright_fetcher import (
    PLAYWRIGHT_AVAILABLE,
    _acquire_browser,
    _browser_sem,
)

_log = structlog.get_logger("aegis.scrape.session_tokens")

# Default time-to-live for a harvested bundle. Tokens typically live much
# longer, but re-harvesting hourly keeps us resilient to rotation.
_DEFAULT_TTL_S = 1800.0

# Realistic desktop UA used for the lightweight httpx homepage handshake.
_HARVEST_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)

# Request-header names worth carrying over from the browser to httpx. Captured
# case-insensitively. ``cookie`` is handled separately via context cookies.
_INTERESTING_HEADER_PREFIXES = ("x-",)
_INTERESTING_HEADER_NAMES = frozenset(
    {
        "authorization",
        "user-agent",
        "accept-language",
        "referer",
        "origin",
    }
)


@dataclass(frozen=True, slots=True)
class SessionBundle:
    """Captured browser session for a host."""

    host: str
    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    captured_at: float = 0.0

    @property
    def cookie_header(self) -> str:
        """Render cookies as a single ``Cookie:`` header value."""
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())


# Per-host cache: host -> (bundle, expiry_monotonic). Guarded by a lock so two
# concurrent adapters don't both launch a browser handshake for the same host.
_CACHE: dict[str, tuple[SessionBundle, float]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}


def _want_header(name: str) -> bool:
    lname = name.lower()
    return lname in _INTERESTING_HEADER_NAMES or lname.startswith(
        _INTERESTING_HEADER_PREFIXES
    )


async def _harvest_httpx(homepage_url: str, *, host: str) -> SessionBundle | None:
    """Mint a session via a plain httpx homepage GET (no browser).

    Most "session-gated" JSON APIs (e.g. Myntra's /gateway/, Akamai
    ``_abck``/``bm_sz`` cookies) only require the cookies the homepage sets in
    its ``Set-Cookie`` response — these are minted server-side, not by JS. A
    single GET with a real UA captures them. This is the primary path because
    it is fast and works in headless-hostile environments (WSL2) where
    Chromium navigation to these CDNs stalls. Returns None on failure.
    """
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(25.0),
            follow_redirects=True,
            http2=False,
            headers={
                "User-Agent": _HARVEST_UA,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "en-IN,en;q=0.9",
            },
        ) as client:
            resp = await client.get(homepage_url)
            cookies: dict[str, str] = {
                name: (client.cookies.get(name) or "") for name in client.cookies
            }
            if not cookies:
                _log.info(
                    "session_tokens.httpx_no_cookies", host=host, status=resp.status_code
                )
                return None
            bundle = SessionBundle(
                host=host,
                cookies=cookies,
                headers={"user-agent": _HARVEST_UA, "accept-language": "en-IN,en;q=0.9"},
                captured_at=time.monotonic(),
            )
            _log.info(
                "session_tokens.harvested_httpx",
                host=host,
                status=resp.status_code,
                n_cookies=len(cookies),
            )
            return bundle
    except Exception as exc:  # httpx harvest is best-effort
        _log.warning("session_tokens.httpx_harvest_failed", host=host, error=str(exc)[:200])
        return None


async def _harvest(
    homepage_url: str,
    *,
    host: str,
    capture_url_substrings: tuple[str, ...],
    timeout_ms: int,
    settle_ms: int,
) -> SessionBundle | None:
    """Drive a browser to mint + capture a session bundle. Returns None on failure."""
    captured_headers: dict[str, str] = {}

    async with _browser_sem:
        try:
            browser = await _acquire_browser()
            ctx = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                locale="en-IN",
            )
            try:
                page = await ctx.new_page()

                async def _on_request(request: Any) -> None:
                    # Only capture from the site's own API calls so we don't
                    # pull in third-party analytics headers.
                    url = request.url
                    if capture_url_substrings and not any(
                        s in url for s in capture_url_substrings
                    ):
                        return
                    try:
                        for name, value in request.headers.items():
                            if _want_header(name) and name.lower() not in captured_headers:
                                captured_headers[name.lower()] = value
                    except Exception:  # header access is best-effort
                        pass

                page.on("request", _on_request)
                await page.goto(
                    homepage_url, wait_until="domcontentloaded", timeout=timeout_ms
                )
                # Let the SPA fire its bootstrap XHRs so tokens get minted.
                await page.wait_for_timeout(settle_ms)

                raw_cookies = await ctx.cookies()
                cookies = {
                    str(c.get("name")): str(c.get("value"))
                    for c in raw_cookies
                    if c.get("name")
                }
                bundle = SessionBundle(
                    host=host,
                    cookies=cookies,
                    headers=captured_headers,
                    captured_at=time.monotonic(),
                )
                _log.info(
                    "session_tokens.harvested",
                    host=host,
                    n_cookies=len(cookies),
                    n_headers=len(captured_headers),
                )
                return bundle
            finally:
                await ctx.close()
        except Exception as exc:  # harvest is best-effort
            _log.warning(
                "session_tokens.harvest_failed", host=host, error=str(exc)[:300]
            )
            return None


async def get_session_bundle(
    homepage_url: str,
    *,
    host: str,
    capture_url_substrings: tuple[str, ...] = (),
    ttl_s: float = _DEFAULT_TTL_S,
    timeout_ms: int = 30_000,
    settle_ms: int = 2500,
    force_refresh: bool = False,
) -> SessionBundle | None:
    """Return a cached or freshly harvested :class:`SessionBundle` for ``host``.

    Args:
        homepage_url: page to visit to mint the session (usually the site root).
        host: cache key + bundle identity (e.g. ``"www.myntra.com"``).
        capture_url_substrings: only capture headers from XHRs whose URL
            contains one of these (e.g. ``("/gateway/",)``). Empty captures all.
        ttl_s: how long a harvested bundle stays valid.
        force_refresh: ignore the cache and re-harvest.

    Strategy: try the lightweight httpx homepage handshake first (fast,
    reliable, works in headless-hostile environments). Only fall back to a
    headless browser when httpx yields no cookies AND Playwright is available
    (rare — for sites that mint tokens purely in JS).

    Returns ``None`` when both paths fail.
    """
    now = time.monotonic()
    if not force_refresh:
        cached = _CACHE.get(host)
        if cached is not None and cached[1] > now:
            return cached[0]

    lock = _LOCKS.setdefault(host, asyncio.Lock())
    async with lock:
        # Re-check under lock — another coroutine may have just harvested.
        cached = _CACHE.get(host)
        if not force_refresh and cached is not None and cached[1] > time.monotonic():
            return cached[0]

        # Primary: plain httpx homepage GET (mints server-set cookies).
        bundle = await _harvest_httpx(homepage_url, host=host)

        # Fallback: headless browser for JS-minted tokens.
        if bundle is None and PLAYWRIGHT_AVAILABLE:
            bundle = await _harvest(
                homepage_url,
                host=host,
                capture_url_substrings=capture_url_substrings,
                timeout_ms=timeout_ms,
                settle_ms=settle_ms,
            )

        if bundle is not None:
            _CACHE[host] = (bundle, time.monotonic() + ttl_s)
        return bundle


def clear_cache() -> None:
    """Drop all cached bundles (used by tests)."""
    _CACHE.clear()


__all__ = ["SessionBundle", "clear_cache", "get_session_bundle"]
