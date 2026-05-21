"""
SwarmAgentPool — wraps each adapter as an autonomous health-tracked mini-agent.

Key design decisions:
- Health uses rolling averages, NOT naive "3 failures = DOWN"
- EMPTY (0 signals) is NOT a failure — some endpoints legitimately return nothing
- Error TYPE matters: BLOCKED and RATE_LIMITED trigger cooling, not DOWN
- Health state persisted to Redis; restored on startup
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from aegis.scrape.governor import ConcurrencyGovernor
from aegis.scrape.result import AdapterCapabilities, AdapterRun, AdapterStatus, AgentHealth

_log = structlog.get_logger("aegis.scrape.swarm_agents")

# Cooldown durations per error type (seconds)
COOLDOWN_BY_ERROR: dict[AdapterStatus, int] = {
    AdapterStatus.RATE_LIMITED: 300,   # 5 min
    AdapterStatus.BLOCKED: 600,        # 10 min
    AdapterStatus.TIMEOUT: 60,         # 1 min
    AdapterStatus.HTTP_ERROR: 120,     # 2 min
    AdapterStatus.PARSE_ERROR: 30,     # 30 sec — may just be transient HTML change
    AdapterStatus.UNKNOWN_ERROR: 60,
}

# Number of consecutive same-type failures before marking DOWN
DOWN_THRESHOLD_BY_ERROR: dict[AdapterStatus, int] = {
    AdapterStatus.BLOCKED: 2,          # blocked means genuinely blocked
    AdapterStatus.RATE_LIMITED: 3,
    AdapterStatus.HTTP_ERROR: 3,
    AdapterStatus.PARSE_ERROR: 5,      # parse errors need more evidence
    AdapterStatus.TIMEOUT: 3,
    AdapterStatus.UNKNOWN_ERROR: 3,
}


@dataclass
class ScraperAgent:
    name: str
    platform: str
    adapter_fn: Callable[..., Any]
    capabilities: AdapterCapabilities
    weight: float = 1.0
    health: AgentHealth = AgentHealth.UNKNOWN
    last_run_at: datetime | None = None
    last_signal_count: int = 0
    consecutive_failures: int = 0
    last_error_type: AdapterStatus | None = None
    avg_latency_ms: float = 0.0
    _latency_history: list[float] = field(default_factory=list, repr=False)
    _cooling_until: float = field(default=0.0, repr=False)

    @property
    def tier(self) -> str:
        return self.capabilities.tier

    @property
    def is_cooling(self) -> bool:
        return time.monotonic() < self._cooling_until

    def record_success(self, signal_count: int, latency_ms: float) -> None:
        self._latency_history = [*self._latency_history, latency_ms][-20:]
        self.avg_latency_ms = sum(self._latency_history) / len(self._latency_history)
        self.last_signal_count = signal_count
        self.last_run_at = datetime.now(UTC)
        self.consecutive_failures = 0
        self.last_error_type = None
        self.health = AgentHealth.HEALTHY

    def record_failure(self, error_type: AdapterStatus, latency_ms: float) -> None:
        # EMPTY is not a failure
        if error_type == AdapterStatus.EMPTY:
            self.last_signal_count = 0
            self.last_run_at = datetime.now(UTC)
            return

        self._latency_history = [*self._latency_history, latency_ms][-20:]
        self.avg_latency_ms = sum(self._latency_history) / len(self._latency_history)
        self.last_run_at = datetime.now(UTC)

        # Only count as failure if same error type persists
        if self.last_error_type == error_type:
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = 1
        self.last_error_type = error_type

        threshold = DOWN_THRESHOLD_BY_ERROR.get(error_type, 3)
        if self.consecutive_failures >= threshold:
            self.health = AgentHealth.DOWN
            cooldown = COOLDOWN_BY_ERROR.get(error_type, 60)
            self._cooling_until = time.monotonic() + cooldown
            _log.warning(
                "agent_marked_down",
                agent=self.name,
                error_type=error_type,
                consecutive=self.consecutive_failures,
                cooldown_s=cooldown,
            )
        elif self.consecutive_failures >= 1:
            self.health = AgentHealth.DEGRADED

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "platform": self.platform,
            "tier": self.tier,
            "health": self.health.value,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_signal_count": self.last_signal_count,
            "consecutive_failures": self.consecutive_failures,
            "last_error_type": self.last_error_type.value if self.last_error_type else None,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "is_cooling": self.is_cooling,
        }


class SwarmAgentPool:
    REDIS_HEALTH_KEY = "aegis:swarm:agent_health"

    def __init__(
        self,
        agents: list[ScraperAgent],
        governor: ConcurrencyGovernor,
        redis_client: Any = None,
    ) -> None:
        self.agents = {a.name: a for a in agents}
        self.governor = governor
        self._redis = redis_client

    async def restore_health_from_redis(self) -> None:
        """Restore agent health state on startup."""
        if not self._redis:
            return
        try:
            data = await self._redis.hgetall(self.REDIS_HEALTH_KEY)
            for agent_name, raw in data.items():
                if agent_name in self.agents:
                    state = json.loads(raw)
                    agent = self.agents[agent_name]
                    agent.health = AgentHealth(state.get("health", "UNKNOWN"))
                    agent.consecutive_failures = state.get("consecutive_failures", 0)
                    agent.avg_latency_ms = state.get("avg_latency_ms", 0.0)
                    agent.last_signal_count = state.get("last_signal_count", 0)
        except Exception as e:
            _log.warning("health_restore_failed", error=str(e))

    async def _persist_health(self, agent: ScraperAgent) -> None:
        if not self._redis:
            return
        try:
            await self._redis.hset(
                self.REDIS_HEALTH_KEY,
                agent.name,
                json.dumps(agent.to_dict()),
            )
            await self._redis.expire(self.REDIS_HEALTH_KEY, 7 * 24 * 3600)
        except Exception as e:
            _log.warning("health_persist_failed", agent=agent.name, error=str(e))

    async def run_agent(
        self,
        agent: ScraperAgent,
        settings: Any,
        http: Any,
        limit: int,
    ) -> AdapterRun:
        if agent.is_cooling:
            _log.info("agent_cooling_skipped", agent=agent.name)
            return AdapterRun(
                agent_name=agent.name,
                platform=agent.platform,
                signals=[],
                status=AdapterStatus.UNKNOWN_ERROR,
                latency_ms=0,
                error_msg="cooling_window_active",
            )

        start = time.monotonic()
        try:
            async with self.governor.global_slot():
                await self.governor.jitter()
                _scrape_cfg = getattr(settings, "scrape", settings)
                timeout = getattr(_scrape_cfg, "swarm_wave_timeout_s", 60.0)
                signals = await asyncio.wait_for(
                    agent.adapter_fn(settings, http, limit),
                    timeout=timeout,
                )

            latency_ms = (time.monotonic() - start) * 1000
            status = AdapterStatus.SUCCESS if signals else AdapterStatus.EMPTY
            agent.record_success(len(signals), latency_ms)
            await self._persist_health(agent)

            return AdapterRun(
                agent_name=agent.name,
                platform=agent.platform,
                signals=signals,
                status=status,
                latency_ms=latency_ms,
            )

        except TimeoutError:
            latency_ms = (time.monotonic() - start) * 1000
            agent.record_failure(AdapterStatus.TIMEOUT, latency_ms)
            await self._persist_health(agent)
            return AdapterRun(
                agent_name=agent.name,
                platform=agent.platform,
                signals=[],
                status=AdapterStatus.TIMEOUT,
                latency_ms=latency_ms,
                error_msg="timeout",
            )
        except Exception as e:
            latency_ms = (time.monotonic() - start) * 1000
            err_str = str(e)
            if "403" in err_str or "blocked" in err_str.lower():
                status = AdapterStatus.BLOCKED
            elif "429" in err_str or "rate" in err_str.lower():
                status = AdapterStatus.RATE_LIMITED
            elif "ConnectError" in type(e).__name__ or "TimeoutError" in type(e).__name__:
                status = AdapterStatus.TIMEOUT
            else:
                status = AdapterStatus.UNKNOWN_ERROR
            agent.record_failure(status, latency_ms)
            await self._persist_health(agent)
            _log.error("agent_run_failed", agent=agent.name, error=err_str, status=status)
            return AdapterRun(
                agent_name=agent.name,
                platform=agent.platform,
                signals=[],
                status=status,
                latency_ms=latency_ms,
                error_msg=err_str,
            )

    async def run_wave(
        self,
        agent_names: list[str],
        settings: Any,
        http: Any,
        limit: int,
    ) -> list[AdapterRun]:
        agents = [self.agents[n] for n in agent_names if n in self.agents]
        tasks = [self.run_agent(a, settings, http, limit) for a in agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        runs: list[AdapterRun] = []
        for r in results:
            if isinstance(r, Exception):
                _log.error("wave_gather_exception", error=str(r))
            else:
                runs.append(r)  # type: ignore[arg-type]
        return runs

    def health_report(self) -> dict[str, Any]:
        return {name: agent.to_dict() for name, agent in self.agents.items()}

    def get_healthy_agents(self, tier: str | None = None) -> list[ScraperAgent]:
        return [
            a for a in self.agents.values()
            if a.health in (AgentHealth.HEALTHY, AgentHealth.UNKNOWN, AgentHealth.DEGRADED)
            and not a.is_cooling
            and (tier is None or a.tier == tier)
        ]


__all__ = [
    "COOLDOWN_BY_ERROR",
    "DOWN_THRESHOLD_BY_ERROR",
    "ScraperAgent",
    "SwarmAgentPool",
]
