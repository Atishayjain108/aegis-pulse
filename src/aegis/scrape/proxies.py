"""Proxy pool with EMA health scoring, ban detection, and session affinity.

Maintains a population of proxies (free-tier sources by default — Tor exits,
ProxyBroker2 aggregator output, optional paid lists) and routes scrape
requests through the best one for a given target.

Key features:

- **Per-proxy EMA health**: latency + success rate as exponential moving
  averages, with ``PROXY_HEALTH_EMA_ALPHA`` controlling the smoothing.
- **Per-target ban tracking**: a proxy may work fine on Reddit but be banned
  on TikTok. We track ban status in a ``(proxy_id, target_host)`` map.
- **Sticky sessions**: callers may pin a proxy for a "session" (e.g. paginated
  scrape across N pages) so cookies remain consistent.
- **Geo filtering**: callers may require ``country_code in {...}``.
- **Quarantine**: a banned proxy is removed from rotation for
  ``PROXY_BAN_QUARANTINE_SECONDS`` then re-tested.
- **Min-samples gate**: a proxy needs ``PROXY_HEALTH_MIN_SAMPLES`` requests
  before its EMA is "trusted" — prevents one network blip from blacklisting
  a good proxy.

Design choices:

- **In-memory state** for v1 (single-process). Multi-process coordination
  via Redis is documented but unimplemented; the right solution there is
  a proxy-management sidecar (e.g. mubeng), not a custom Redis lua script.
- **No HTTP client owned**: this module returns a ``ProxySpec`` (URL,
  credentials, geo metadata). The actual HTTP/Playwright client is
  configured by the adapter — proxy pool stays transport-agnostic.
- **Fail-closed on empty pool**: ``acquire()`` raises rather than returns
  ``None`` so callers don't accidentally make direct (un-proxied) requests.

Author: AEGIS Pulse Team
Relationship: consumed by ``base.SourceAdapter``; populated at startup from
config (env vars, ProxyBroker2 output, paid provider APIs).
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Final
from urllib.parse import urlparse
from uuid import UUID, uuid4

from aegis.constants import (
    PROXY_BAN_QUARANTINE_SECONDS,
    PROXY_HEALTH_EMA_ALPHA,
    PROXY_HEALTH_MIN_SAMPLES,
)
from aegis.core.logging import get_logger
from aegis.core.metrics import scrape_proxy_ban_total

if TYPE_CHECKING:
    from collections.abc import Iterable

log = get_logger(__name__)


# =============================================================================
# Types
# =============================================================================


@unique
class ProxyKind(StrEnum):
    """Provider category. Drives default reliability assumptions and metric labels."""

    TOR = "tor"
    """Tor exit node. Free, but slow and well-known to anti-bot services."""

    PUBLIC_FREE = "public_free"
    """Aggregated free proxies (ProxyBroker2 output). Volatile."""

    DATACENTER = "datacenter"
    """Paid datacenter (e.g. SmartProxy, IPRoyal DC). Easy to detect, fast."""

    RESIDENTIAL = "residential"
    """Paid residential. Best stealth-vs-cost tradeoff for bot-defended targets."""

    MOBILE = "mobile"
    """Paid mobile/4G/5G. Most expensive, hardest to block."""

    DIRECT = "direct"
    """Sentinel for "no proxy" — included so the pool can route trusted
    (T1 / official-API) traffic through the host's own IP."""


@dataclass(frozen=True, slots=True)
class ProxySpec:
    """A single proxy entry. Immutable; mutable state lives in ``ProxyState``."""

    proxy_id: UUID
    """Stable UUID. Survives restart only if the pool is persisted."""

    url: str
    """Format: ``scheme://[user:pass@]host:port``. Schemes: http, https, socks5."""

    kind: ProxyKind

    country_code: str | None = None
    """ISO 3166-1 alpha-2. ``None`` if unknown / mixed."""

    sticky: bool = False
    """If True, the provider gives a stable IP for a session (residential
    'sticky' upstreams). Affects rotation behaviour."""

    notes: str = ""
    """Human-readable. e.g. ``'IPRoyal residential — US, hourly rotation'``."""

    @property
    def host(self) -> str:
        """Bare hostname extracted from ``url``."""
        parsed = urlparse(self.url)
        return parsed.hostname or ""


