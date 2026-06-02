"""Unit tests for src/aegis/scrape/harden_shim.py.

Covers both paths:
  * aegis.harden available (full hardening)
  * aegis.harden unavailable / disabled (pass-through)

No network calls; all harden modules are pure and synchronous.
"""

from __future__ import annotations

import pytest

from aegis.scrape.harden_shim import HARDEN_AVAILABLE, HardenShim, PreflightDecision, make_shim

# ---------------------------------------------------------------------------
# PreflightDecision struct
# ---------------------------------------------------------------------------


class TestPreflightDecision:
    def test_is_frozen(self) -> None:
        d = PreflightDecision(
            skip=False,
            reason="",
            fingerprint=None,
            proxy_index=-1,
            delay_ms=0,
            playbook_name="default",
        )
        with pytest.raises((AttributeError, TypeError)):
            d.skip = True  # type: ignore[misc]

    def test_pass_through_fields(self) -> None:
        d = PreflightDecision(
            skip=False,
            reason="",
            fingerprint=None,
            proxy_index=-1,
            delay_ms=0,
            playbook_name="default",
        )
        assert d.skip is False
        assert d.fingerprint is None
        assert d.proxy_index == -1
        assert d.delay_ms == 0


# ---------------------------------------------------------------------------
# HardenShim — disabled path (no aegis.harden or AEGIS_ENABLE_HARDEN=false)
# ---------------------------------------------------------------------------


class TestHardenShimDisabled:
    """When aegis.harden is not installed the shim degrades to pass-through."""

    def _no_harden_shim(self) -> HardenShim:
        shim = object.__new__(HardenShim)
        shim._available = False  # type: ignore[attr-defined]
        return shim

    def test_available_property_false(self) -> None:
        shim = self._no_harden_shim()
        assert shim.available is False

    def test_preflight_not_skip(self) -> None:
        shim = self._no_harden_shim()
        d = shim.preflight(source="reddit-rss", url="https://www.reddit.com/r/test/.rss", seq=0)
        assert d.skip is False

    def test_preflight_fingerprint_is_none(self) -> None:
        shim = self._no_harden_shim()
        d = shim.preflight(source="reddit-rss", url="https://www.reddit.com", seq=0)
        assert d.fingerprint is None

    def test_preflight_is_idempotent(self) -> None:
        shim = self._no_harden_shim()
        results = [
            shim.preflight(source="reddit-rss", url="https://www.reddit.com", seq=i)
            for i in range(5)
        ]
        # All pass-through — same sentinel returned every time
        assert all(not d.skip for d in results)
        assert all(d.fingerprint is None for d in results)

    def test_honeypot_url_not_blocked_when_disabled(self) -> None:
        shim = self._no_harden_shim()
        d = shim.preflight(
            source="reddit-rss",
            url="https://example.com/honeypot/donotvisit",
            seq=0,
        )
        # Without harden, honeypot detection is off — pass-through
        assert d.skip is False


