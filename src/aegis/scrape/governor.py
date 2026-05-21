"""
Concurrency governor — prevents IP bans and socket exhaustion.

Rules enforced:
- Global semaphore: max N adapters making HTTP requests simultaneously
- FlareSolverr semaphore: max 2 concurrent bypass requests
- Per-domain rate limiter: token bucket, configurable per domain
- Jitter: random 0–500ms delay before each request batch
"""
from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    capacity: int
    refill_rate: float        # tokens per second
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)

    def __post_init__(self) -> None:
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()

    def consume(self, tokens: int = 1) -> bool:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    async def wait_and_consume(self, tokens: int = 1) -> None:
        while True:
            if self.consume(tokens):
                return
            await asyncio.sleep(0.1)


class ConcurrencyGovernor:
    """
    Central concurrency manager injected into SwarmOrchestrator.

    Usage:
        async with governor.global_slot():
            result = await adapter.scrape(...)

        async with governor.flaresolverr_slot():
            result = await flaresolverr_bypass(...)
    """

    def __init__(
        self,
        max_concurrent: int = 5,
        max_flaresolverr: int = 2,
        jitter_max_ms: int = 500,
    ) -> None:
        self._global = asyncio.Semaphore(max_concurrent)
        self._flaresolverr = asyncio.Semaphore(max_flaresolverr)
        self._jitter_max = jitter_max_ms / 1000
        self._domain_buckets: dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(capacity=10, refill_rate=0.5)
        )

    def global_slot(self) -> asyncio.Semaphore:
        return self._global

    def flaresolverr_slot(self) -> asyncio.Semaphore:
        return self._flaresolverr

    async def jitter(self) -> None:
        delay = random.uniform(0, self._jitter_max)
        await asyncio.sleep(delay)

    async def domain_slot(self, domain: str) -> None:
        await self._domain_buckets[domain].wait_and_consume()


__all__ = ["TokenBucket", "ConcurrencyGovernor"]
