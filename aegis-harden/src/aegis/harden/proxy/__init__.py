"""
Proxy rotation posture.

Given a playbook and a request counter, return the proxy URL (or `None` for
direct) the scraper should use. The actual proxy POOL is Phase 1's
responsibility; this module is the policy layer.

The policy is *pure*: no I/O, no network, no global state. Pass in the
candidate pool, get back an index. Phase 1 then resolves the index to the
proxy connection details.
"""

from __future__ import annotations

from dataclasses import dataclass

from aegis.harden.schemas import Playbook
from aegis.harden.utils.rng import SeededRng


@dataclass(frozen=True, slots=True)
class ProxyDecision:
    """Decision for a single request."""

    use_proxy: bool
    pool_index: int  # -1 if `use_proxy` is False
    rotate_after: int  # ticks until rotation is forced again


class ProxyPosture:
    """Stateless policy module.

    Usage:
        posture = ProxyPosture(pool_size=8)
        decision = posture.decide(playbook=pb, request_seq=42, rng=rng)
        # caller resolves pool index to actual proxy & uses it
    """

    __slots__ = ("_pool_size",)

    def __init__(self, *, pool_size: int) -> None:
        if pool_size < 0:
            raise ValueError(f"pool_size must be >= 0, got {pool_size}")
        self._pool_size = int(pool_size)

    @property
    def pool_size(self) -> int:
        return self._pool_size

    def decide(self, *, playbook: Playbook, request_seq: int, rng: SeededRng) -> ProxyDecision:
        """Pick a proxy for the `request_seq`-th request under `playbook`.

        Rules (deterministic given the RNG seed and the playbook):
          * `minimal` profile: never proxy.
          * `standard`: proxy on every request, rotate every `rotate_every`.
          * `stealth`: proxy on every request, rotate every `rotate_every / 2`
            (more aggressive), at least every 3 requests.
          * `tor`: always proxy via index 0 (the tor circuit), rotated by Phase 1.
        """
        if self._pool_size == 0 or playbook.profile == "minimal":
            return ProxyDecision(use_proxy=False, pool_index=-1, rotate_after=playbook.rotate_every)

        if playbook.profile == "tor":
            return ProxyDecision(use_proxy=True, pool_index=0, rotate_after=playbook.rotate_every)

        rotate_every = playbook.rotate_every
        if playbook.profile == "stealth":
            rotate_every = max(3, rotate_every // 2)

        # Cycle through the pool deterministically; jitter the start with the RNG
        # so independent processes pick different starting positions but each
        # process is reproducible.
        bucket = request_seq // max(rotate_every, 1)
        offset = rng.py.randint(0, max(self._pool_size - 1, 0))
        idx = (bucket + offset) % self._pool_size

        next_rotate = rotate_every - (request_seq % max(rotate_every, 1))
        return ProxyDecision(use_proxy=True, pool_index=idx, rotate_after=next_rotate)


__all__ = ["ProxyDecision", "ProxyPosture"]
