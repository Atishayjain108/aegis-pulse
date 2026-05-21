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


__all__ = [
    "USER_AGENTS",
    "_looks_like_captcha",
    "flaresolverr_get",
    "random_ua",
]
