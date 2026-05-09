"""FlareSolverr client — Cloudflare challenge bypass.

`FlareSolverr <https://github.com/FlareSolverr/FlareSolverr>`_ is a self-
hosted proxy that uses a real Chrome (via undetected-chromedriver) to solve
Cloudflare's "Just a moment..." JS challenge and Turnstile. It returns the
resulting ``cf_clearance`` cookie + user-agent, which the caller can then
use directly via httpx/curl-impersonate for subsequent requests.

Why this matters: solving challenges in our main scraper would cost us a
real browser session per ban. Offloading to FlareSolverr means:

1. One running Chrome handles all challenge-solving across our scrapers.
2. We can stay on lightweight HTTP libraries (httpx, curl-cffi) for the
   90% of requests that are not challenged.
3. FREE (self-hosted) — replaces 2Captcha for v1.

Lifecycle of a typical request:

    1. Adapter HTTP-fetches a Reddit/TikTok/etc URL.
    2. Response body contains ``"Just a moment..."`` (Cloudflare interstitial).
    3. Adapter calls ``FlareSolverr.get(url)`` → returns ``cf_clearance``
       cookie + UA.
    4. Adapter retries the original request, attaching cookie+UA.
    5. ``cf_clearance`` is cached for ~30 min (per Cloudflare default) and
       reused for subsequent requests to the same domain.

Design choices:

- **Stateless wrapper**: this client doesn't manage session_id lifetimes
  on the FlareSolverr side. Each call uses ``request.get`` mode, which
  spawns a one-shot solve. Good enough for Phase 1 — Phase 5 may add
  long-lived session reuse.
- **Resilient by default**: wrapped with our ``@resilient_call`` so a
  flaky FlareSolverr instance doesn't propagate failures.
- **Detection helper**: ``looks_like_challenge()`` lets adapters quickly
  decide whether to invoke this client at all.

Author: AEGIS Pulse Team
Relationship: invoked from ``base.SourceAdapter`` when a CAPTCHA / challenge
is detected. Optional — adapters with an official API never use it.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, cast

import httpx
import orjson

from aegis.constants import HTTP_TOTAL_TIMEOUT_SECONDS
from aegis.core.logging import get_logger
from aegis.core.metrics import scrape_captcha_total
from aegis.core.resilience import ResiliencePolicy, resilient_call

log = get_logger(__name__)


# =============================================================================
# Detection helpers — pure-string heuristics, no external IO
# =============================================================================

# Strings strongly indicative of a Cloudflare challenge interstitial.
# We match the WORST case (HTML body returned with 200 OK + JS challenge).
_CHALLENGE_BODY_MARKERS: Final[tuple[str, ...]] = (
    "Just a moment...",
    "Checking your browser before accessing",
    "_cf_chl_opt",                  # Turnstile JS variable
    "/cdn-cgi/challenge-platform",  # CF challenge platform script path
    "DDoS protection by Cloudflare",
    "ray-id",                       # Cloudflare's request id surfaced on errors
)

# Headers that, even on a 200, indicate Cloudflare touched the response.
_CF_HEADERS: Final[tuple[str, ...]] = (
    "cf-ray",
    "cf-cache-status",
    "server",  # value "cloudflare" → CF (we check value below)
)

# Status codes Cloudflare emits during challenges.
_CHALLENGE_STATUS_CODES: Final[frozenset[int]] = frozenset({403, 429, 503})

# DDoS-Guard / PerimeterX have similar markers; we match those too because
# FlareSolverr handles them with the same flow.
_OTHER_PROVIDER_MARKERS: Final[tuple[str, ...]] = (
    "ddos-guard.net",
    "_pxhd",     # PerimeterX
    "_pxAction",
)

_RAY_ID_RE: Final[re.Pattern[str]] = re.compile(r'cf-ray["\s:=]+([0-9a-f]+-[A-Z]+)')


def looks_like_challenge(
    *,
    status_code: int | None = None,
    body: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> bool:
    """Return True if a response is plausibly a CF/DDoS-Guard challenge.

    Pass any subset of ``status_code``/``body``/``headers`` you have on hand;
    the function applies all available evidence. Designed for cheap
    short-circuiting in the adapter request loop.
    """
    # Strong signal: an HTML status code with a body containing markers.
    if body:
        for marker in _CHALLENGE_BODY_MARKERS:
            if marker in body:
                return True
        for marker in _OTHER_PROVIDER_MARKERS:
            if marker in body:
                return True

    if status_code is not None and status_code in _CHALLENGE_STATUS_CODES:
        # 403/429/503 alone aren't a challenge — many APIs use them
        # legitimately. We require Cloudflare evidence in headers OR body
        # to upgrade to "challenge".
        if headers is not None:
            lowered = {k.lower(): v for k, v in headers.items()}
            if lowered.get("server", "").lower() == "cloudflare":
                return True
            if "cf-ray" in lowered or "cf-mitigated" in lowered:
                return True
        if body and "cloudflare" in body.lower():
            return True

    return False


# =============================================================================
# Result type
# =============================================================================


@dataclass(frozen=True, slots=True)
class ChallengeSolution:
    """Output of a successful ``FlareSolverr.get()`` call.

    The adapter applies these as cookies+headers on subsequent requests
    to the same target host, until ``cf_clearance`` expires (~30 min)."""

    url: str
    """Final URL after any redirects."""

    status_code: int
    """HTTP status from FlareSolverr's perspective (the underlying solve)."""

    cookies: dict[str, str]
    """All cookies from the solved session. Includes ``cf_clearance``."""

    user_agent: str
    """The UA Chrome used during the solve. Adapters MUST reuse this exact
    string — Cloudflare validates that ``cf_clearance`` is bound to it."""

    response_body: str
    """The fully-rendered HTML body (post-challenge). Often useful — saves
    a second fetch when the page itself is what we wanted."""

    headers: dict[str, str]
    """Final response headers from the solved request."""

    @property
    def cf_clearance(self) -> str | None:
        """Convenience: the ``cf_clearance`` cookie value, or ``None`` if absent."""
        return self.cookies.get("cf_clearance")


