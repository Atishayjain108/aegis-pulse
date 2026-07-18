"""Tests for `aegis.harden.proxy`."""

from __future__ import annotations

import pytest

from aegis.harden.proxy import ProxyPosture
from aegis.harden.schemas import Playbook, PlaybookMatch
from aegis.harden.utils.rng import SeededRng


def _pb(profile: str, *, rotate_every: int = 10) -> Playbook:
    return Playbook(
        name="t",
        version=1,
        match=PlaybookMatch(source="reddit-rss"),
        delay_ms=500,
        rate_per_min=60,
        retries=3,
        profile=profile,  # type: ignore[arg-type]
        rotate_every=rotate_every,
    )


class TestProxyPosture:
    def test_invalid_pool_size(self) -> None:
        with pytest.raises(ValueError):
            ProxyPosture(pool_size=-1)

    def test_minimal_profile_no_proxy(self, rng: SeededRng) -> None:
        p = ProxyPosture(pool_size=8)
        d = p.decide(playbook=_pb("minimal"), request_seq=5, rng=rng)
        assert d.use_proxy is False
        assert d.pool_index == -1

    def test_empty_pool_no_proxy(self, rng: SeededRng) -> None:
        p = ProxyPosture(pool_size=0)
        d = p.decide(playbook=_pb("standard"), request_seq=5, rng=rng)
        assert d.use_proxy is False

    def test_standard_profile_proxies(self, rng: SeededRng) -> None:
        p = ProxyPosture(pool_size=8)
        d = p.decide(playbook=_pb("standard"), request_seq=0, rng=rng)
        assert d.use_proxy is True
        assert 0 <= d.pool_index < 8

    def test_tor_picks_zero(self, rng: SeededRng) -> None:
        p = ProxyPosture(pool_size=8)
        d = p.decide(playbook=_pb("tor"), request_seq=10, rng=rng)
        assert d.use_proxy is True
        assert d.pool_index == 0

    def test_stealth_rotates_aggressively(self, rng: SeededRng) -> None:
        p = ProxyPosture(pool_size=8)
        pb = _pb("stealth", rotate_every=10)
        # Stealth halves rotate_every (10 → 5). So bucket changes every 5 requests.
        d0 = p.decide(playbook=pb, request_seq=0, rng=SeededRng(1))
        d5 = p.decide(playbook=pb, request_seq=5, rng=SeededRng(1))
        # Indexes should differ across rotations with same seed
        assert d0.pool_index != d5.pool_index

    def test_decision_reproducible(self) -> None:
        p = ProxyPosture(pool_size=8)
        pb = _pb("standard")
        d1 = p.decide(playbook=pb, request_seq=42, rng=SeededRng(seed=7))
        d2 = p.decide(playbook=pb, request_seq=42, rng=SeededRng(seed=7))
        assert d1.pool_index == d2.pool_index

    def test_rotate_after_within_rotate_every(self, rng: SeededRng) -> None:
        p = ProxyPosture(pool_size=8)
        pb = _pb("standard", rotate_every=10)
        d = p.decide(playbook=pb, request_seq=3, rng=rng)
        assert 1 <= d.rotate_after <= 10
