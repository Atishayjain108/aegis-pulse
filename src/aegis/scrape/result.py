"""
Typed contracts for scraper adapter outputs and agent health.
All adapters must return List[AdapterResult] via the to_signal_dict() method.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class AdapterStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"       # returned signals but fewer than expected
    EMPTY = "empty"           # 0 signals, no error (legitimately empty)
    HTTP_ERROR = "http_error"
    PARSE_ERROR = "parse_error"
    RATE_LIMITED = "rate_limited"
    BLOCKED = "blocked"       # 403 / Cloudflare wall
    TIMEOUT = "timeout"
    UNKNOWN_ERROR = "unknown_error"


@dataclass
class AdapterRun:
    """
    Rich result wrapper returned by every adapter call via SwarmAgentPool.
    Adapters themselves still return List[dict] — this wraps that at the pool level.
    """
    agent_name: str
    platform: str
    signals: list[dict[str, Any]]
    status: AdapterStatus
    latency_ms: float
    error_msg: str | None = None
    http_status_code: int | None = None

    @property
    def success(self) -> bool:
        return self.status in (AdapterStatus.SUCCESS, AdapterStatus.PARTIAL)

    @property
    def signal_count(self) -> int:
        return len(self.signals)


class AgentHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"   # 1-2 consecutive failures
    DOWN = "DOWN"            # 3+ consecutive failures of same error type
    COOLING = "COOLING"      # temporary backoff window active
    UNKNOWN = "UNKNOWN"      # never run


@dataclass
class AdapterCapabilities:
    """
    Registry entry for each adapter — enables intelligent scheduling.
    """
    platform: str
    tier: str                        # one of SourceTier enum values e.g. "T1_intent"
    supports_html: bool = False
    supports_json: bool = True
    supports_rss: bool = False
    anti_bot_risk: str = "low"       # low | medium | high
    expected_latency_ms: int = 2000
    rate_limit_per_minute: int = 10
    cache_ttl_s: int = 300
    requires_flaresolverr: bool = False
    geo_restricted: bool = False     # True if endpoint is India-only or region-gated
    currency: str = "USD"


__all__ = ["AdapterCapabilities", "AdapterRun", "AdapterStatus", "AgentHealth"]
