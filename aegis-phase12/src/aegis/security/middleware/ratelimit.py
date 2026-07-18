"""
aegis.security.middleware.ratelimit — Redis token-bucket rate-limiting middleware.

Algorithm: Token Bucket via a single Lua script executed atomically in Redis.
The script grants the request if tokens remain, otherwise returns the retry-after
value.

Headers injected on every response:
    X-RateLimit-Limit      — requests per minute allowed
    X-RateLimit-Remaining  — tokens left in current window
    X-RateLimit-Reset      — Unix timestamp when bucket refills

On 429 the response body is JSON::

    {"error_code": "AEGIS-SEC-0081", "retry_after_s": 12}

Usage::

    app.add_middleware(
        RateLimitMiddleware,
        default_rpm=60,
        burst=10,
    )

    # Per-route override via request state:
    request.state.rate_limit_rpm = 5   # stricter limit for expensive route
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from aegis.security.config import SecurityConfig, get_security_config

_log = structlog.get_logger(__name__)

# Lua script: atomically check + deduct a token from the bucket.
# Returns [tokens_remaining, ttl_ms] where tokens_remaining == -1 means denied.
_LUA_BUCKET_SCRIPT = """
local key       = KEYS[1]
local capacity  = tonumber(ARGV[1])  -- max tokens (burst)
local rate      = tonumber(ARGV[2])  -- tokens per second (rpm / 60)
local now       = tonumber(ARGV[3])  -- current unix milliseconds
local cost      = tonumber(ARGV[4])  -- tokens to consume (usually 1)

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens      = tonumber(bucket[1]) or capacity
local last_refill = tonumber(bucket[2]) or now

-- Refill tokens based on elapsed time
local elapsed = math.max(0, now - last_refill)
local refill  = math.floor(elapsed * rate / 1000)
tokens = math.min(capacity, tokens + refill)
if refill > 0 then
    last_refill = now
end

if tokens >= cost then
    tokens = tokens - cost
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', last_refill)
    -- TTL: keep the key for (capacity / rate) seconds + 10s buffer
    local ttl = math.ceil(capacity / rate) + 10
    redis.call('EXPIRE', key, ttl)
    -- Return [remaining, ttl_ms_until_full_refill]
    local needed = capacity - tokens
    local ms_to_full = math.ceil(needed / rate * 1000)
    return {tokens, ms_to_full}
else
    -- Denied: return remaining=-1 and ms until 1 token is available
    local ms_to_one = math.ceil((cost - tokens) / rate * 1000)
    return {-1, ms_to_one}
end
"""


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Token-bucket rate limiter backed by Redis.

    Parameters
    ----------
    app:
        ASGI application.
    config:
        Injected config; defaults to global singleton.
    redis_client:
        Async Redis client (aioredis / redis.asyncio).
        If ``None`` the middleware is a no-op (useful in unit tests).
    default_rpm:
        Default requests-per-minute ceiling per IP.
    burst:
        Bucket capacity above the per-second rate.
    skip_paths:
        URL paths that bypass rate limiting (health checks, metrics).
    """

    def __init__(
        self,
        app: object,
        config: SecurityConfig | None = None,
        redis_client: Any | None = None,  # noqa: ANN401
        *,
        default_rpm: int | None = None,
        burst: int | None = None,
        skip_paths: set[str] | None = None,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._cfg = config or get_security_config()
        self._redis = redis_client
        self._rpm = default_rpm or self._cfg.rate_limit_default_rpm
        self._burst = burst or self._cfg.rate_limit_burst
        self._skip = skip_paths or {"/healthz", "/readyz", "/metrics"}
        self._script_sha: str | None = None  # Cached EVALSHA

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not self._cfg.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path
        if path in self._skip:
            return await call_next(request)

        if self._redis is None:
            return await call_next(request)

        # Determine effective RPM (allow per-route override)
        rpm = getattr(request.state, "rate_limit_rpm", self._rpm)
        burst = getattr(request.state, "rate_limit_burst", self._burst)

        client_ip = self._extract_ip(request)
        key = f"{self._cfg.rate_limit_redis_prefix}{client_ip}"

        tokens_remaining, ms_to_reset = await self._consume_token(
            key, capacity=burst, rpm=rpm
        )

        # Attach rate-limit info to request state for upstream handlers
        request.state.rate_limit_remaining = max(tokens_remaining, 0)
        request.state.rate_limit_reset = int(time.time() + ms_to_reset / 1000)

        if tokens_remaining < 0:
            retry_after = max(1, int(ms_to_reset / 1000))
            _log.warning(
                "ratelimit.denied",
                ip=client_ip,
                path=path,
                retry_after_s=retry_after,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error_code": "AEGIS-SEC-0081",
                    "message": "Rate limit exceeded",
                    "retry_after_s": retry_after,
                    "docs": "https://aegis.internal/docs/errors/AEGIS-SEC-0081",
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(rpm),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time() + ms_to_reset / 1000)),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(rpm)
        response.headers["X-RateLimit-Remaining"] = str(tokens_remaining)
        response.headers["X-RateLimit-Reset"] = str(
            int(time.time() + ms_to_reset / 1000)
        )
        return response

    async def _consume_token(
        self,
        key: str,
        *,
        capacity: int,
        rpm: int,
    ) -> tuple[int, int]:
        """Execute the Lua token-bucket script against Redis.

        Returns
        -------
        tuple[int, int]
            ``(tokens_remaining, ms_until_refill)``
            ``tokens_remaining == -1`` means request is denied.
        """
        try:
            # Cache the script SHA for EVALSHA efficiency
            if self._script_sha is None:
                self._script_sha = await self._redis.script_load(_LUA_BUCKET_SCRIPT)

            rate_per_s = rpm / 60.0  # tokens / second
            now_ms = int(time.time() * 1000)

            result = await self._redis.evalsha(
                self._script_sha,
                1,  # num keys
                key,
                capacity,
                rate_per_s,
                now_ms,
                1,  # cost
            )
            return int(result[0]), int(result[1])
        except Exception as exc:
            # If Redis fails, allow the request (fail open is better than DOS)
            _log.error("ratelimit.redis_error", error=str(exc))
            return capacity, 0

    @staticmethod
    def _extract_ip(request: Request) -> str:
        """Extract the real client IP, respecting reverse-proxy headers."""
        # Trust X-Forwarded-For only if behind Traefik (single hop)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
        if request.client:
            return request.client.host
        return "unknown"
