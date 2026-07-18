"""Optional live trademark lookup clients (USPTO / EUIPO / WIPO free endpoints).

These are *enrichment only*. Every method has a timeout, decorrelated-jitter
retry, and a per-client circuit breaker; on any failure the caller falls back to
the deterministic local screen. ``httpx`` is imported lazily so the package
loads without it.

The endpoints are referenced by their public free-API base URLs. Network calls
are never made by the deterministic core or by the test suite.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field

from aegis.comply.constants import (
    LIVE_LOOKUP_CIRCUIT_RECOVERY_S,
    LIVE_LOOKUP_CIRCUIT_THRESHOLD,
    LIVE_LOOKUP_RETRY_BASE_S,
    LIVE_LOOKUP_RETRY_MAX_S,
    LIVE_LOOKUP_TIMEOUT_S,
)
from aegis.comply.errors import TrademarkCircuitOpenError, TrademarkLookupError
from aegis.comply.logging import get_logger
from aegis.comply.schemas import Jurisdiction, TrademarkMatch

_log = get_logger("aegis.comply.trademark.clients")


@dataclass
class _Circuit:
    """Minimal per-client circuit breaker (mirrors the Phase 11 pattern)."""

    failures: int = 0
    opened_at: float = 0.0
    threshold: int = LIVE_LOOKUP_CIRCUIT_THRESHOLD
    recovery_s: float = LIVE_LOOKUP_CIRCUIT_RECOVERY_S
    _clock: object = field(default=time.monotonic)

    def _now(self) -> float:
        return float(self._clock())  # type: ignore[operator]

    @property
    def is_open(self) -> bool:
        if self.failures < self.threshold:
            return False
        if (self._now() - self.opened_at) >= self.recovery_s:
            self.failures = 0  # half-open -> reset on probe
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self._now()


class TrademarkClient:
    """Async client for one trademark registry endpoint.

    This is intentionally generic: ``query`` issues a single GET against the
    registry's free search endpoint and maps results into ``TrademarkMatch``.
    Concrete response parsing per registry is left to ``_parse`` overrides.
    """

    base_url: str = ""
    source: str = "live"
    jurisdiction: Jurisdiction = Jurisdiction.GLOBAL

    def __init__(self, *, timeout_s: float = LIVE_LOOKUP_TIMEOUT_S) -> None:
        self._timeout = timeout_s
        self._circuit = _Circuit()

    async def query(self, term: str) -> list[TrademarkMatch]:
        """Look up ``term`` with retry + circuit breaker; raise on hard failure."""
        if self._circuit.is_open:
            raise TrademarkCircuitOpenError(f"{self.source} circuit open")
        try:
            import httpx  # lazy import
        except Exception as exc:
            raise TrademarkLookupError("httpx not installed for live lookup") from exc

        delay = LIVE_LOOKUP_RETRY_BASE_S
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(self.base_url, params={"q": term})
                    resp.raise_for_status()
                    self._circuit.record_success()
                    return self._parse(term, resp.json())
            except Exception as exc:
                last_exc = exc
                _log.warning(
                    "trademark.lookup_failed",
                    source=self.source,
                    attempt=attempt,
                    error=str(exc),
                )
                await asyncio.sleep(min(delay, LIVE_LOOKUP_RETRY_MAX_S))
                delay = min(delay * 2 + random.random(), LIVE_LOOKUP_RETRY_MAX_S)
        self._circuit.record_failure()
        raise TrademarkLookupError(f"{self.source} lookup failed: {last_exc}")

    def _parse(self, term: str, payload: object) -> list[TrademarkMatch]:  # pragma: no cover
        """Map a raw registry payload to matches. Override per registry."""
        return []


class USPTOClient(TrademarkClient):
    """USPTO TSDR / trademark search (free, no key)."""

    base_url = "https://tmsearch.uspto.gov/api/v1/search"
    source = "uspto"
    jurisdiction = Jurisdiction.US


class EUIPOClient(TrademarkClient):
    """EUIPO TMview (free)."""

    base_url = "https://www.tmdn.org/tmview/api/search"
    source = "euipo"
    jurisdiction = Jurisdiction.EU


class WIPOClient(TrademarkClient):
    """WIPO Global Brand Database (free)."""

    base_url = "https://branddb.wipo.int/branddb/api/search"
    source = "wipo"
    jurisdiction = Jurisdiction.GLOBAL
