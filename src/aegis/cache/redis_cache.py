"""Redis cache manager + priority queue primitives.

Two layers in one module (kept together because they share a connection
pool, serialisation format, and observability hooks):

1. **RedisCache** — typed key/value with TTL tiers (hot/warm/cold),
   namespacing, and hit/miss metrics. Supports both ``orjson``-serialised
   objects and raw bytes.
2. **PriorityQueue** — a thin wrapper around Redis sorted sets (``ZADD``,
   ``ZPOPMIN``, ``ZRANGEBYSCORE``). Lower score = higher priority. Score
   bands from ``constants.PRIORITY_P{0,1,2,3}_BOUNDARY`` let downstream
   workers cheaply drain only P0+P1 when under load.

Design choices:

- **redis-py asyncio client** (``redis.asyncio``), NOT ``aioredis`` — the
  latter is deprecated. Pinned via ``pyproject``.
- **Keyspace prefixes** (``aegis:{tenant}:{ns}:...``) so a shared Redis can
  serve multiple tenants without an accidental cross-tenant read.
- **Serde is pluggable** per call via ``encoder`` / ``decoder`` params —
  default is orjson, but raw bytes and msgpack are common in the pipeline.
- **NO lua scripts in v1.** I considered using ZPOPMIN-with-score or an
  atomic get-and-refresh script, but the simplicity savings outweigh the
  1-RTT cost; we revisit if the latency histogram demands it.

Author: AEGIS Pulse Team
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Any, Final, Self, cast

import orjson
from redis.asyncio import Redis
from redis.asyncio.connection import ConnectionPool
from redis.exceptions import RedisError

from aegis.constants import (
    CACHE_KEY_MAX_LEN,
    CACHE_TTL_COLD_SECONDS,
    CACHE_TTL_HOT_SECONDS,
    CACHE_TTL_WARM_SECONDS,
    PRIORITY_P0_BOUNDARY,
    PRIORITY_P1_BOUNDARY,
    PRIORITY_P2_BOUNDARY,
    PRIORITY_P3_BOUNDARY,
)
from aegis.core.logging import get_logger
from aegis.core.metrics import cache_ops_total

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from uuid import UUID

log = get_logger(__name__)


# =============================================================================
# TTL tiers
# =============================================================================


@unique
class CacheTier(StrEnum):
    """Logical TTL tier. Resolves to a concrete seconds value via ``ttl_of()``."""

    HOT = "hot"      # seconds — live counts, "is this hashtag currently trending"
    WARM = "warm"    # minutes — hourly derived features
    COLD = "cold"    # day — author profiles, product descriptors


def ttl_of(tier: CacheTier) -> int:
    """Return the TTL seconds for a tier. Raises on unknown tiers."""
    match tier:
        case CacheTier.HOT:
            return CACHE_TTL_HOT_SECONDS
        case CacheTier.WARM:
            return CACHE_TTL_WARM_SECONDS
        case CacheTier.COLD:
            return CACHE_TTL_COLD_SECONDS


# =============================================================================
# Priority bands
# =============================================================================


@unique
class Priority(StrEnum):
    """Priority tier. Lower numeric value (via ``band_of()``) = HIGHER priority."""

    P0 = "P0"   # breakout, pre-RED_TEAM-pass alerts
    P1 = "P1"   # saturation exits
    P2 = "P2"   # standard opportunities
    P3 = "P3"   # housekeeping


def band_of(priority: Priority) -> tuple[float, float]:
    """Return the ``[min_score, max_score)`` band for a priority tier.

    Scores within a band let the scheduler add ordered jitter (e.g. age of
    the item, or inverse velocity) without needing a separate tiebreak field.
    """
    match priority:
        case Priority.P0:
            return (PRIORITY_P0_BOUNDARY, PRIORITY_P1_BOUNDARY)
        case Priority.P1:
            return (PRIORITY_P1_BOUNDARY, PRIORITY_P2_BOUNDARY)
        case Priority.P2:
            return (PRIORITY_P2_BOUNDARY, PRIORITY_P3_BOUNDARY)
        case Priority.P3:
            return (PRIORITY_P3_BOUNDARY, PRIORITY_P3_BOUNDARY + 1_000.0)


# =============================================================================
# Configuration
# =============================================================================


@dataclass(frozen=True, slots=True)
class RedisConfig:
    """Declarative Redis connection config."""

    url: str
    """Standard redis URL: ``redis://[user:pass@]host:port/db`` or ``rediss://...`` for TLS."""

    max_connections: int = 32
    """Concurrent connections per pool. Tuned for a 16-core laptop; override in prod."""

    socket_timeout: float = 5.0
    socket_connect_timeout: float = 3.0
    health_check_interval: float = 30.0
    """Seconds between idle-connection liveness pings. Catches stale TCP."""

    # All keys written/read are prefixed with "{keyspace_prefix}:{tenant}:{ns}:..."
    keyspace_prefix: str = "aegis"

    @classmethod
    def from_env(cls, env: dict[str, str], **overrides: Any) -> Self:
        """Build a config from ``AEGIS_REDIS_URL`` (falls back to standard REDIS_URL)."""
        url = env.get("AEGIS_REDIS_URL") or env.get("REDIS_URL") or "redis://localhost:6379/0"
        return cls(url=url, **overrides)