@dataclass(slots=True)
class ProxyState:
    """Mutable health state for one ``ProxySpec``."""

    proxy_id: UUID
    samples: int = 0
    """Total observed requests through this proxy."""

    ema_latency_ms: float = 0.0
    """Exponential moving average of latency. ``0.0`` until ``samples >= 1``."""

    ema_success: float = 0.0
    """EMA of success rate (1.0 = success, 0.0 = fail)."""

    last_used_at: float = 0.0
    """``time.monotonic()`` of last issue. Used for round-robin tiebreak."""

    # Per-target ban map: (target_host) → ban_until_monotonic
    banned_until: dict[str, float] = field(default_factory=dict)

    @property
    def trusted(self) -> bool:
        """``True`` once we have enough samples to trust the EMA."""
        return self.samples >= PROXY_HEALTH_MIN_SAMPLES

    def composite_score(self) -> float:
        """Combine success-rate and latency into a single score in [0, 1].

        Untrusted proxies (insufficient samples) get a fixed mid-range score
        so they get tried but don't beat established good proxies. Score
        is used by ``ProxyPool.acquire()`` to pick the best candidate.
        """
        if not self.trusted:
            return 0.5
        # Latency: clamp to [0, 5000] ms; > 5s effectively scores zero.
        latency_score = max(0.0, 1.0 - min(self.ema_latency_ms, 5000.0) / 5000.0)
        # Weight success at 70%, latency at 30%. We care more about
        # "does this work" than "how fast" — a 1% ban rate is a much
        # bigger problem than a 200ms latency increase.
        return 0.7 * self.ema_success + 0.3 * latency_score

    def is_banned_for(self, target_host: str, *, now_monotonic: float) -> bool:
        """Check if quarantined for ``target_host``."""
        until = self.banned_until.get(target_host, 0.0)
        if until <= 0.0:
            return False
        if now_monotonic >= until:
            # Quarantine elapsed — clear and let the proxy back in.
            del self.banned_until[target_host]
            return False
        return True


# =============================================================================
# Outcome reporting
# =============================================================================


@unique
class ProxyOutcome(StrEnum):
    """How a request through a proxy went. Reported back to the pool."""

    SUCCESS = "success"
    """2xx response, content matched expectations."""

    SOFT_FAIL = "soft_fail"
    """5xx, network error, timeout — likely transient. Counts against EMA
    but does NOT trigger a ban."""

    BANNED = "banned"
    """Anti-bot challenge that the proxy itself caused (Cloudflare hard block,
    HTTP 451, account-disabled redirect, captcha that survives FlareSolverr).
    Triggers per-target quarantine."""

    SLOW = "slow"
    """Successful but above latency budget. Tracked for EMA, not banned."""


# =============================================================================
# The pool
# =============================================================================