# ---------------------------------------------------------------------------
# HardenShim — available path (skipped when aegis.harden not installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HARDEN_AVAILABLE, reason="aegis.harden not installed")
class TestHardenShimAvailable:
    def test_available_flag(self) -> None:
        shim = HardenShim()
        assert shim.available is True

    def test_preflight_returns_decision(self) -> None:
        shim = HardenShim(rng_seed=1234)
        d = shim.preflight(source="reddit-rss", url="https://www.reddit.com/r/test/.rss", seq=0)
        assert isinstance(d, PreflightDecision)
        assert d.skip is False
        assert d.fingerprint is not None
        assert d.playbook_name == "reddit-rss"

    def test_fingerprint_has_ua(self) -> None:
        shim = HardenShim(rng_seed=42)
        d = shim.preflight(source="hacker-news", url="https://hn.algolia.com/api/v1/search", seq=0)
        assert d.fingerprint is not None
        ua: str = d.fingerprint.tls.ua  # type: ignore[union-attr]
        assert len(ua) > 10

    def test_fingerprint_rotates_with_seq(self) -> None:
        shim = HardenShim(rng_seed=7)
        fids: set[str] = set()
        for i in range(20):
            d = shim.preflight(source="reddit-rss", url="https://example.com", seq=i)
            if d.fingerprint is not None:
                fid: str = d.fingerprint.tls.fid  # type: ignore[union-attr]
                fids.add(fid)
        assert len(fids) >= 2, "fingerprint must rotate across requests"

    def test_same_seed_same_sequence(self) -> None:
        shim_a = HardenShim(rng_seed=999)
        shim_b = HardenShim(rng_seed=999)
        for seq in range(5):
            da = shim_a.preflight(source="reddit-rss", url="https://example.com", seq=seq)
            db = shim_b.preflight(source="reddit-rss", url="https://example.com", seq=seq)
            assert da.fingerprint is not None
            assert db.fingerprint is not None
            fid_a: str = da.fingerprint.tls.fid  # type: ignore[union-attr]
            fid_b: str = db.fingerprint.tls.fid  # type: ignore[union-attr]
            assert fid_a == fid_b

    def test_no_proxy_when_pool_size_zero(self) -> None:
        shim = HardenShim(rng_seed=1, proxy_pool_size=0)
        d = shim.preflight(source="hacker-news", url="https://hn.algolia.com", seq=0)
        assert d.proxy_index == -1

    def test_proxy_index_bounded_by_pool(self) -> None:
        pool_size = 5
        shim = HardenShim(rng_seed=1, proxy_pool_size=pool_size)
        for seq in range(20):
            d = shim.preflight(source="reddit-rss", url="https://www.reddit.com", seq=seq)
            assert d.proxy_index >= -1

    def test_honeypot_url_skipped(self) -> None:
        shim = HardenShim(rng_seed=1)
        d = shim.preflight(
            source="reddit-rss",
            url="https://example.com/honeypot/donotvisit",
            seq=0,
        )
        assert d.skip is True
        assert "honeypot" in d.reason

    def test_clean_url_not_skipped(self) -> None:
        shim = HardenShim(rng_seed=1)
        d = shim.preflight(source="hacker-news", url="https://news.ycombinator.com/", seq=0)
        assert d.skip is False

    def test_unknown_source_falls_back_to_default(self) -> None:
        shim = HardenShim(rng_seed=1)
        d = shim.preflight(
            source="some-future-adapter",
            url="https://example.com/feed",
            seq=0,
        )
        assert isinstance(d, PreflightDecision)
        assert d.skip is False

    def test_delay_ms_is_non_negative(self) -> None:
        shim = HardenShim(rng_seed=77)
        for seq in range(10):
            d = shim.preflight(source="reddit-rss", url="https://www.reddit.com", seq=seq)
            assert d.delay_ms >= 0


# ---------------------------------------------------------------------------
# make_shim factory
# ---------------------------------------------------------------------------


class TestMakeShim:
    def test_returns_harden_shim(self) -> None:
        shim = make_shim()
        assert isinstance(shim, HardenShim)

    def test_custom_seed(self) -> None:
        shim = make_shim(rng_seed=42)
        assert isinstance(shim, HardenShim)

    def test_proxy_pool_size(self) -> None:
        shim = make_shim(proxy_pool_size=8)
        assert isinstance(shim, HardenShim)


# ---------------------------------------------------------------------------
# reddit_rss adapter wiring (smoke tests)
# ---------------------------------------------------------------------------


class TestRedditRSSAdapterWiring:
    """Verify HardenShim integrates into RedditRSSAdapter without breaking it."""

    def test_adapter_constructs_without_shim(self) -> None:
        from aegis.scrape.sources.reddit_rss import RedditRSSAdapter, RedditRSSConfig

        adapter = RedditRSSAdapter(RedditRSSConfig())
        assert adapter._harden_shim is None  # type: ignore[attr-defined]

    def test_adapter_constructs_with_shim(self) -> None:
        from aegis.scrape.sources.reddit_rss import RedditRSSAdapter, RedditRSSConfig

        shim = make_shim(rng_seed=42)
        adapter = RedditRSSAdapter(RedditRSSConfig(), harden_shim=shim)
        assert adapter._harden_shim is shim  # type: ignore[attr-defined]

    def test_harden_seq_initializes_to_zero(self) -> None:
        from aegis.scrape.sources.reddit_rss import RedditRSSAdapter, RedditRSSConfig

        adapter = RedditRSSAdapter(RedditRSSConfig(), harden_shim=make_shim())
        assert adapter._harden_seq == 0  # type: ignore[attr-defined]