# =============================================================================
# Errors
# =============================================================================


class FlareSolverrError(Exception):
    """Base class for FlareSolverr-specific errors."""


class FlareSolverrSolveError(FlareSolverrError):
    """The FlareSolverr server itself responded, but reported a solve failure
    (e.g. challenge unsolvable, target site down, target site fast-blocked).

    Not retryable: hammering the same URL again won't help. Adapter should
    treat the underlying signal as un-fetchable for now."""


class FlareSolverrUnavailableError(FlareSolverrError):
    """The FlareSolverr server itself is unreachable or returned a non-200.

    Retryable: probably transient (server restarting, network blip)."""


# =============================================================================
# Client
# =============================================================================


# Module-level resilience policy — tightly coupled to FlareSolverr's
# expected solve time (10–30s typical). max_attempts=2 because a 30s timeout
# is already long; retrying on top is a 60s+ tax on a single signal.
_FLARE_POLICY: Final[ResiliencePolicy] = ResiliencePolicy(
    name="flaresolverr.solve",
    timeout=60.0,
    max_attempts=2,
    base_delay=2.0,
    max_delay=10.0,
    retry_on=(httpx.TransportError, FlareSolverrUnavailableError),
    # Solve failures from the upstream are NOT retryable — same URL won't suddenly start working.
    do_not_retry_on=(FlareSolverrSolveError,),
)


