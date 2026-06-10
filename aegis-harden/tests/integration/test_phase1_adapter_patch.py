"""
Integration test for the Phase 1 adapter patch.

We load `integration-patches/03-phase1-adapter/AFTER.py` directly and verify
it works against:
  * a real `HardenShim` constructed with the real Phase 5 modules,
  * a mock httpx transport (so no network),
  * a stub `aegis.scrape.harden_shim` module mirroring the import path that
    operators will use after applying the patch.

This is the same testing discipline applied to `test_phase1_shim.py` and
`test_phase4_intake_patch.py` — every integration artefact gets a test that
proves it actually links up.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from aegis.harden.utils.rng import SeededRng


def _install_stub_module(name: str, attrs: dict) -> ModuleType:
    """Install a synthetic module so AFTER.py's `from aegis.scrape...` works."""
    mod = ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _load_after_module():
    """Load AFTER.py with stubbed import paths.

    AFTER.py imports `from aegis.scrape.harden_shim import HardenShim,
    PreflightDecision`. In the real repo that module is created by
    integration-patch 01. Here we stub it to point at the in-tree shim
    under docs/phase5/, which is the same file content.
    """
    here = Path(__file__).resolve()
    # here -> .../aegis-phase5/aegis-phase5/tests/integration/<file>.py
    # workspace member root (where docs/phase5/ lives):
    workspace_root = here.parent.parent.parent
    # repo root (where integration-patches/ lives):
    repo_root = workspace_root.parent
    shim_src = workspace_root / "docs" / "phase5" / "phase1_integration_shim.py"
    after_src = (
        repo_root / "integration-patches" / "03-phase1-adapter" / "AFTER.py"
    )
    assert shim_src.exists(), f"shim not found: {shim_src}"
    assert after_src.exists(), f"AFTER.py not found: {after_src}"

    # 1. Load the shim itself.
    spec = importlib.util.spec_from_file_location("harden_shim_real", shim_src)
    assert spec and spec.loader
    shim_mod = importlib.util.module_from_spec(spec)
    sys.modules["harden_shim_real"] = shim_mod
    spec.loader.exec_module(shim_mod)

    # 2. Build the synthetic `aegis.scrape.harden_shim` import path that
    #    AFTER.py expects. Create the parent packages first.
    if "aegis.scrape" not in sys.modules:
        _install_stub_module("aegis.scrape", {})
    _install_stub_module(
        "aegis.scrape.harden_shim",
        {
            "HardenShim": shim_mod.HardenShim,
            "PreflightDecision": shim_mod.PreflightDecision,
        },
    )

    # 3. Load AFTER.py itself.
    spec = importlib.util.spec_from_file_location("reddit_rss_after", after_src)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reddit_rss_after"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def adapter_mod():
    return _load_after_module()


