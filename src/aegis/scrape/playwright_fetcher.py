"""Playwright / Patchright async page renderer for JS-heavy sites.

Preference order: patchright (stealth, evades automation detection) →
playwright (standard) → unavailable (returns None; callers fall back).

Both are optional; install with:
    uv sync --extra scrape

A single Chromium browser process is shared for the lifetime of the
interpreter; each call gets a fresh browser context (isolated cookies/storage)
so there is no cross-request state leakage.  A Semaphore(2) limits concurrent
page fetches to prevent OOM on RAM-constrained hosts.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import structlog

_log = structlog.get_logger("aegis.scrape.playwright")

PLAYWRIGHT_AVAILABLE = False
_async_playwright: Any = None

try:
    from patchright.async_api import (
        async_playwright as _async_playwright,  # type: ignore[assignment,no-redef]
    )
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    try:
        from playwright.async_api import (
            async_playwright as _async_playwright,  # type: ignore[assignment,no-redef]
        )
        PLAYWRIGHT_AVAILABLE = True
    except ImportError:
        pass

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_BLOCK_PATTERNS = "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,eot,mp4,mp3}"

# --- Module-level browser pool -------------------------------------------
# One Chromium process shared across all fetch_page_html calls.
# Semaphore caps concurrent page loads to avoid OOM under swarm load.
_browser_sem = asyncio.Semaphore(2)
_pw_lock = asyncio.Lock()
_pw: Any = None
_browser: Any = None


async def _acquire_browser() -> Any:
    """Return the shared Chromium browser, launching it if needed."""
    global _pw, _browser  # noqa: PLW0603
    async with _pw_lock:
        if _browser is None or not _browser.is_connected():
            if _pw is not None:
                with contextlib.suppress(Exception):
                    await _pw.stop()
            _pw = await _async_playwright().start()
            _browser = await _pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            _log.info("playwright.browser_started")
    return _browser


async def fetch_page_html(
    url: str,
    *,
    timeout_ms: int = 30_000,
    wait_until: str = "domcontentloaded",
    extra_headers: dict[str, str] | None = None,
    locale: str = "en-IN",
) -> str | None:
    """Render *url* with headless Chromium and return the full page HTML.

    Returns ``None`` when playwright/patchright is not installed or the
    fetch fails — callers must fall back gracefully.
    """
    if not PLAYWRIGHT_AVAILABLE or _async_playwright is None:
        return None
    async with _browser_sem:
        try:
            browser = await _acquire_browser()
            ctx = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent=_DEFAULT_UA,
                locale=locale,
                extra_http_headers=extra_headers or {},
            )
            try:
                page = await ctx.new_page()

                async def _abort(route: Any) -> None:
                    await route.abort()

                await page.route(_BLOCK_PATTERNS, _abort)
                await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                html: str = await page.content()
                _log.info("playwright.fetch_ok", url=url, html_len=len(html))
                return html
            finally:
                await ctx.close()
        except Exception as exc:
            _log.warning("playwright.fetch_failed", url=url, error=str(exc)[:300])
            return None
