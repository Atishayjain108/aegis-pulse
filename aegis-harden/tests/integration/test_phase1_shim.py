"""Integration test for the Phase 1 shim documented in docs/phase5/.

We import the shim by file path so the integration test exercises the
actual reference code shipped to operators.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from aegis.harden.utils.rng import SeededRng


def _load_shim_module():
    # Load the shim from its docs location so we test the exact code we ship.
    import sys

    here = Path(__file__).resolve()
    shim_path = here.parent.parent.parent / "docs" / "phase5" / "phase1_integration_shim.py"
    spec = importlib.util.spec_from_file_location("harden_shim", shim_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec so @dataclass machinery can find it.
    sys.modules["harden_shim"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def shim_module():
    return _load_shim_module()


@pytest.mark.integration
class TestHardenShim:
    def test_preflight_clean_url(self, shim_module) -> None:
        shim = shim_module.HardenShim(rng=SeededRng(1234), proxy_pool_size=10)
        decision = shim.preflight(
            source="reddit-rss",
            url="https://www.reddit.com/r/MachineLearning/.rss",
            seq=0,
        )
        assert decision.skip is False
        assert decision.playbook.name == "reddit-rss"
        assert decision.fingerprint.tls.fid
        assert decision.delay_ms >= 50
        assert decision.proxy_index >= 0  # standard profile uses proxy

    def test_preflight_blocks_honeypot(self, shim_module) -> None:
        shim = shim_module.HardenShim(rng=SeededRng(1234), proxy_pool_size=10)
        decision = shim.preflight(
            source="hacker-news",
            url="https://news.ycombinator.com/honeypot",
            seq=0,
        )
        assert decision.skip is True
        assert decision.reason.startswith("honeypot:")

    def test_preflight_reproducible(self, shim_module) -> None:
        shim1 = shim_module.HardenShim(rng=SeededRng(7), proxy_pool_size=10)
        shim2 = shim_module.HardenShim(rng=SeededRng(7), proxy_pool_size=10)
        d1 = shim1.preflight(source="reddit-rss", url="https://x.com/foo", seq=3)
        d2 = shim2.preflight(source="reddit-rss", url="https://x.com/foo", seq=3)
        assert d1.fingerprint.tls.fid == d2.fingerprint.tls.fid
        assert d1.proxy_index == d2.proxy_index
        assert d1.delay_ms == d2.delay_ms

    def test_preflight_falls_back_to_default(self, shim_module) -> None:
        """An unknown source slug routes to the `default` playbook."""
        shim = shim_module.HardenShim(rng=SeededRng(1234), proxy_pool_size=10)
        decision = shim.preflight(source="not-a-known-source", url="https://x.com/y", seq=0)
        assert decision.playbook.name == "default"
        assert decision.skip is False

    def test_preflight_with_empty_proxy_pool(self, shim_module) -> None:
        """Empty proxy pool: shim reports `proxy_index = -1` for any profile."""
        shim = shim_module.HardenShim(rng=SeededRng(1234), proxy_pool_size=0)
        decision = shim.preflight(source="reddit-rss", url="https://x.com/y", seq=0)
        assert decision.proxy_index == -1

    def test_preflight_uses_playbook_delay(self, shim_module) -> None:
        """The returned `delay_ms` comes from the matched playbook."""
        shim = shim_module.HardenShim(rng=SeededRng(1234), proxy_pool_size=4)
        d_reddit = shim.preflight(source="reddit-rss", url="https://x/y", seq=0)
        d_amazon = shim.preflight(source="amazon", url="https://x/y", seq=0)
        # reddit-rss is 600ms base, amazon is 2000ms base.
        assert d_reddit.delay_ms < d_amazon.delay_ms