@pytest.fixture
def mock_transport():
    """A mock httpx transport that returns a fixed RSS body."""
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        '<title>r/MachineLearning</title>'
        '<entry><title>Test entry</title></entry>'
        "</feed>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        # Capture the request so tests can inspect it.
        handler.last_request = request  # type: ignore[attr-defined]
        return httpx.Response(200, content=body, headers={"content-type": "application/xml"})

    handler.last_request = None  # type: ignore[attr-defined]
    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRedditRssAdapter:
    async def test_clean_fetch_succeeds(self, adapter_mod, mock_transport) -> None:
        shim = adapter_mod.HardenShim(rng=SeededRng(seed=42), proxy_pool_size=4)
        a = adapter_mod.RedditRssAdapter(harden_shim=shim)
        # Swap in the mock transport.
        await a._client.aclose()
        a._client = httpx.AsyncClient(transport=mock_transport, http2=False)
        try:
            result = await a.fetch("MachineLearning", seq=0)
            assert result.skipped is False
            assert result.status == 200
            assert result.content_hash
            assert "Test entry" in result.body
        finally:
            await a.close()

    async def test_fetch_uses_fingerprint_ua(
        self, adapter_mod, mock_transport
    ) -> None:
        """The User-Agent on the wire must come from the chosen fingerprint,
        not a hardcoded string. This proves the patch's UA-rotation works."""
        shim = adapter_mod.HardenShim(rng=SeededRng(seed=42), proxy_pool_size=4)
        a = adapter_mod.RedditRssAdapter(harden_shim=shim)
        await a._client.aclose()
        a._client = httpx.AsyncClient(transport=mock_transport, http2=False)
        try:
            await a.fetch("MachineLearning", seq=0)
            ua = mock_transport.handler.last_request.headers.get("user-agent", "")
            # All fingerprints in the built-in pool ship a Mozilla-prefixed UA.
            assert ua.startswith("Mozilla/5.0")
            # And it is NOT the hardcoded baseline UA from BEFORE.py.
            assert "aegis-pulse/0.4" not in ua
        finally:
            await a.close()

    async def test_different_seq_can_change_fingerprint(
        self, adapter_mod, mock_transport
    ) -> None:
        """Across several requests, the UA rotates (the shim advances the
        RNG between calls). This is a high-level smoke check that fingerprint
        diversity is wired in, not just available."""
        shim = adapter_mod.HardenShim(rng=SeededRng(seed=42), proxy_pool_size=4)
        a = adapter_mod.RedditRssAdapter(harden_shim=shim)
        await a._client.aclose()
        a._client = httpx.AsyncClient(transport=mock_transport, http2=False)
        try:
            seen_uas = set()
            for seq in range(15):
                await a.fetch("MachineLearning", seq=seq)
                seen_uas.add(mock_transport.handler.last_request.headers["user-agent"])
            # 15 picks should produce at least 2 distinct UAs from the 8-entry pool.
            assert len(seen_uas) >= 2
        finally:
            await a.close()

    async def test_honeypot_url_is_skipped(self, adapter_mod, mock_transport) -> None:
        """A URL the honeypot detector blocks must NOT hit the network.

        We craft a subreddit name that yields a URL containing `/honeypot`,
        which is one of the bundled trap tokens.
        """
        shim = adapter_mod.HardenShim(rng=SeededRng(seed=42), proxy_pool_size=4)
        a = adapter_mod.RedditRssAdapter(harden_shim=shim)
        await a._client.aclose()
        a._client = httpx.AsyncClient(transport=mock_transport, http2=False)
        try:
            # The URL becomes "https://www.reddit.com/r/honeypot/.rss"
            # — contains "/honeypot" which is in _URL_TRAP_TOKENS.
            result = await a.fetch("honeypot", seq=0)
            assert result.skipped is True
            assert result.skip_reason.startswith("honeypot:")
            # Critical: no network call was made.
            assert mock_transport.handler.last_request is None
        finally:
            await a.close()

    async def test_factory_constructs_valid_adapter(self, adapter_mod) -> None:
        """The `make_default_adapter` factory should produce a working adapter
        without requiring the caller to know about HardenShim/SeededRng."""
        a = adapter_mod.make_default_adapter(rng_seed=7, proxy_pool_size=2)
        try:
            assert isinstance(a, adapter_mod.RedditRssAdapter)
            assert a._shim is not None
        finally:
            await a.close()

    async def test_skipped_result_does_not_raise(
        self, adapter_mod, mock_transport
    ) -> None:
        """The whole point of returning `skipped=True` instead of raising is
        that callers can keep going. Verify the method returns cleanly."""
        shim = adapter_mod.HardenShim(rng=SeededRng(seed=42), proxy_pool_size=4)
        a = adapter_mod.RedditRssAdapter(harden_shim=shim)
        await a._client.aclose()
        a._client = httpx.AsyncClient(transport=mock_transport, http2=False)
        try:
            result = await a.fetch("honeypot", seq=0)
            assert result.skipped is True
            # Body and content_hash are sentinel-empty on a skip.
            assert result.body == ""
            assert result.content_hash == ""
            assert result.status == 0
        finally:
            await a.close()
