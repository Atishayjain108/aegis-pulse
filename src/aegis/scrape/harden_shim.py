"""Phase 1 ↔ Phase 5 (aegis.harden) integration shim.

Single callsite for all scrape adapters to invoke Phase 5 hardening:
  1. Match a per-source playbook (delay, rate, proxy profile).
  2. Pick a TLS + HTTP/2 fingerprint (JA3/JA4 diversity).
  3. Decide proxy posture (index into the caller's pool, or -1 for direct).
  4. Screen the target URL for honeypot patterns.

Graceful degradation: all ``aegis.harden`` imports are guarded. When the
``harden`` extra is not installed or ``AEGIS_ENABLE_HARDEN=false``, every
``HardenShim.preflight()`` call returns a pass-through decision
(``skip=False``, ``fingerprint=None``). Adapters that key on
``decision.fingerprint is not None`` handle both paths transparently.

Construct ONE HardenShim per scrape session, not per request — the internal
RNG state advances across calls so each request gets a distinct fingerprint
slice. Sharing across adapters keeps the fingerprint distribution diverse
and uncorrelated.
"""

from __future__ import annotations

import contextvars
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from aegis.harden.schemas import FingerprintProfile

_log = structlog.get_logger("aegis.scrape.harden_shim")

_ENABLE_HARDEN: bool = os.getenv("AEGIS_ENABLE_HARDEN", "true").lower() not in (
    "0",
    "false",
    "no",
)

HARDEN_AVAILABLE: bool = False

if _ENABLE_HARDEN:
    try:
        from aegis.harden.detect import screen_url as _screen_url
        from aegis.harden.fingerprint import FingerprintPool as _FingerprintPool
        from aegis.harden.playbooks import (
            PlaybookRegistry as _PlaybookRegistry,
        )
        from aegis.harden.playbooks import (
            builtin_registry as _builtin_registry,
        )
        from aegis.harden.proxy import ProxyPosture as _ProxyPosture
        from aegis.harden.utils.rng import SeededRng as _SeededRng

        HARDEN_AVAILABLE = True
    except Exception:  # optional dep; degrade silently
        _log.debug("harden_shim.unavailable", reason="aegis.harden not installed")


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreflightDecision:
    """Consolidated pre-flight result for one request.

    Callers pattern:
      decision = shim.preflight(source="reddit-rss", url=url, seq=seq)
      if decision.skip:
          continue  # honeypot or blocked by policy
      if decision.fingerprint is not None:
          headers["User-Agent"] = decision.fingerprint.tls.ua
    """

    skip: bool
    reason: str
    # None when aegis.harden is not installed or AEGIS_ENABLE_HARDEN=false.
    fingerprint: FingerprintProfile | None
    proxy_index: int  # -1 means direct (no proxy)
    delay_ms: int  # jittered inter-request sleep from playbook
    playbook_name: str


# Singleton returned by all pass-through paths (no harden, url is clean).
_PASS_THROUGH = PreflightDecision(
    skip=False,
    reason="",
    fingerprint=None,
    proxy_index=-1,
    delay_ms=0,
    playbook_name="default",
)


# ---------------------------------------------------------------------------
# Main shim
# ---------------------------------------------------------------------------


class HardenShim:
    """Thin integration adapter between Phase 1 adapters and Phase 5 hardening.

    Thread-safe for read-only use after construction. The internal RNG is NOT
    thread-safe — construct one shim per concurrent scrape worker.
    """

    def __init__(
        self,
        *,
        rng_seed: int | None = None,
        proxy_pool_size: int = 0,
    ) -> None:
        self._available = HARDEN_AVAILABLE
        if not HARDEN_AVAILABLE:
            return

        seed = (
            rng_seed
            if rng_seed is not None
            else int(os.getenv("AEGIS_HARDEN_RNG_SEED", str(0xA5615)))
        )
        self._rng = _SeededRng(seed=seed)
        self._pool = _FingerprintPool()
        self._registry: _PlaybookRegistry = _builtin_registry()
        self._posture = _ProxyPosture(pool_size=proxy_pool_size)

    @property
    def available(self) -> bool:
        """True when aegis.harden is installed and AEGIS_ENABLE_HARDEN is set."""
        return self._available

    def preflight(self, *, source: str, url: str, seq: int) -> PreflightDecision:
        """Run four pre-flight checks and return the consolidated decision.

        Synchronous and CPU-bound (microseconds) — safe to call from any
        async context without an executor.

        Args:
            source: Phase 1 source slug (e.g. ``"reddit-rss"``).
            url:    Full URL about to be fetched.
            seq:    Zero-based request sequence counter for this session.
        """
        if not self._available:
            return _PASS_THROUGH

        # 1. Playbook match
        try:
            playbook = self._registry.match(source=source, url=url)
        except Exception:
            playbook = self._registry.match(url=url)

        # 2. Fingerprint pick
        fingerprint = self._pool.pick(self._rng)

        # 3. Proxy posture
        proxy_decision = self._posture.decide(
            playbook=playbook, request_seq=seq, rng=self._rng
        )

        # 4. URL honeypot screen
        harden_verdict = _screen_url(url)
        if harden_verdict.verdict == "block":
            _log.warning(
                "harden_shim.url_blocked",
                url=url,
                source=source,
                reason=harden_verdict.reason_code,
                score=harden_verdict.score,
            )
            return PreflightDecision(
                skip=True,
                reason=f"honeypot:{harden_verdict.reason_code}",
                fingerprint=fingerprint,
                proxy_index=proxy_decision.pool_index,
                delay_ms=self._rng.jitter_ms(playbook.delay_ms, playbook.jitter_ms),
                playbook_name=playbook.name,
            )

        return PreflightDecision(
            skip=False,
            reason="",
            fingerprint=fingerprint,
            proxy_index=proxy_decision.pool_index,
            delay_ms=self._rng.jitter_ms(playbook.delay_ms, playbook.jitter_ms),
            playbook_name=playbook.name,
        )


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_shim(*, rng_seed: int | None = None, proxy_pool_size: int = 0) -> HardenShim:
    """Construct a ready-to-use HardenShim with sensible defaults.

    For single-adapter callers. Multi-adapter sessions should construct one
    HardenShim explicitly and pass it to every adapter so the RNG sequence
    advances continuously.
    """
    return HardenShim(rng_seed=rng_seed, proxy_pool_size=proxy_pool_size)


# ---------------------------------------------------------------------------
# Session-scoped shim registry (ContextVar-based)
# ---------------------------------------------------------------------------
# Storing a HardenShim in this ContextVar ensures the same RNG sequence
# advances continuously across all adapter tasks within one scrape_topic
# call, so every adapter in the session produces a distinct fingerprint
# slice rather than repeating the same seed-0 sequence independently.

_session_shim_var: contextvars.ContextVar[HardenShim | None] = contextvars.ContextVar(
    "aegis_session_shim", default=None
)


def set_session_shim(shim: HardenShim) -> contextvars.Token:  # type: ignore[type-arg]
    """Bind *shim* as the active session shim for the current async task.

    Returns the ContextVar token so the caller can reset it when done.
    """
    return _session_shim_var.set(shim)


def get_session_shim() -> HardenShim:
    """Return the active session shim, creating a fresh time-seeded one if absent."""
    shim = _session_shim_var.get()
    if shim is None:
        shim = HardenShim(rng_seed=time.time_ns() & 0xFFFF_FFFF)
    return shim


__all__ = [
    "HARDEN_AVAILABLE",
    "HardenShim",
    "PreflightDecision",
    "get_session_shim",
    "make_shim",
    "set_session_shim",
]