class FlareSolverr:
    """Async client for a self-hosted FlareSolverr instance.

    Construct with the FlareSolverr URL (default in our docker-compose is
    ``http://flaresolverr:8191``). Calls are wrapped in our
    ``resilient_call`` decorator so transport failures and timeouts are
    handled uniformly with the rest of the system.

    Example::

        fs = FlareSolverr("http://flaresolverr:8191")
        sol = await fs.get("https://example.com/", proxy="http://proxy:8080")
        cookies = sol.cookies          # {'cf_clearance': '...', ...}
        ua = sol.user_agent

        # Use these in subsequent httpx requests:
        async with httpx.AsyncClient(headers={'User-Agent': ua}) as c:
            r = await c.get("https://example.com/api/...", cookies=cookies)
    """

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        default_max_timeout_ms: int = 60_000,
    ) -> None:
        # Strip trailing slash so we can format URLs cleanly.
        self._base_url: str = base_url.rstrip("/")
        # Reuse a client for connection pooling. If caller passes None,
        # we own the lifecycle and close it on aclose().
        self._client: httpx.AsyncClient = client or httpx.AsyncClient(
            timeout=httpx.Timeout(HTTP_TOTAL_TIMEOUT_SECONDS, connect=10.0),
            headers={"Content-Type": "application/json"},
        )
        self._owns_client: bool = client is None
        self._default_max_timeout_ms: int = default_max_timeout_ms

    async def aclose(self) -> None:
        """Close the underlying HTTP client iff we own it."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> FlareSolverr:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health(self) -> bool:
        """Return True if FlareSolverr responds to a status check.

        Hits ``GET /health``. Useful at adapter startup so we know whether
        we can use the bypass at all (and skip the relevant feature flag
        if not).
        """
        url = f"{self._base_url}/health"
        try:
            r = await self._client.get(url, timeout=5.0)
            return r.status_code == 200
        except httpx.HTTPError as e:
            log.warning("flaresolverr.health.fail", error=str(e))
            return False

    # ------------------------------------------------------------------
    # The actual solve call
    # ------------------------------------------------------------------

    async def get(
        self,
        url: str,
        *,
        proxy: str | None = None,
        max_timeout_ms: int | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> ChallengeSolution:
        """Solve a Cloudflare challenge for ``url`` and return the cookies.

        Args:
            url: target URL.
            proxy: optional upstream proxy that FlareSolverr should route
                through. Format ``"http://[user:pass@]host:port"``.
                When set, the resulting cookies are bound to the proxy's IP
                — reuse the same proxy for follow-up requests.
            max_timeout_ms: per-solve cap. Defaults to ``default_max_timeout_ms``
                from the constructor.
            cookies: optional cookies to seed the solve session (rarely useful
                for fresh challenges, but supported by the API).

        Raises:
            FlareSolverrUnavailableError: server unreachable or non-200.
            FlareSolverrError: server returned an error envelope.
        """
        body = self._build_request_body(
            url=url,
            proxy=proxy,
            max_timeout_ms=max_timeout_ms or self._default_max_timeout_ms,
            cookies=cookies,
        )
        return await self._resilient_post(body=body)

    # Module-level decorator application is awkward inside a class. We
    # define ``_resilient_post`` as a normal method, then in __init__ we
    # could wrap it — but the cleanest approach is to keep a free function
    # decorated at the module level and call it from the method. See
    # ``_resilient_post`` below.
    async def _resilient_post(self, *, body: dict[str, Any]) -> ChallengeSolution:
        """Resilient POST wrapper. The actual decoration happens via
        ``_resilient_post_impl`` at module level so mypy can resolve types."""
        # Cast around mypy 1.11's ParamSpec resolution limitation across
        # decorator factory boundaries — the runtime types are correct.
        impl = cast(
            "Callable[[FlareSolverr, dict[str, Any]], Awaitable[ChallengeSolution]]",
            _resilient_post_impl,
        )
        return await impl(self, body)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_request_body(
        self,
        *,
        url: str,
        proxy: str | None,
        max_timeout_ms: int,
        cookies: Mapping[str, str] | None,
    ) -> dict[str, Any]:
        """Assemble the FlareSolverr v2 ``request.get`` payload.

        The FlareSolverr API surface:
        https://github.com/FlareSolverr/FlareSolverr#commands

        We DO NOT use ``session`` IDs (one-shot solves only — see module
        docstring for rationale). We DO set ``maxTimeout`` because the
        default of 60 s sometimes runs over our own timeout budget.
        """
        body: dict[str, Any] = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": max_timeout_ms,
        }
        if proxy:
            body["proxy"] = {"url": proxy}
        if cookies:
            # FlareSolverr expects a list of {name, value, domain?, path?}.
            domain = httpx.URL(url).host
            body["cookies"] = [
                {"name": k, "value": v, "domain": domain, "path": "/"}
                for k, v in cookies.items()
            ]
        return body

    async def _do_post(self, *, body: dict[str, Any]) -> ChallengeSolution:
        """Single POST to the FlareSolverr ``/v1`` endpoint.

        Wrapped by ``_FLARE_POLICY`` in ``get()`` — this is the inner call.
        Exceptions raised here are categorised by the policy.
        """
        endpoint = f"{self._base_url}/v1"
        try:
            response = await self._client.post(
                endpoint,
                content=orjson.dumps(body),
            )
        except httpx.HTTPError as e:
            # All transport-layer failures get re-raised as
            # FlareSolverrUnavailableError so the resilience policy retries.
            raise FlareSolverrUnavailableError(
                f"FlareSolverr unreachable: {type(e).__name__}: {e}",
            ) from e

        if response.status_code != 200:
            raise FlareSolverrUnavailableError(
                f"FlareSolverr non-200: {response.status_code} body={response.text[:200]!r}",
            )

        try:
            envelope = response.json()
        except (ValueError, orjson.JSONDecodeError) as e:
            raise FlareSolverrUnavailableError(
                f"FlareSolverr returned non-JSON: {e}",
            ) from e

        return self._parse_envelope(envelope)

    @staticmethod
    def _parse_envelope(envelope: Mapping[str, Any]) -> ChallengeSolution:
        """Unpack FlareSolverr's v2 envelope into a ChallengeSolution.

        The envelope shape::

            {
              "status": "ok" | "error",
              "message": "...",
              "solution": {
                "url": "...",
                "status": 200,
                "cookies": [{"name": "...", "value": "...", ...}, ...],
                "userAgent": "...",
                "headers": {"...": "..."},
                "response": "<html>...</html>"
              }
            }
        """
        status = envelope.get("status")
        if status != "ok":
            msg = str(envelope.get("message", "unknown error"))
            raise FlareSolverrSolveError(f"solve failed: {msg}")

        sol_obj = envelope.get("solution")
        if not isinstance(sol_obj, Mapping):
            raise FlareSolverrUnavailableError("envelope missing 'solution' object")

        cookies_raw = sol_obj.get("cookies", [])
        if not isinstance(cookies_raw, list):
            raise FlareSolverrUnavailableError("'cookies' must be a list")
        cookies: dict[str, str] = {}
        for c in cookies_raw:
            if isinstance(c, Mapping):
                name = c.get("name")
                value = c.get("value")
                if isinstance(name, str) and isinstance(value, str):
                    cookies[name] = value

        # Metric: did we actually get a clearance cookie?
        had_clearance = "cf_clearance" in cookies
        scrape_captcha_total.labels(
            platform="cloudflare",
            resolved="yes" if had_clearance else "no",
        ).inc()

        return ChallengeSolution(
            url=cast("str", sol_obj.get("url", "")),
            status_code=int(cast("int", sol_obj.get("status", 0))),
            cookies=cookies,
            user_agent=cast("str", sol_obj.get("userAgent", "")),
            response_body=cast("str", sol_obj.get("response", "")),
            headers=cast("dict[str, str]", dict(sol_obj.get("headers", {}))),
        )


__all__ = [
    "ChallengeSolution",
    "FlareSolverr",
    "FlareSolverrError",
    "FlareSolverrSolveError",
    "FlareSolverrUnavailableError",
    "looks_like_challenge",
]


# =============================================================================
# Module-level resilient wrapper
# -----------------------------------------------------------------------------
# We define this OUTSIDE the FlareSolverr class so mypy's ParamSpec resolution
# works cleanly. The class method ``_resilient_post`` simply delegates here.
# =============================================================================


# mypy 1.11 cannot resolve ParamSpec/TypeVar through `resilient_call`'s
# decorator-factory + Optional[fallback] signature; runtime types are correct.
@resilient_call(_FLARE_POLICY)  # type: ignore[arg-type]
async def _resilient_post_impl(
    client: FlareSolverr,
    body: dict[str, Any],
) -> ChallengeSolution:
    """Run one resilient POST against the FlareSolverr endpoint."""
    return await client._do_post(body=body)