# =============================================================================
# Serde
# =============================================================================

# Default serialiser pair. ``None`` is specialised — we want distinct sentinel
# behaviour for "key absent" vs "value = None".
_ORJSON_OPTS: Final[int] = orjson.OPT_SORT_KEYS | orjson.OPT_NAIVE_UTC

_MISSING: Final[object] = object()
"""Sentinel returned when a cache key does not exist."""


def _default_encode(value: Any) -> bytes:
    """orjson with stable key ordering + UTC-normalised naive datetimes."""
    if isinstance(value, bytes):
        return value
    return orjson.dumps(value, option=_ORJSON_OPTS)


def _default_decode(data: bytes) -> Any:
    """Assume JSON; fall back to raw bytes on decode failure (defensive)."""
    try:
        return orjson.loads(data)
    except orjson.JSONDecodeError:
        return data


# =============================================================================
# RedisCache
# =============================================================================


class RedisCache:
    """Lazy-initialised Redis cache wrapper."""

    def __init__(
        self,
        config: RedisConfig | None = None,
        *,
        url: str | None = None,
        namespace: str | None = None,
    ) -> None:
        if config is None:
            if url is None:
                raise ValueError("RedisCache requires either a RedisConfig or url=")
            kwargs: dict[str, Any] = {"url": url}
            if namespace is not None:
                kwargs["keyspace_prefix"] = namespace
            config = RedisConfig(**kwargs)
        self._config: RedisConfig = config
        self._pool: ConnectionPool | None = None
        self._client: Redis | None = None
        self._connect_lock: asyncio.Lock = asyncio.Lock()
        self._shutting_down: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the pool. Idempotent."""
        if self._client is not None:
            return
        async with self._connect_lock:
            # Double-checked locking — see PgPool.connect() for the getattr trick.
            if getattr(self, "_client", None) is not None:
                return
            log.info("redis.connecting", url=_sanitise_url(self._config.url))
            self._pool = ConnectionPool.from_url(
                self._config.url,
                max_connections=self._config.max_connections,
                socket_timeout=self._config.socket_timeout,
                socket_connect_timeout=self._config.socket_connect_timeout,
                health_check_interval=self._config.health_check_interval,
                # Keep raw bytes; we decode at call sites based on the encoder
                # used. Setting decode_responses=True would force every value
                # through UTF-8, which breaks msgpack / pickle payloads.
                decode_responses=False,
            )
            self._client = Redis(connection_pool=self._pool)
            # Proactive auth + version check so failures surface at connect
            # time rather than first use. redis-py's type stubs declare ping()
            # as "Awaitable[bool] | bool" which mypy can't await; the runtime
            # always returns an Awaitable in the async client, so we cast.
            try:
                await cast("Awaitable[bool]", self._client.ping())
            except RedisError:
                # Propagate but ensure we clean up the half-open pool.
                await self._pool.disconnect()
                self._pool = None
                self._client = None
                raise
            log.info("redis.connected")

    async def aclose(self) -> None:
        """Close the client and pool. Idempotent."""
        self._shutting_down = True
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as e:
                log.warning("redis.close_error", error=str(e))
            self._client = None
        if self._pool is not None:
            try:
                await self._pool.disconnect()
            except Exception as e:
                log.warning("redis.pool_disconnect_error", error=str(e))
            self._pool = None
        log.info("redis.closed")

    # Convenience aliases used by CLI / tests.
    start = connect
    close = aclose

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _build_key(
        self,
        *,
        namespace: str,
        key: str,
        tenant_id: UUID | None,
    ) -> bytes:
        """Assemble a fully-qualified Redis key."""
        tenant = str(tenant_id) if tenant_id else "_"
        k = f"{self._config.keyspace_prefix}:{tenant}:{namespace}:{key}"
        if len(k) > CACHE_KEY_MAX_LEN:
            raise ValueError(
                f"cache key exceeds {CACHE_KEY_MAX_LEN} chars: "
                f"{len(k)} chars, starts with {k[:80]!r}",
            )
        return k.encode("utf-8")

    @property
    def client(self) -> Redis:
        """Return the underlying redis-py async client (raises if disconnected)."""
        if self._client is None:
            raise RuntimeError("RedisCache is not connected; await .connect() first")
        return self._client

    # ------------------------------------------------------------------
    # Cache API
    # ------------------------------------------------------------------

    async def get(
        self,
        *,
        namespace: str,
        key: str,
        tenant_id: UUID | None = None,
        decoder: Callable[[bytes], Any] = _default_decode,
    ) -> Any:
        """Return the cached value, or ``_MISSING`` sentinel if absent.

        Use ``is _MISSING`` to distinguish "key absent" from "value is None".
        """
        full_key = self._build_key(namespace=namespace, key=key, tenant_id=tenant_id)
        try:
            raw: bytes | None = await self.client.get(full_key)
        except RedisError as e:
            cache_ops_total.labels(op="get", result="error").inc()
            log.warning("cache.get.error", key=full_key.decode(), error=str(e))
            raise
        if raw is None:
            cache_ops_total.labels(op="get", result="miss").inc()
            return _MISSING
        cache_ops_total.labels(op="get", result="hit").inc()
        return decoder(raw)

    async def set(
        self,
        *,
        namespace: str,
        key: str,
        value: Any,
        tier: CacheTier = CacheTier.WARM,
        tenant_id: UUID | None = None,
        encoder: Callable[[Any], bytes] = _default_encode,
        # XX=False NX=False is standard SET; XX=True only if already present;
        # NX=True only if absent. Mutually exclusive.
        xx: bool = False,
        nx: bool = False,
    ) -> bool:
        """Set a key with TTL from the given tier.

        Returns True if the SET actually wrote (i.e., XX/NX condition satisfied).
        """
        if xx and nx:
            raise ValueError("xx and nx are mutually exclusive")
        full_key = self._build_key(namespace=namespace, key=key, tenant_id=tenant_id)
        encoded = encoder(value)
        ttl = ttl_of(tier)
        try:
            result = await self.client.set(full_key, encoded, ex=ttl, xx=xx, nx=nx)
        except RedisError as e:
            cache_ops_total.labels(op="set", result="error").inc()
            log.warning("cache.set.error", key=full_key.decode(), error=str(e))
            raise
        wrote = bool(result)
        cache_ops_total.labels(op="set", result=("write" if wrote else "noop")).inc()
        return wrote

    async def delete(
        self,
        *,
        namespace: str,
        keys: Sequence[str],
        tenant_id: UUID | None = None,
    ) -> int:
        """Delete one or more keys. Returns the number of keys actually removed."""
        if not keys:
            return 0
        full_keys = [
            self._build_key(namespace=namespace, key=k, tenant_id=tenant_id)
            for k in keys
        ]
        try:
            n = await self.client.delete(*full_keys)
        except RedisError as e:
            cache_ops_total.labels(op="delete", result="error").inc()
            log.warning("cache.delete.error", count=len(keys), error=str(e))
            raise
        cache_ops_total.labels(op="delete", result="ok").inc()
        return cast("int", n)

    async def exists(
        self,
        *,
        namespace: str,
        key: str,
        tenant_id: UUID | None = None,
    ) -> bool:
        """Return True iff the key exists."""
        full_key = self._build_key(namespace=namespace, key=key, tenant_id=tenant_id)
        try:
            n = await self.client.exists(full_key)
        except RedisError:
            cache_ops_total.labels(op="exists", result="error").inc()
            raise
        result = bool(n)
        cache_ops_total.labels(op="exists", result=("hit" if result else "miss")).inc()
        return result

    async def get_or_set(
        self,
        *,
        namespace: str,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        tier: CacheTier = CacheTier.WARM,
        tenant_id: UUID | None = None,
    ) -> Any:
        """Read-through cache: return the cached value, or compute + store it.

        Thundering-herd note: multiple concurrent calls with the same key will
        each call ``factory()``. If that's expensive, add an application-level
        ``asyncio.Lock`` keyed by ``(namespace, key)``. We don't do it in the
        cache layer because lock lifetime is app-specific.
        """
        cached = await self.get(namespace=namespace, key=key, tenant_id=tenant_id)
        if cached is not _MISSING:
            return cached
        value = await factory()
        await self.set(
            namespace=namespace, key=key, value=value,
            tier=tier, tenant_id=tenant_id,
        )
        return value

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health(self, *, timeout: float = 2.0) -> CacheHealth:
        """PING + latency. Never raises on a normal failure."""
        if self._client is None:
            return CacheHealth(ok=False, latency_ms=None, error="not connected")
        t0 = time.monotonic()
        try:
            async with asyncio.timeout(timeout):
                pong = await cast("Awaitable[bool]", self._client.ping())
            if pong is not True:
                return CacheHealth(
                    ok=False, latency_ms=None,
                    error=f"unexpected PING response: {pong!r}",
                )
            return CacheHealth(
                ok=True,
                latency_ms=(time.monotonic() - t0) * 1000.0,
                error=None,
            )
        except TimeoutError:
            return CacheHealth(ok=False, latency_ms=None,
                               error=f"health timeout after {timeout}s")
        except Exception as e:
            return CacheHealth(ok=False, latency_ms=None,
                               error=f"{type(e).__name__}: {e}")


# =============================================================================
# PriorityQueue
# =============================================================================


class PriorityQueue:
    """Redis sorted-set priority queue.

    Each logical queue lives at key ``{prefix}:{tenant}:pq:{name}``. Members
    are opaque byte strings (produce any stable identifier your worker can
    resolve — usually a ``signal_id`` or ``trend_id``).
    """

    def __init__(
        self,
        cache: RedisCache,
        *,
        name: str,
        tenant_id: UUID | None = None,
    ) -> None:
        self._cache: RedisCache = cache
        self._name: str = name
        self._tenant_id: UUID | None = tenant_id

    @property
    def full_key(self) -> bytes:
        """The absolute Redis key for this queue."""
        # NOTE: priority queues live in a distinct "pq" namespace so that a
        # wild `DEL aegis:*:cache:*` never accidentally wipes queue state.
        return self._cache._build_key(
            namespace="pq", key=self._name, tenant_id=self._tenant_id,
        )

    # ---- enqueue ------------------------------------------------------

    async def push(
        self,
        member: str | bytes,
        *,
        priority: Priority,
        tiebreaker: float = 0.0,
    ) -> bool:
        """Enqueue ``member`` with score ``band_min + tiebreaker``.

        Returns True if the member is new, False if it was already present
        (score updated to the new value either way).
        """
        lo, hi = band_of(priority)
        if not 0.0 <= tiebreaker < (hi - lo):
            raise ValueError(
                f"tiebreaker {tiebreaker} out of band [0, {hi - lo}) for {priority}",
            )
        score = lo + tiebreaker
        m = member.encode("utf-8") if isinstance(member, str) else member
        try:
            added = await self._cache.client.zadd(self.full_key, {m: score})
        except RedisError as e:
            log.warning("pq.push.error", queue=self._name, error=str(e))
            raise
        return bool(added)

    async def push_many(
        self,
        items: Sequence[tuple[str | bytes, Priority, float]],
    ) -> int:
        """Bulk enqueue. Returns the count of NEW members added."""
        if not items:
            return 0
        mapping: dict[bytes, float] = {}
        for member, priority, tiebreaker in items:
            lo, hi = band_of(priority)
            if not 0.0 <= tiebreaker < (hi - lo):
                raise ValueError(
                    f"tiebreaker {tiebreaker} out of band for {priority}",
                )
            m = member.encode("utf-8") if isinstance(member, str) else member
            mapping[m] = lo + tiebreaker
        try:
            return cast("int", await self._cache.client.zadd(self.full_key, mapping))
        except RedisError as e:
            log.warning("pq.push_many.error", queue=self._name,
                        count=len(items), error=str(e))
            raise

    # ---- dequeue ------------------------------------------------------

    async def pop(self, *, max_priority: Priority | None = None) -> tuple[bytes, float] | None:
        """Pop the highest-priority (lowest-score) member.

        Args:
            max_priority: if set, only pop members at or better than this
                tier — returns ``None`` if the queue has no such members.
                Useful to let a dedicated "P0-only" worker drain urgent
                items while a general worker handles the rest.

        Returns:
            ``(member_bytes, score)`` or ``None`` if nothing matches.
        """
        if max_priority is None:
            try:
                res: list[tuple[bytes, float]] = await self._cache.client.zpopmin(
                    self.full_key, count=1,
                )
            except RedisError as e:
                log.warning("pq.pop.error", queue=self._name, error=str(e))
                raise
            return res[0] if res else None

        _, hi = band_of(max_priority)
        # Two steps: peek the lowest-score member, pop only if ≤ hi.
        # (ZPOPMIN with a max-score predicate isn't atomic on stock Redis,
        # so we use WATCH/MULTI or optimistic retry. For simplicity we peek
        # then ZREM; since we're single-process most of the time, the race
        # window is small. A lua script would close it fully — backlogged
        # for the v1.1 refactor.)
        try:
            candidates: list[tuple[bytes, float]] = await self._cache.client.zrange(
                self.full_key, 0, 0, withscores=True,
            )
        except RedisError as e:
            log.warning("pq.peek.error", queue=self._name, error=str(e))
            raise
        if not candidates:
            return None
        member, score = candidates[0]
        if score >= hi:
            return None  # nothing at or above the requested priority
        # Best-effort atomic remove. If someone else took it first, ZREM
        # returns 0 and we return None.
        removed = await self._cache.client.zrem(self.full_key, member)
        if not removed:
            return None
        return member, score

    async def pop_batch(
        self, *, max_items: int = 10, max_priority: Priority | None = None,
    ) -> list[tuple[bytes, float]]:
        """Pop up to ``max_items`` highest-priority members.

        When ``max_priority`` is set, stops early once a member exceeds the band.
        """
        if max_items < 1:
            raise ValueError("max_items must be >= 1")
        results: list[tuple[bytes, float]] = []
        for _ in range(max_items):
            popped = await self.pop(max_priority=max_priority)
            if popped is None:
                break
            results.append(popped)
        return results

    # ---- observability -----------------------------------------------

    async def size(self) -> int:
        """Return the number of members currently queued."""
        try:
            return cast("int", await self._cache.client.zcard(self.full_key))
        except RedisError as e:
            log.warning("pq.size.error", queue=self._name, error=str(e))
            raise

    async def sizes_by_priority(self) -> dict[Priority, int]:
        """Return the count of members per priority band."""
        client = self._cache.client
        # ZCOUNT requires min/max scores; use ≤ for upper bound via "(" prefix for exclusive.
        out: dict[Priority, int] = {}
        for p in (Priority.P0, Priority.P1, Priority.P2, Priority.P3):
            lo, hi = band_of(p)
            try:
                n = await client.zcount(self.full_key, lo, f"({hi}")
            except RedisError as e:
                log.warning("pq.sizes.error", queue=self._name, error=str(e))
                raise
            out[p] = cast("int", n)
        return out

    async def clear(self) -> None:
        """Drop all members. Intended for tests and operational emergencies."""
        try:
            await self._cache.client.delete(self.full_key)
        except RedisError as e:
            log.warning("pq.clear.error", queue=self._name, error=str(e))
            raise


# =============================================================================
# Helpers
# =============================================================================


@dataclass(frozen=True, slots=True)
class CacheHealth:
    """Outcome of ``RedisCache.health()``."""

    ok: bool
    latency_ms: float | None
    error: str | None


def _sanitise_url(url: str) -> str:
    """Remove credentials from a Redis URL before logging it.

    Input:  ``redis://user:secret@host:6379/0``
    Output: ``redis://***@host:6379/0``
    """
    if "@" not in url:
        return url
    scheme_sep = url.find("://")
    if scheme_sep < 0:
        return url
    rest = url[scheme_sep + 3 :]
    at_idx = rest.rfind("@")
    if at_idx < 0:
        return url
    return url[: scheme_sep + 3] + "***" + rest[at_idx:]


__all__ = [
    "CacheHealth",
    "CacheTier",
    "Priority",
    "PriorityQueue",
    "RedisCache",
    "RedisConfig",
    "band_of",
    "ttl_of",
]