class ProxyPool:
    """Thread-safe-ish proxy pool. Designed for asyncio single-loop use.

    The module-level state is protected by an ``asyncio.Lock`` rather than a
    threading lock — we run on a single event loop per process. If you need
    multi-process coordination, use ``mubeng`` or ``proxify`` as a sidecar.
    """

    def __init__(
        self,
        *,
        rng_seed: int | None = None,
    ) -> None:
        # Immutable proxy population (UUID → ProxySpec)
        self._specs: dict[UUID, ProxySpec] = {}
        # Mutable per-proxy state
        self._states: dict[UUID, ProxyState] = {}
        # Sticky session bindings: session_key → proxy_id
        self._sticky: dict[str, UUID] = {}
        # Coordination
        self._lock: asyncio.Lock = asyncio.Lock()
        self._rng: random.Random = (
            random.Random(rng_seed) if rng_seed is not None else random.Random()
        )

    # ------------------------------------------------------------------
    # Population management
    # ------------------------------------------------------------------

    async def add(self, spec: ProxySpec) -> None:
        """Register a proxy. No-op if proxy_id already known."""
        async with self._lock:
            if spec.proxy_id in self._specs:
                log.debug("proxy.duplicate", proxy_id=str(spec.proxy_id))
                return
            self._specs[spec.proxy_id] = spec
            self._states[spec.proxy_id] = ProxyState(proxy_id=spec.proxy_id)
            log.info(
                "proxy.added",
                proxy_id=str(spec.proxy_id),
                kind=spec.kind.value,
                country=spec.country_code,
                # Don't log credentials — sanitise the URL.
                url=_sanitise_proxy_url(spec.url),
            )

    async def add_many(self, specs: Iterable[ProxySpec]) -> int:
        """Bulk add. Returns the number actually inserted (excluding dupes)."""
        n = 0
        for s in specs:
            before = len(self._specs)
            await self.add(s)
            if len(self._specs) > before:
                n += 1
        return n

    async def remove(self, proxy_id: UUID) -> bool:
        """Drop a proxy completely. Returns True if it was present."""
        async with self._lock:
            existed = proxy_id in self._specs
            self._specs.pop(proxy_id, None)
            self._states.pop(proxy_id, None)
            # Purge any sticky bindings.
            self._sticky = {k: v for k, v in self._sticky.items() if v != proxy_id}
            return existed

    @property
    def size(self) -> int:
        return len(self._specs)

    # ------------------------------------------------------------------
    # Acquisition
    # ------------------------------------------------------------------

    async def acquire(
        self,
        *,
        target_host: str,
        country_codes: frozenset[str] | None = None,
        kind_in: frozenset[ProxyKind] | None = None,
        session_key: str | None = None,
    ) -> ProxySpec:
        """Pick the best proxy for a request to ``target_host``.

        Args:
            target_host: hostname being scraped (e.g. ``"reddit.com"``). Used
                to skip proxies in the per-target ban map.
            country_codes: optional ISO-3166 alpha-2 set. Restrict to these
                geos. Passing ``None`` matches all (including unknown geo).
            kind_in: optional set of acceptable kinds.
            session_key: if set, the same proxy is reused for subsequent
                ``acquire()`` calls with this key (sticky). Useful for
                paginated scrapes that share cookies.

        Returns:
            A ``ProxySpec``.

        Raises:
            EmptyPoolError: no proxy matches the filters.
        """
        async with self._lock:
            # Sticky path
            if session_key:
                pinned_id = self._sticky.get(session_key)
                if pinned_id is not None:
                    spec = self._specs.get(pinned_id)
                    state = self._states.get(pinned_id)
                    if (
                        spec is not None
                        and state is not None
                        and not state.is_banned_for(
                            target_host,
                            now_monotonic=time.monotonic(),
                        )
                    ):
                        state.last_used_at = time.monotonic()
                        return spec
                    # Sticky binding stale → fall through and re-select.
                    self._sticky.pop(session_key, None)

            candidates = self._filter_candidates(
                target_host=target_host,
                country_codes=country_codes,
                kind_in=kind_in,
            )
            if not candidates:
                raise EmptyPoolError(
                    f"no proxy available for target={target_host!r} "
                    f"country={country_codes} kind={kind_in}",
                )

            # Sort by composite_score DESC, then by last_used_at ASC (LRU
            # within the best-scoring set). Best score first.
            candidates.sort(
                key=lambda x: (-x[1].composite_score(), x[1].last_used_at),
            )

            # Take from the top quartile (or top 1 if pool is small) and
            # randomise within it. This blends "use the best" with "spread
            # load so no single IP's pattern is too obvious".
            top_n = max(1, len(candidates) // 4)
            spec, state = self._rng.choice(candidates[:top_n])
            state.last_used_at = time.monotonic()

            if session_key:
                self._sticky[session_key] = spec.proxy_id

            return spec

    def _filter_candidates(
        self,
        *,
        target_host: str,
        country_codes: frozenset[str] | None,
        kind_in: frozenset[ProxyKind] | None,
    ) -> list[tuple[ProxySpec, ProxyState]]:
        """Return all (spec, state) pairs that satisfy the filters AND
        are not currently banned for the target host."""
        now = time.monotonic()
        out: list[tuple[ProxySpec, ProxyState]] = []
        for proxy_id, spec in self._specs.items():
            state = self._states[proxy_id]
            if state.is_banned_for(target_host, now_monotonic=now):
                continue
            if country_codes is not None and (
                spec.country_code is None or spec.country_code not in country_codes
            ):
                continue
            if kind_in is not None and spec.kind not in kind_in:
                continue
            out.append((spec, state))
        return out

    # ------------------------------------------------------------------
    # Outcome reporting
    # ------------------------------------------------------------------

    async def report(
        self,
        *,
        proxy_id: UUID,
        target_host: str,
        outcome: ProxyOutcome,
        latency_ms: float | None = None,
    ) -> None:
        """Update health state from a request outcome.

        Adapter MUST call this once per request. Skipping reports causes
        the EMA to lag and bad proxies to stay in rotation longer than
        they should.
        """
        async with self._lock:
            state = self._states.get(proxy_id)
            if state is None:
                # Proxy was removed mid-flight; ignore.
                log.debug("proxy.report.unknown", proxy_id=str(proxy_id))
                return

            # EMA update on success: 1.0 (success), 0.0 (failure)
            success_value = 1.0 if outcome in (ProxyOutcome.SUCCESS, ProxyOutcome.SLOW) else 0.0
            alpha = PROXY_HEALTH_EMA_ALPHA

            if state.samples == 0:
                state.ema_success = success_value
                if latency_ms is not None:
                    state.ema_latency_ms = latency_ms
            else:
                state.ema_success = alpha * success_value + (1 - alpha) * state.ema_success
                if latency_ms is not None:
                    state.ema_latency_ms = alpha * latency_ms + (1 - alpha) * state.ema_latency_ms
            state.samples += 1

            if outcome is ProxyOutcome.BANNED:
                ban_until = time.monotonic() + PROXY_BAN_QUARANTINE_SECONDS
                state.banned_until[target_host] = ban_until
                # Metric: a proxy banned for THIS target.
                # Label "platform" expects a Platform enum value; we don't
                # have that here (target_host could be any). We label by
                # host host instead — bounded cardinality because the set of
                # target hosts is small.
                spec = self._specs.get(proxy_id)
                if spec is not None:
                    scrape_proxy_ban_total.labels(platform=target_host).inc()
                log.warning(
                    "proxy.banned",
                    proxy_id=str(proxy_id),
                    target=target_host,
                    quarantine_seconds=PROXY_BAN_QUARANTINE_SECONDS,
                    ema_success=round(state.ema_success, 3),
                )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    async def stats(self) -> PoolStats:
        """Snapshot of the pool's health for diagnostics / dashboards."""
        async with self._lock:
            now = time.monotonic()
            specs = list(self._specs.values())
            states = [self._states[s.proxy_id] for s in specs]
            trusted = sum(1 for s in states if s.trusted)
            avg_success = sum(s.ema_success for s in states if s.samples > 0) / max(
                1, sum(1 for s in states if s.samples > 0)
            )
            avg_latency = sum(s.ema_latency_ms for s in states if s.samples > 0) / max(
                1, sum(1 for s in states if s.samples > 0)
            )
            ban_count = sum(
                1 for st in states for ban_until in st.banned_until.values() if ban_until > now
            )
            return PoolStats(
                total=len(specs),
                trusted=trusted,
                avg_success_rate=avg_success,
                avg_latency_ms=avg_latency,
                active_bans=ban_count,
                by_kind={
                    k: sum(1 for sp in specs if sp.kind == k)
                    for k in ProxyKind
                    if any(sp.kind == k for sp in specs)
                },
            )


# =============================================================================
# Errors & helpers
# =============================================================================


class EmptyPoolError(RuntimeError):
    """Raised by ``acquire()`` when no proxy matches the filters."""


@dataclass(frozen=True, slots=True)
class PoolStats:
    total: int
    trusted: int
    avg_success_rate: float
    avg_latency_ms: float
    active_bans: int
    by_kind: dict[ProxyKind, int]


def _sanitise_proxy_url(url: str) -> str:
    """Strip credentials before logging.

    ``http://user:pass@host:port`` → ``http://***@host:port``
    """
    parsed = urlparse(url)
    if parsed.username:
        netloc = f"***@{parsed.hostname}:{parsed.port}" if parsed.port else f"***@{parsed.hostname}"
        return f"{parsed.scheme}://{netloc}{parsed.path}"
    return url


def make_proxy_spec(
    url: str,
    *,
    kind: ProxyKind,
    country_code: str | None = None,
    sticky: bool = False,
    notes: str = "",
) -> ProxySpec:
    """Helper to construct a ``ProxySpec`` with a fresh UUID.

    Use this in adapter / config code rather than constructing ``ProxySpec``
    directly — keeps UUID generation in one place.
    """
    return ProxySpec(
        proxy_id=uuid4(),
        url=url,
        kind=kind,
        country_code=country_code,
        sticky=sticky,
        notes=notes,
    )


# Default seed pool — DIRECT only. Real proxies must be loaded from config.
# DIRECT is included so adapters with low ToS_RISK (T1 official APIs) can
# safely fall through to "no proxy" without an EmptyPoolError.
DEFAULT_DIRECT_SPEC: Final[ProxySpec] = ProxySpec(
    proxy_id=UUID("00000000-0000-0000-0000-00000000DEAD"),
    url="direct://localhost",
    kind=ProxyKind.DIRECT,
    country_code=None,
    sticky=False,
    notes="No-proxy sentinel; used for trusted (T1 official-API) traffic.",
)


__all__ = [
    "DEFAULT_DIRECT_SPEC",
    "EmptyPoolError",
    "PoolStats",
    "ProxyKind",
    "ProxyOutcome",
    "ProxyPool",
    "ProxySpec",
    "ProxyState",
    "make_proxy_spec",
]
