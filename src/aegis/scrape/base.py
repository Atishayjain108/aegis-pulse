"""Abstract ``SourceAdapter`` — base class for every per-source scraper.

A *source adapter* is the unit of pluggable scraping logic. Each one knows
how to:

1. Talk to ONE upstream platform (Reddit, TikTok, GDELT, …).
2. Iterate the platform's content for a given query / subreddit / hashtag.
3. Convert raw items into canonical ``ProductSignal`` instances.

Everything else — stealth, proxies, Cloudflare bypass, retries, metrics,
structured logging — is handled by this base class. Concrete adapters
override **two methods** in the typical case:

- ``def name(self) -> str``: a stable identity for metrics and registry.
- ``async def fetch_raw(...)``: yields raw items from the platform.
- ``def parse(self, raw, ctx) -> ProductSignal | None``: converts one
  raw item into a signal (return ``None`` to skip filtered/empty items).

The base class then orchestrates the run loop:

    fetch_raw  →  parse  →  validate (pydantic)  →  emit signal
                                ↑
                       resilience + stealth + proxy + metrics

Design choices:

- **Generic over the raw type**: ``SourceAdapter[RawT]`` lets each adapter
  state its raw-item type (e.g. PRAW Submission for Reddit). Type-checked.
- **Async iterator output**: scrapes can produce thousands of signals;
  yielding one-by-one keeps memory bounded and lets downstream pipelines
  start processing before the scrape completes.
- **Cooperative cancellation**: the run loop checks ``cancel_event`` between
  items so SIGTERM / dashboard kill-switch cleanly stops a long scrape.
- **Per-adapter rate limiting**: ``asyncio.Semaphore`` width from
  ``per_source_concurrency`` + a token-bucket leaky on ``per_source_rps``.
- **Cookie/UA reuse on Cloudflare success**: if FlareSolverr solves a
  challenge, the resulting cookie + UA are stuck to the proxy in our state
  for ``CF_CLEARANCE_TTL_SECONDS`` so subsequent requests piggyback for free.

Author: AEGIS Pulse Team
Relationship: parent of every concrete scraper in ``scrape/sources/``.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Generic, TypeVar
from uuid import UUID, uuid4

from aegis.constants import (
    PER_SOURCE_CONCURRENCY_DEFAULT,
    PER_SOURCE_RPS_DEFAULT,
)
from aegis.core.logging import bind_request_context, clear_request_context, get_logger
from aegis.core.metrics import (
    ingest_errors_total,
    ingest_latency_seconds,
    ingest_signals_total,
    scrape_requests_total,
)
from aegis.core.resilience import (
    ExhaustedError,
    ResiliencePolicy,
)
from aegis.scrape.cloudflare import (
    ChallengeSolution,
    FlareSolverr,
    FlareSolverrError,
    FlareSolverrUnavailableError,
    looks_like_challenge,
)
from aegis.scrape.proxies import (
    DEFAULT_DIRECT_SPEC,
    EmptyPoolError,
    ProxyKind,
    ProxyOutcome,
    ProxyPool,
    ProxySpec,
)
from aegis.scrape.stealth import StealthProfile

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from aegis.schemas.signal import ProductSignal

log = get_logger(__name__)


# =============================================================================
# Type variables
# =============================================================================

RawT = TypeVar("RawT")
"""Raw upstream item type — adapter-specific (e.g. praw.models.Submission)."""

ContextT = TypeVar("ContextT", bound="ScrapeContext")
"""Per-run context type — usually plain ScrapeContext, occasionally subclassed."""


# Cookies / UA stuck to a proxy after a successful CF solve.
_CF_CLEARANCE_TTL_SECONDS: Final[int] = 1_500  # ~25 min — slightly under CF default 30 min


# =============================================================================
# Run-time context — passed through fetch_raw + parse
# =============================================================================


@dataclass(slots=True)
class ScrapeContext:
    """Per-run state shared between adapter methods.

    A single ``ScrapeContext`` instance is created per ``adapter.run()`` call
    and threaded through the pipeline. Adapters MAY subclass to add fields
    specific to their source (e.g. Reddit subreddit name, GDELT date window).
    """

    correlation_id: UUID = field(default_factory=uuid4)
    """Stable id for this scrape run. Used as the trace correlation id and
    appears in every log line + metric label."""

    started_at_monotonic: float = field(default_factory=time.monotonic)
    """``time.monotonic()`` at run start. Used for elapsed-time stats."""

    # Stealth identity for this run. None until ``adapter.run()`` rolls one.
    profile: StealthProfile | None = None

    # Currently-bound proxy (set per request via _checkout_proxy).
    proxy: ProxySpec | None = None

    # Per-target sticky cookies/UA from a prior CF solve.
    cf_cookies: dict[str, dict[str, str]] = field(default_factory=dict)
    """Map ``proxy_id_str -> {cookie_name: value}``."""

    cf_user_agent: dict[str, str] = field(default_factory=dict)
    """Map ``proxy_id_str -> user-agent string``. Reuse ALONGSIDE the cookies."""

    cf_set_at: dict[str, float] = field(default_factory=dict)
    """``proxy_id_str -> monotonic time the cookies were set.`` Expires at
    ``+_CF_CLEARANCE_TTL_SECONDS``."""

    items_seen: int = 0
    items_emitted: int = 0
    items_failed: int = 0

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at_monotonic

    def cf_cookies_for(self, proxy: ProxySpec | None) -> tuple[dict[str, str], str | None]:
        """Return (cookies, user_agent) bound to this proxy, or empty if none/stale."""
        if proxy is None:
            return {}, None
        key = str(proxy.proxy_id)
        set_at = self.cf_set_at.get(key, 0.0)
        if set_at == 0.0 or (time.monotonic() - set_at) > _CF_CLEARANCE_TTL_SECONDS:
            # Stale or absent.
            self.cf_cookies.pop(key, None)
            self.cf_user_agent.pop(key, None)
            self.cf_set_at.pop(key, None)
            return {}, None
        return dict(self.cf_cookies.get(key, {})), self.cf_user_agent.get(key)

    def store_cf_solution(self, proxy: ProxySpec | None, solution: ChallengeSolution) -> None:
        """Cache a successful Cloudflare solve against this proxy."""
        if proxy is None:
            return
        key = str(proxy.proxy_id)
        self.cf_cookies[key] = solution.cookies
        self.cf_user_agent[key] = solution.user_agent
        self.cf_set_at[key] = time.monotonic()


# =============================================================================
# Configuration
# =============================================================================


@dataclass(frozen=True, slots=True)
class AdapterConfig:
    """Per-adapter configuration. Constructed at startup, immutable thereafter.

    Adapter subclasses may extend this via ``@dataclass(frozen=True, slots=True)``
    inheritance to add source-specific fields (e.g. Reddit ``client_id``)."""

    name: str
    """Identity used in logs, metrics, and the resilience policy registry."""

    per_source_concurrency: int = PER_SOURCE_CONCURRENCY_DEFAULT
    per_source_rps: float = PER_SOURCE_RPS_DEFAULT

    proxy_kinds: frozenset[ProxyKind] | None = None
    """Restrict to these proxy kinds. ``None`` accepts any kind including DIRECT."""

    proxy_country_codes: frozenset[str] | None = None
    """ISO-3166 alpha-2 codes. ``None`` accepts any country."""

    use_cloudflare_bypass: bool = False
    """If False, this adapter never invokes FlareSolverr. Set False for adapters
    using official APIs (Reddit, YouTube via key) that never see CF challenges."""

    timeout_seconds: float = 30.0
    """Per-request timeout. Shorter than the global default because adapters
    that need longer set it explicitly."""

    max_retries: int = 3
    mobile_user_agent: bool = False
    """If True, the stealth profile is sampled from mobile UA pool (TikTok,
    Instagram tend to give richer responses to mobile clients)."""

    max_signals: int = 100
    """Maximum number of ProductSignals to emit per run(). Used by CLI --limit."""

    extras: dict[str, Any] = field(default_factory=dict)
    """Adapter-specific keyword args (e.g. subreddit, query, hashtag).
    Populated by the CLI from --subreddit / --query flags."""


# =============================================================================
# Token bucket — primitive used for per-adapter rate limiting
# =============================================================================


class _TokenBucket:
    """Leaky token bucket. Async-safe via ``asyncio.Lock``.

    Capacity == burst size. Refill rate is ``rate_per_sec`` tokens per second.
    ``acquire(n=1)`` waits until ``n`` tokens are available, then deducts them.
    """

    def __init__(self, *, rate_per_sec: float, capacity: float | None = None) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        self._rate: float = rate_per_sec
        self._capacity: float = capacity if capacity is not None else max(1.0, rate_per_sec)
        self._tokens: float = self._capacity
        self._last_update: float = time.monotonic()
        self._lock: asyncio.Lock = asyncio.Lock()

    async def acquire(self, n: float = 1.0) -> None:
        if n > self._capacity:
            raise ValueError(f"requested {n} tokens > capacity {self._capacity}")
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_update
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                self._last_update = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                # Compute precise wait time outside the lock.
                deficit = n - self._tokens
                wait = deficit / self._rate
            # Sleep WITHOUT holding the lock so other coroutines can probe.
            await asyncio.sleep(wait)


# =============================================================================
# The base class
# =============================================================================


class SourceAdapter(ABC, Generic[RawT]):
    """Base class for every scrape source.

    Lifecycle:

        adapter = MyAdapter(config, proxy_pool=pool, flaresolverr=fs)
        async for signal in adapter.run(query="..."):
            await persist(signal)

    The run loop:
        1. Roll a fresh ``StealthProfile`` for this run.
        2. Iterate ``fetch_raw()`` to get raw items.
        3. For each raw item, call ``parse()`` to produce a ``ProductSignal``.
        4. Pydantic validates the signal at construction; failures land in
           the ``ingest_errors`` metric with the failing field.
        5. Yield the signal upward.

    Subclasses MUST implement:
        - ``name`` (property)
        - ``fetch_raw`` (async generator of RawT)
        - ``parse`` (RawT, ScrapeContext) -> ProductSignal | None

    Subclasses MAY override:
        - ``setup`` / ``teardown`` for source-specific resource management
        - ``classify_response`` to customise CF/ban detection per source
    """

    # Subclasses set this if their `parse()` is purely synchronous and CPU-bound.
    # When True, we offload to a thread pool — keeps the event loop responsive.
    parse_is_blocking: bool = False

    # Default resilience policy. Subclasses can override by passing a custom
    # policy in their config or by overriding `_resilience_policy()`.
    def _default_resilience_policy(self) -> ResiliencePolicy:
        return ResiliencePolicy(
            name=f"scrape.{self.name}",
            timeout=self._config.timeout_seconds,
            max_attempts=self._config.max_retries,
        )

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        config: AdapterConfig,
        *,
        proxy_pool: ProxyPool | None = None,
        flaresolverr: FlareSolverr | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        self._config: AdapterConfig = config
        self._proxy_pool: ProxyPool | None = proxy_pool
        self._flaresolverr: FlareSolverr | None = flaresolverr
        self._cancel_event: asyncio.Event = cancel_event or asyncio.Event()
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(config.per_source_concurrency)
        self._rate_limiter: _TokenBucket = _TokenBucket(rate_per_sec=config.per_source_rps)
        self._log = log.bind(adapter=config.name)

    # ------------------------------------------------------------------
    # Required overrides
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identity. Used in metrics labels — keep it lowercase/snake_case."""

    @abstractmethod
    def fetch_raw(self, ctx: ScrapeContext, **params: Any) -> AsyncIterator[RawT]:
        """Async iterator over raw items from the platform.

        Receives ``ctx`` and adapter-specific keyword args (``subreddit=``,
        ``hashtag=``, ``query=``...). Implementations should yield items as
        they arrive — do not buffer the whole result set.

        Implementations should call ``self._rate_limit()`` and
        ``self._make_request()`` for outbound requests so global throttling +
        resilience apply uniformly.
        """

    @abstractmethod
    def parse(self, raw: RawT, ctx: ScrapeContext) -> ProductSignal | None:
        """Convert one raw item into a ``ProductSignal`` (or ``None`` to skip).

        Implementations should:
            - Compute the content hash via ``compute_content_hash(...)``.
            - Pass it to ``ProductSignal(...)`` (NOT compute it inside the
              constructor — pydantic validators run immediately).
            - Return ``None`` for items that should be filtered out (e.g.
              deleted comments, NSFW marked, below-velocity-floor).

        Raising an exception here is treated as a parse error and counted
        against ``ingest_errors_total``; the adapter continues with the next item.
        """

    # ------------------------------------------------------------------
    # Optional overrides
    # ------------------------------------------------------------------

    async def setup(self, ctx: ScrapeContext) -> None:
        """Hook for source-specific initialisation (e.g. PRAW client login).

        Default is a no-op.
        """

    async def teardown(self, ctx: ScrapeContext) -> None:
        """Hook for source-specific cleanup (e.g. closing aiohttp sessions)."""

    def classify_response(
        self,
        *,
        status_code: int | None,
        body: str | None,
        headers: Mapping[str, str] | None,
    ) -> str:
        """Categorise an HTTP response. Override for custom ban / challenge detection.

        Returns one of ``"ok"``, ``"challenge"``, ``"banned"``, ``"rate_limit"``.
        Default uses ``looks_like_challenge`` from the cloudflare module.
        """
        if looks_like_challenge(status_code=status_code, body=body, headers=headers):
            return "challenge"
        if status_code is not None:
            if status_code == 429:
                return "rate_limit"
            if status_code in (401, 403, 451):
                return "banned"
        return "ok"

    # ------------------------------------------------------------------
    # The run loop — DO NOT override
    # ------------------------------------------------------------------

    async def run(self, **params: Any) -> AsyncIterator[ProductSignal]:
        """Run a full scrape. Yields ``ProductSignal``s as they're produced.

        The loop is interrupted gracefully if ``cancel_event`` is set.
        """
        ctx = self._new_context()
        ctx.profile = StealthProfile.random(mobile=self._config.mobile_user_agent)

        bind_request_context(
            adapter=self.name,
            correlation_id=str(ctx.correlation_id),
        )
        self._log.info(
            "adapter.run.start",
            params={k: v for k, v in params.items() if not _looks_like_secret(k, v)},
            profile_locale=ctx.profile.locale,
            profile_tz=ctx.profile.timezone,
        )

        try:
            await self.setup(ctx)
        except Exception as e:
            ingest_errors_total.labels(platform=self.name, category="setup").inc()
            self._log.error("adapter.setup.failed", error=str(e))
            clear_request_context()
            raise

        try:
            async for raw in self.fetch_raw(ctx, **params):
                if self._cancel_event.is_set():
                    self._log.warning("adapter.run.cancelled", emitted=ctx.items_emitted)
                    break

                ctx.items_seen += 1
                signal = await self._parse_one(raw, ctx)
                if signal is None:
                    continue

                ctx.items_emitted += 1
                ingest_signals_total.labels(
                    platform=signal.platform.value,
                    tier=signal.tier.value,
                ).inc()
                ingest_latency_seconds.labels(platform=signal.platform.value).observe(
                    ctx.elapsed_seconds,
                )
                yield signal

        finally:
            try:
                await self.teardown(ctx)
            except Exception as e:
                self._log.error("adapter.teardown.failed", error=str(e))

            self._log.info(
                "adapter.run.end",
                seen=ctx.items_seen,
                emitted=ctx.items_emitted,
                failed=ctx.items_failed,
                elapsed_seconds=round(ctx.elapsed_seconds, 2),
            )
            clear_request_context()

    # ------------------------------------------------------------------
    # Internal helpers — usable by subclasses
    # ------------------------------------------------------------------

    def _new_context(self) -> ScrapeContext:
        """Override in subclasses that use a custom ``ScrapeContext`` subclass."""
        return ScrapeContext()

    async def _parse_one(self, raw: RawT, ctx: ScrapeContext) -> ProductSignal | None:
        """Run ``parse`` with metrics + error catch.

        Offloads to a thread pool when ``parse_is_blocking`` is True. This is
        the path where most parse exceptions land; we count + log + continue.
        """
        try:
            if self.parse_is_blocking:
                return await asyncio.to_thread(self.parse, raw, ctx)
            return self.parse(raw, ctx)
        except Exception as e:
            ctx.items_failed += 1
            ingest_errors_total.labels(platform=self.name, category="parse").inc()
            self._log.warning(
                "adapter.parse.failed",
                error_type=type(e).__name__,
                error=str(e)[:200],
                # Don't dump the raw item — could be huge or contain PII.
            )
            return None

    async def _rate_limit(self, n: float = 1.0) -> None:
        """Wait for the per-adapter token bucket. Subclasses call before each request."""
        await self._rate_limiter.acquire(n)

    @asynccontextmanager
    async def _checkout_proxy(
        self,
        ctx: ScrapeContext,
        *,
        target_host: str,
        session_key: str | None = None,
    ) -> AsyncIterator[ProxySpec]:
        """Acquire a proxy + remember it on ``ctx``.

        Yields:
            The chosen ``ProxySpec``. Caller MUST call
            ``self._report_proxy_outcome(...)`` after the request finishes.
        """
        if self._proxy_pool is None:
            ctx.proxy = DEFAULT_DIRECT_SPEC
            yield DEFAULT_DIRECT_SPEC
            return

        try:
            spec = await self._proxy_pool.acquire(
                target_host=target_host,
                country_codes=self._config.proxy_country_codes,
                kind_in=self._config.proxy_kinds,
                session_key=session_key,
            )
        except EmptyPoolError:
            self._log.warning("proxy.empty_pool", target=target_host)
            ctx.proxy = DEFAULT_DIRECT_SPEC
            yield DEFAULT_DIRECT_SPEC
            return
        ctx.proxy = spec
        try:
            yield spec
        finally:
            ctx.proxy = None

    async def _report_proxy_outcome(
        self,
        proxy: ProxySpec,
        *,
        target_host: str,
        outcome: ProxyOutcome,
        latency_ms: float | None = None,
    ) -> None:
        """Report request outcome back to the proxy pool. No-op for DIRECT."""
        if self._proxy_pool is None or proxy.kind is ProxyKind.DIRECT:
            return
        await self._proxy_pool.report(
            proxy_id=proxy.proxy_id,
            target_host=target_host,
            outcome=outcome,
            latency_ms=latency_ms,
        )

    async def _ensure_cf_clearance(
        self,
        ctx: ScrapeContext,
        *,
        url: str,
    ) -> tuple[dict[str, str], str | None]:
        """If a Cloudflare challenge was previously solved for the current proxy,
        return cached cookies + UA. Otherwise (and if ``use_cloudflare_bypass``
        is enabled), invoke FlareSolverr now.

        Returns:
            ``(cookies_dict, user_agent_or_None)``. Empty dict if no clearance
            is in effect.
        """
        cookies, ua = ctx.cf_cookies_for(ctx.proxy)
        if cookies:
            return cookies, ua

        if not self._config.use_cloudflare_bypass or self._flaresolverr is None:
            return {}, None

        # Solve!
        proxy_url = ctx.proxy.url if ctx.proxy and ctx.proxy.kind is not ProxyKind.DIRECT else None
        try:
            solution = await self._flaresolverr.get(url, proxy=proxy_url)
        except (FlareSolverrError, FlareSolverrUnavailableError, ExhaustedError) as e:
            self._log.warning(
                "cloudflare.solve.failed",
                error_type=type(e).__name__,
                target=url,
            )
            return {}, None

        ctx.store_cf_solution(ctx.proxy, solution)
        self._log.info(
            "cloudflare.solve.ok",
            target=url,
            had_clearance=solution.cf_clearance is not None,
        )
        return dict(solution.cookies), solution.user_agent

    def _record_request_metric(self, *, method: str = "http") -> None:
        """Count an outbound scrape request. Subclasses call once per HTTP fetch."""
        scrape_requests_total.labels(platform=self.name, method=method).inc()

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Request graceful cancellation. The run loop exits at the next item boundary."""
        self._cancel_event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()


# =============================================================================
# Helpers
# =============================================================================


def _looks_like_secret(key: str, value: object) -> bool:
    """Return True if ``key=value`` looks like a credential (used for log scrubbing)."""
    _ = value  # currently unused; reserved for future heuristics
    needle = key.lower()
    return any(s in needle for s in ("token", "secret", "password", "key", "auth"))


__all__ = [
    "AdapterConfig",
    "ScrapeContext",
    "SourceAdapter",
]
