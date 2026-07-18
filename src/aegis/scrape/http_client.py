"""Shared HTTP client factory for AEGIS scrape adapters (GODMODE PASS5-5B).

Phase 0 scrape layer. Provides one pooled ``httpx.AsyncClient`` per
(event loop, host, headers, http2, timeout, redirects) combination with
connection pooling, keep-alive, and timeouts pre-configured. Eliminates
the per-adapter-instance httpx client anti-pattern (ADP-7 in
AEGIS_AUDIT.md): repeated setup/teardown cycles of the same adapter now
reuse warm TCP/TLS connections instead of renegotiating every run.

Usage (inside an adapter)::

    from aegis.scrape.http_client import get_or_create_client

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = await get_or_create_client(
            "hn.algolia.com",
            timeout=self._config.timeout_seconds,
            headers={...},
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        self._client = None  # shared client — release reference, never close

Clients are registered per event loop because ``httpx.AsyncClient``
transports are loop-bound: a client created under one pytest event loop
cannot be awaited from another. Stale-loop entries are pruned lazily.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.http_client")

# =============================================================================
# Anti-throttling: per-host request spacing + header/UA rotation
# =============================================================================
# Root cause of 429/403 storms: adapters fire many requests at the same host
# in the same second (e.g. 5 subreddits) with an identical fingerprint. We
# enforce a randomized minimum gap between consecutive requests to the SAME
# host and rotate browser-like headers on every request. Applied via httpx
# request event hooks on every shared client, so all adapters benefit.

# Modern Chrome user agents (stable channel q1/q2 2026) — coherent triplets
# of (User-Agent, Sec-CH-UA, Sec-CH-UA-Platform).
_UA_POOL: tuple[tuple[str, str, str], ...] = (
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        '"Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132"',
        '"Windows"',
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        '"macOS"',
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        '"Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132"',
        '"Linux"',
    ),
)

_ACCEPT_LANGUAGES: tuple[str, ...] = (
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.8,hi;q=0.6",
    "en-IN,en;q=0.9,hi;q=0.7",
)

# Hosts known to ban on burst — get a wider request gap. Substring match on
# the request host. Tunable via AEGIS_SCRAPE_SENSITIVE_GAP_S (min,max).
_SENSITIVE_HOST_FRAGMENTS: tuple[str, ...] = (
    "reddit.com",
    "flipkart.com",
    "myntra.com",
    "meesho.com",
    "nykaa.com",
    "producthunt.com",
    "trends.google.com",
    "google.com/trends",
    "ajio.com",
    "snapdeal.com",
    "indiamart.com",
    "amazon.",
)


def _gap_bounds(env_var: str, default_lo: float, default_hi: float) -> tuple[float, float]:
    raw = os.getenv(env_var, "")
    if raw:
        try:
            lo_s, hi_s = raw.split(",", 1)
            return float(lo_s), float(hi_s)
        except ValueError:
            _log.warning("http_client.bad_gap_env", env=env_var, value=raw)
    return default_lo, default_hi


# Default request gaps. Friendly hosts (RSS, APIs) get a small gap so harvests
# stay fast; sensitive hosts get the 1.5–4.2s window that real users exhibit.
_DEFAULT_GAP = _gap_bounds("AEGIS_SCRAPE_DEFAULT_GAP_S", 0.25, 0.75)
_SENSITIVE_GAP = _gap_bounds("AEGIS_SCRAPE_SENSITIVE_GAP_S", 1.5, 4.2)

# Per-host last-request timestamps + locks (serialize spacing per host).
_HOST_LAST: dict[str, float] = {}
_HOST_LOCKS: dict[str, asyncio.Lock] = {}


def _is_sensitive(host: str) -> bool:
    h = host.lower()
    return any(frag in h for frag in _SENSITIVE_HOST_FRAGMENTS)


async def _respect_host_gap(host: str) -> None:
    """Enforce a randomized minimum gap between requests to the same host.

    Shared by the httpx event hook and the curl_cffi impersonated path so both
    transports observe the same human-like pacing per origin.
    """
    if not host:
        return
    lock = _HOST_LOCKS.setdefault(host, asyncio.Lock())
    lo, hi = _SENSITIVE_GAP if _is_sensitive(host) else _DEFAULT_GAP
    async with lock:
        now = time.monotonic()
        last = _HOST_LAST.get(host)
        gap = random.uniform(lo, hi)
        if last is not None:
            wait = (last + gap) - now
            if wait > 0:
                await asyncio.sleep(wait)
        _HOST_LAST[host] = time.monotonic()


async def _throttle_hook(request: httpx.Request) -> None:
    """Enforce a randomized minimum gap between requests to the same host."""
    await _respect_host_gap(request.url.host or "")


async def _rotate_headers_hook(request: httpx.Request) -> None:
    """Attach rotated, browser-like headers unless the caller set them.

    Must be ``async`` — httpx awaits every request event hook; a sync hook
    returns ``None`` which httpx then tries to ``await`` (TypeError).

    Respects explicit per-adapter headers (e.g. Reddit's gzip-only rule):
    we only fill headers the request does not already carry.
    """
    ua, sec_ch_ua, platform = random.choice(_UA_POOL)
    defaults = {
        "User-Agent": ua,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
        "Sec-CH-UA": sec_ch_ua,
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": platform,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    for name, value in defaults.items():
        if name not in request.headers:
            request.headers[name] = value


_EVENT_HOOKS: dict[str, list] = {
    "request": [_throttle_hook, _rotate_headers_hook],
}


def _proxy_url() -> str | None:
    """Optional outbound proxy (e.g. rotating residential gateway or Tor).

    Resolution order (all free unless you supply a paid gateway):
      1. ``AEGIS_SCRAPE_PROXY_URL`` — explicit proxy URL (any scheme).
      2. ``AEGIS_SCRAPE_TOR=1`` — route via local Tor SOCKS5 at
         ``AEGIS_SCRAPE_TOR_URL`` (default ``socks5h://127.0.0.1:9050``).
         Free IP rotation; requires the ``tor`` daemon running locally.
    Empty/unset means direct connections (your own residential IP).
    """
    explicit = os.getenv("AEGIS_SCRAPE_PROXY_URL")
    if explicit:
        return explicit
    if os.getenv("AEGIS_SCRAPE_TOR", "").lower() in ("1", "true", "yes"):
        return os.getenv("AEGIS_SCRAPE_TOR_URL") or "socks5h://127.0.0.1:9050"
    return None


# curl_cffi browser-impersonation targets. Each makes the TLS/JA3 + HTTP2
# fingerprint identical to a real browser, which is what defeats Akamai /
# PerimeterX / Cloudflare 403s that fire on the handshake — before any header
# or IP is even inspected. Rotated per request for fingerprint diversity.
_IMPERSONATE_POOL: tuple[str, ...] = (
    "chrome124", "chrome123", "chrome120", "edge101", "safari17_0",
)


async def impersonated_fetch(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 25.0,
    impersonate: str | None = None,
    method: str = "GET",
) -> tuple[int, str]:
    """Fetch a URL with a real-browser TLS fingerprint via curl_cffi.

    This is the single highest-leverage *free* anti-bot lever: the handshake
    looks like Chrome, so WAFs that 403 plain ``httpx`` (Python JA3) let it
    through. Honours the same per-host pacing and proxy/Tor config as the
    pooled httpx clients.

    Returns ``(status_code, text)``. Never raises — returns ``(0, "")`` when
    curl_cffi is unavailable or the request fails, so callers degrade to their
    Playwright / httpx fallbacks transparently.
    """
    try:
        from curl_cffi.requests import AsyncSession  # type: ignore[import-untyped]
    except Exception:
        _log.debug("http_client.curl_cffi_unavailable")
        return (0, "")

    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    await _respect_host_gap(host)

    target = impersonate or random.choice(_IMPERSONATE_POOL)
    proxy = _proxy_url()
    try:
        async with AsyncSession() as session:
            resp = await session.request(
                method,  # type: ignore[arg-type]
                url,
                headers=headers,
                timeout=timeout,
                impersonate=target,  # type: ignore[arg-type]
                proxy=proxy,
                allow_redirects=True,
            )
            return (resp.status_code, resp.text)
    except Exception as exc:
        _log.warning(
            "http_client.impersonated_fetch_failed",
            host=host, impersonate=target, error=str(exc),
        )
        return (0, "")

# Standard timeout: 5s connect, 15s read, 5s write, 2s pool acquire.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=2.0)

# Connection pool limits: 20 per host prevents thundering-herd against a
# single origin; keep-alive avoids TLS renegotiation on every request.
_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10,
    keepalive_expiry=30.0,
)

# Hosts that do NOT support HTTP/2 (must use HTTP/1.1) when the caller
# leaves ``http2=None``. Adapters that already know their protocol pass
# it explicitly.
_HTTP1_ONLY_HOSTS = frozenset(
    {
        "www.reddit.com",  # blocks Brotli + HTTP/2 by TOS
        "oauth.reddit.com",
        "news.google.com",  # RSS endpoint serves HTTP/1.1 only
        "localhost",  # Ollama's embedded server rejects h2
        "127.0.0.1",
    }
)

# Per-event-loop client registry: {loop_id: {cache_key: client}}.
_REGISTRY: dict[int, dict[str, httpx.AsyncClient]] = {}


def _loop_clients() -> dict[str, httpx.AsyncClient]:
    """Return the client registry for the current running event loop."""
    loop_id = id(asyncio.get_running_loop())
    return _REGISTRY.setdefault(loop_id, {})


def _cache_key(
    host: str,
    use_http2: bool,
    headers: dict[str, str] | None,
    timeout: float | None,
    follow_redirects: bool,
) -> str:
    config_fingerprint = json.dumps(
        {
            "headers": headers or {},
            "timeout": timeout,
            "redirects": follow_redirects,
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(config_fingerprint.encode()).hexdigest()[:16]
    return f"{host}|{'h2' if use_http2 else 'h1'}|{digest}"


async def get_or_create_client(
    host: str,
    *,
    http2: bool | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    follow_redirects: bool = True,
) -> httpx.AsyncClient:
    """Return (creating if necessary) a shared AsyncClient for a host.

    Clients are created once per (loop, host, config) and reused across
    all adapter calls. Callers must NOT close the returned client —
    lifecycle is owned by this module (see :func:`close_all`).
    """
    use_http2 = http2 if http2 is not None else (host not in _HTTP1_ONLY_HOSTS)
    clients = _loop_clients()
    key = _cache_key(host, use_http2, headers, timeout, follow_redirects)

    client = clients.get(key)
    if client is not None and not client.is_closed:
        return client

    timeout_cfg = httpx.Timeout(timeout) if timeout is not None else _DEFAULT_TIMEOUT
    proxy = _proxy_url()
    try:
        client = httpx.AsyncClient(
            timeout=timeout_cfg,
            http2=use_http2,
            headers=headers,
            follow_redirects=follow_redirects,
            limits=_LIMITS,
            event_hooks=_EVENT_HOOKS,
            proxy=proxy,
        )
    except ImportError:
        # The optional ``h2`` package is absent — fall back to HTTP/1.1.
        use_http2 = False
        key = _cache_key(host, use_http2, headers, timeout, follow_redirects)
        client = clients.get(key)
        if client is not None and not client.is_closed:
            return client
        client = httpx.AsyncClient(
            timeout=timeout_cfg,
            http2=False,
            headers=headers,
            follow_redirects=follow_redirects,
            limits=_LIMITS,
            event_hooks=_EVENT_HOOKS,
            proxy=proxy,
        )

    clients[key] = client
    _log.debug("http_client.created", host=host, http2=use_http2)
    return client


@asynccontextmanager
async def get_client(
    host: str,
    *,
    http2: bool | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    follow_redirects: bool = True,
) -> AsyncIterator[httpx.AsyncClient]:
    """Context manager for adapter use — does NOT close the client on exit."""
    client = await get_or_create_client(
        host,
        http2=http2,
        headers=headers,
        timeout=timeout,
        follow_redirects=follow_redirects,
    )
    yield client


async def close_all() -> None:
    """Close all shared clients owned by the current event loop.

    Call at process shutdown. Registries belonging to other (dead) event
    loops are dropped without awaiting their clients — their transports
    died with their loop.
    """
    loop_id = id(asyncio.get_running_loop())
    clients = _REGISTRY.pop(loop_id, {})
    for client in clients.values():
        try:
            await client.aclose()
        except Exception as exc:
            _log.debug("http_client.close_failed", error=str(exc))
    # Prune registries of loops that no longer run (best-effort).
    for stale_loop_id in [k for k in _REGISTRY if k != loop_id]:
        _REGISTRY.pop(stale_loop_id, None)


__all__ = ["close_all", "get_client", "get_or_create_client"]
