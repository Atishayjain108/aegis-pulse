"""
Phase 1 integration helper: a thin shim a Phase 1 scrape adapter can use
to call into Phase 5 with one line per concern.

This file is NOT part of Phase 5's runtime — it lives here as documentation
+ a copy-pasteable reference. To use it, drop it into the main package as
`src/aegis/scrape/harden_shim.py` and import from there.

Usage in an existing Phase 1 adapter (illustrative — not a real diff):

    from aegis.scrape.harden_shim import HardenShim
    from aegis.harden.utils import SeededRng

    class RedditRssAdapter:
        def __init__(self) -> None:
            self.shim = HardenShim(
                rng=SeededRng(seed=os.getenv("AEGIS_RNG_SEED", 0xA5615)),
                proxy_pool_size=len(self.proxies),
            )

        async def fetch(self, url: str, *, seq: int) -> Response:
            decision = self.shim.preflight(source="reddit-rss", url=url, seq=seq)
            if decision.skip:
                _log.info("skipped", url=url, reason=decision.reason)
                return Response.empty(reason=decision.reason)

            # Use decision.fingerprint to configure curl-impersonate / Playwright,
            # decision.proxy_index to pick a proxy from the pool,
            # decision.delay_ms as the inter-request sleep.
            return await self._do_fetch(
                url,
                fingerprint=decision.fingerprint,
                proxy=self.proxies[decision.proxy_index] if decision.proxy_index >= 0 else None,
                delay_ms=decision.delay_ms,
            )

The shim is intentionally synchronous — pre-flight is fast (microseconds),
so no async wrapping is needed.
"""

from __future__ import annotations

from dataclasses import dataclass

from aegis.harden.detect import screen_url
from aegis.harden.fingerprint import FingerprintPool
from aegis.harden.playbooks import PlaybookRegistry, builtin_registry
from aegis.harden.proxy import ProxyPosture
from aegis.harden.schemas import FingerprintProfile, Playbook
from aegis.harden.utils.rng import SeededRng


@dataclass(frozen=True, slots=True)
class PreflightDecision:
  """Result of a single pre-flight check. All callers care about."""

  skip: bool
  reason: str
  fingerprint: FingerprintProfile
  playbook: Playbook
  proxy_index: int           # -1 if no proxy
  delay_ms: int              # effective sleep before this request


class HardenShim:
  """Thin Phase 1 ↔ Phase 5 adapter.

  Bundles the four pre-flight checks into one call:
    1. Match a playbook for the source.
    2. Pick a fingerprint.
    3. Decide proxy posture.
    4. Screen the URL for honeypots.

  Reasons for the design:
    * One callsite in the adapter, not four.
    * Centralized seed propagation — every adapter uses the same RNG.
    * Trivial to mock in adapter tests.
  """

  __slots__ = ("_rng", "_pool", "_registry", "_posture")

  def __init__(
    self,
    *,
    rng: SeededRng,
    proxy_pool_size: int,
    pool: FingerprintPool | None = None,
    registry: PlaybookRegistry | None = None,
  ) -> None:
    self._rng = rng
    self._pool = pool or FingerprintPool()
    self._registry = registry or builtin_registry()
    self._posture = ProxyPosture(pool_size=proxy_pool_size)

  def preflight(self, *, source: str, url: str, seq: int) -> PreflightDecision:
    """Run the four pre-flight checks. Returns the consolidated decision.

    The order is intentional: cheap & deterministic first (playbook match,
    fingerprint pick), then URL-pattern honeypot check. The honeypot check
    is last because if any earlier step fails to produce a usable result,
    we'd rather see THAT error than a honeypot block.
    """
    playbook = self._registry.match(source=source, url=url)
    fingerprint = self._pool.pick(self._rng)
    proxy_decision = self._posture.decide(playbook=playbook, request_seq=seq, rng=self._rng)
    delay_ms = playbook.effective_delay_ms(seq)

    honey = screen_url(url, trend_id=None)
    if honey.verdict == "block":
      return PreflightDecision(
        skip=True,
        reason=f"honeypot:{honey.reason_code}",
        fingerprint=fingerprint,
        playbook=playbook,
        proxy_index=proxy_decision.pool_index,
        delay_ms=delay_ms,
      )

    return PreflightDecision(
      skip=False,
      reason=f"warn:{honey.reason_code}" if honey.verdict == "warn" else "ok",
      fingerprint=fingerprint,
      playbook=playbook,
      proxy_index=proxy_decision.pool_index,
      delay_ms=delay_ms,
    )


__all__ = ["HardenShim", "PreflightDecision"]
