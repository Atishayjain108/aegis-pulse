"""Additional tests targeting coverage gaps in CLI, metrics, enums, and base adapter."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from aegis.schemas.signal import ProductSignal

# ---------------------------------------------------------------------------
# schemas/enums.py — confidence_band and ConfidenceBand
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_confidence_band_very_low():
    from aegis.schemas.enums import ConfidenceBand, confidence_band

    assert confidence_band(0.0) == ConfidenceBand.VERY_LOW
    assert confidence_band(0.1) == ConfidenceBand.VERY_LOW
    assert confidence_band(0.19) == ConfidenceBand.VERY_LOW


@pytest.mark.unit
def test_confidence_band_low():
    from aegis.schemas.enums import ConfidenceBand, confidence_band

    assert confidence_band(0.2) == ConfidenceBand.LOW
    assert confidence_band(0.35) == ConfidenceBand.LOW


@pytest.mark.unit
def test_confidence_band_medium():
    from aegis.schemas.enums import ConfidenceBand, confidence_band

    assert confidence_band(0.4) == ConfidenceBand.MEDIUM
    assert confidence_band(0.55) == ConfidenceBand.MEDIUM


@pytest.mark.unit
def test_confidence_band_high():
    from aegis.schemas.enums import ConfidenceBand, confidence_band

    assert confidence_band(0.6) == ConfidenceBand.HIGH
    assert confidence_band(0.75) == ConfidenceBand.HIGH


@pytest.mark.unit
def test_confidence_band_very_high():
    from aegis.schemas.enums import ConfidenceBand, confidence_band

    assert confidence_band(0.8) == ConfidenceBand.VERY_HIGH
    assert confidence_band(1.0) == ConfidenceBand.VERY_HIGH


@pytest.mark.unit
def test_confidence_band_out_of_range():
    from aegis.schemas.enums import confidence_band

    with pytest.raises(ValueError, match="confidence"):
        confidence_band(-0.1)
    with pytest.raises(ValueError, match="confidence"):
        confidence_band(1.1)


# ---------------------------------------------------------------------------
# core/metrics.py — more metric functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_metrics_db_pool_gauges():
    from aegis.core.metrics import db_pool_in_use, db_pool_size

    db_pool_size.set(5)
    db_pool_in_use.set(2)


@pytest.mark.unit
def test_metrics_db_query_histogram():
    from aegis.core.metrics import db_query_duration_seconds

    db_query_duration_seconds.labels(op="select").observe(0.01)


@pytest.mark.unit
def test_metrics_scrape_requests_total():
    from aegis.core.metrics import scrape_requests_total

    scrape_requests_total.labels(platform="hn", method="http").inc()


@pytest.mark.unit
def test_resilient_call_observe():
    from aegis.core.metrics import resilient_call_observe

    resilient_call_observe(policy="test.op", outcome="success", attempt=1, duration=0.1)
    resilient_call_observe(policy="test.op", outcome="timeout", attempt=2)


# ---------------------------------------------------------------------------
# scrape/base.py — _looks_like_secret + blocking parse + setup failure
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_looks_like_secret_true():
    from aegis.scrape.base import _looks_like_secret

    assert _looks_like_secret("api_key", "abc") is True
    assert _looks_like_secret("client_secret", "xyz") is True
    assert _looks_like_secret("password", "pw") is True
    assert _looks_like_secret("access_token", "tok") is True
    assert _looks_like_secret("auth_header", "h") is True


@pytest.mark.unit
def test_looks_like_secret_false():
    from aegis.scrape.base import _looks_like_secret

    assert _looks_like_secret("query", "q") is False
    assert _looks_like_secret("limit", 10) is False
    assert _looks_like_secret("url", "http://x.com") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_adapter_blocking_parse():
    """Test parse_is_blocking=True offloads to thread."""
    from datetime import datetime

    from aegis.schemas.enums import (
        ContentModality,
        Platform,
        ScrapeMethod,
        SourceTier,
        ToSRisk,
    )
    from aegis.schemas.signal import (
        ConfidenceMetadata,
        ProductSignal,
        ScrapeProvenance,
        compute_content_hash,
    )
    from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter

    NOW = datetime(2024, 1, 15, tzinfo=UTC)

    class _BlockingAdapter(SourceAdapter[str]):
        parse_is_blocking = True

        @property
        def name(self) -> str:
            return "blocking_mock"

        async def fetch_raw(self, ctx: ScrapeContext, **_: Any) -> AsyncIterator[str]:
            yield "item1"

        def parse(self, raw: str, ctx: ScrapeContext) -> ProductSignal | None:
            h = compute_content_hash(
                platform=Platform.HACKER_NEWS,
                external_id=raw,
                url=None,
                title=raw,
                raw_text=None,
                posted_at=None,
            )
            return ProductSignal(
                platform=Platform.HACKER_NEWS,
                tier=SourceTier.TIER_5_ALTERNATIVE,
                external_id=raw,
                title=raw,
                modality=ContentModality.TEXT,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.OFFICIAL_API,
                    scraped_at=NOW,
                    scraper_version="t-1.0",
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(completeness=1.0, source_confidence=1.0),
                content_hash=h,
            )

    adapter = _BlockingAdapter(AdapterConfig(name="blocking_mock"))
    signals = [s async for s in adapter.run()]
    assert len(signals) == 1
    assert signals[0].external_id == "item1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_adapter_setup_failure_propagates():
    from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter

    class _FailSetupAdapter(SourceAdapter[str]):
        @property
        def name(self) -> str:
            return "fail_setup"

        async def setup(self, ctx: ScrapeContext) -> None:
            raise RuntimeError("setup failed intentionally")

        async def fetch_raw(self, ctx: ScrapeContext, **_: Any) -> AsyncIterator[str]:
            yield "x"

        def parse(self, raw: str, ctx: ScrapeContext) -> ProductSignal | None:
            return None

    adapter = _FailSetupAdapter(AdapterConfig(name="fail_setup"))
    with pytest.raises(RuntimeError, match="setup failed"):
        _ = [s async for s in adapter.run()]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_adapter_teardown_failure_does_not_mask():
    from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter

    class _FailTeardownAdapter(SourceAdapter[str]):
        @property
        def name(self) -> str:
            return "fail_teardown"

        async def teardown(self, ctx: ScrapeContext) -> None:
            raise RuntimeError("teardown failed")

        async def fetch_raw(self, ctx: ScrapeContext, **_: Any) -> AsyncIterator[str]:
            yield "a"

        def parse(self, raw: str, ctx: ScrapeContext) -> ProductSignal | None:
            return None

    adapter = _FailTeardownAdapter(AdapterConfig(name="fail_teardown"))
    # Teardown failure should be logged but not propagate
    signals = [s async for s in adapter.run()]
    assert signals == []


# ---------------------------------------------------------------------------
# CLI — _looks_like_secret via cli helpers + _repo_root
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cli_repo_root_type():
    from pathlib import Path

    from aegis.cli.main import _repo_root

    r = _repo_root()
    assert isinstance(r, Path)


@pytest.mark.unit
def test_cli_doctor_command():
    from click.testing import CliRunner

    from aegis.cli.main import main

    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--help"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_cli_support_bundle_help():
    from click.testing import CliRunner

    from aegis.cli.main import main

    runner = CliRunner()
    result = runner.invoke(main, ["support-bundle", "--help"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_cli_tail_help():
    from click.testing import CliRunner

    from aegis.cli.main import main

    runner = CliRunner()
    result = runner.invoke(main, ["tail", "--help"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_cli_status_help():
    from click.testing import CliRunner

    from aegis.cli.main import main

    runner = CliRunner()
    result = runner.invoke(main, ["status", "--help"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_cli_reset_help():
    from click.testing import CliRunner

    from aegis.cli.main import main

    runner = CliRunner()
    result = runner.invoke(main, ["reset", "--help"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_cli_build_adapter_fallback_for_unknown_source():
    """Test that _build_adapter creates a generic adapter for unknown sources."""
    from aegis.cli.main import _build_adapter

    class _GenericAdapter:
        def __init__(self, config: Any):
            self.config = config

    result = _build_adapter("some-new-source", _GenericAdapter, limit=5)
    assert isinstance(result, _GenericAdapter)


# ---------------------------------------------------------------------------
# Source adapters — _int_or_none / _float_or_none utility functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_amazon_int_or_none():
    from aegis.scrape.sources.amazon import _int_or_none

    assert _int_or_none(None) is None
    assert _int_or_none("42") == 42
    assert _int_or_none("1,234") == 1234
    assert _int_or_none("not-a-number") is None


@pytest.mark.unit
def test_tiktok_int_or_none():
    from aegis.scrape.sources.tiktok import _int_or_none

    assert _int_or_none(None) is None
    assert _int_or_none(100) == 100
    assert _int_or_none("500") == 500
    assert _int_or_none("bad") is None


@pytest.mark.unit
def test_youtube_int_or_none():
    from aegis.scrape.sources.youtube import _int_or_none

    assert _int_or_none(None) is None
    assert _int_or_none("1000000") == 1_000_000
    assert _int_or_none("abc") is None


@pytest.mark.unit
def test_pinterest_int_or_none():
    from aegis.scrape.sources.pinterest import _int_or_none

    assert _int_or_none(None) is None
    assert _int_or_none(250) == 250
    assert _int_or_none("oops") is None


@pytest.mark.unit
def test_google_trends_parse_no_date():
    from aegis.scrape.base import ScrapeContext
    from aegis.scrape.sources.google_trends import GoogleTrendsAdapter, GoogleTrendsConfig

    adapter = GoogleTrendsAdapter(GoogleTrendsConfig())
    ctx = ScrapeContext()
    signal = adapter.parse({"keyword": "ai tools", "value": 90, "geo": "US"}, ctx)
    assert signal is not None
    assert signal.posted_at is None
    assert signal.platform.value == "google_trends"


@pytest.mark.unit
def test_hacker_news_adapter_parse_story_url():
    from aegis.scrape.base import ScrapeContext
    from aegis.scrape.sources.hacker_news import HackerNewsAdapter, HackerNewsConfig

    adapter = HackerNewsAdapter(HackerNewsConfig())
    ctx = ScrapeContext()

    raw = {
        "objectID": "40099",
        "story_id": "40099",
        "title": "Launch HN: New startup",
        "url": "https://startup.io",
        "story_title": "Launch HN: New startup",
        "points": 200,
        "num_comments": 50,
    }
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.external_id == "40099"


@pytest.mark.unit
def test_reddit_adapter_url_with_no_score():
    """Reddit parse with zero score → engagement.likes should be 0."""
    from types import SimpleNamespace

    from aegis.scrape.base import ScrapeContext
    from aegis.scrape.sources.reddit import RedditAdapter, RedditConfig

    adapter = RedditAdapter(
        RedditConfig(client_id="x", client_secret="y", user_agent="script:t:1.0 (by /u/testbot)")
    )
    ctx = ScrapeContext()
    sub = SimpleNamespace(
        id="xyz",
        title="Test post",
        selftext="",
        score=-5,
        num_comments=1,
        created_utc=1705312800.0,
        permalink="/r/test/comments/xyz/",
        is_self=True,
        over_18=False,
        stickied=False,
        author=None,
        subreddit=SimpleNamespace(display_name="test"),
    )
    signal = adapter.parse(sub, ctx)
    assert signal is not None
    assert signal.engagement.likes == 0  # clamped to 0


@pytest.mark.unit
def test_pinterest_parse_missing_pin_id_returns_none():
    from aegis.scrape.base import ScrapeContext
    from aegis.scrape.sources.pinterest import PinterestAdapter, PinterestConfig

    adapter = PinterestAdapter(PinterestConfig())
    ctx = ScrapeContext()
    assert adapter.parse({}, ctx) is None
    assert adapter.parse({"id": ""}, ctx) is None


# ---------------------------------------------------------------------------
# Phase 6: runner.py — swarm_context injection success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_swarm_context_injected_on_valid_redis_response() -> None:
    """runner.run_trend() parses SwarmResult when Redis.get() returns JSON."""
    from datetime import UTC
    from unittest.mock import AsyncMock

    from aegis.agents.runner import run_trend
    from aegis.agents.schemas import TrendCandidate
    from aegis.scrape.swarm_result import SwarmResult

    now = __import__("datetime").datetime.now(UTC)
    swarm = SwarmResult(
        started_at=now,
        finished_at=now,
        total_signals=15,
        unique_signals=12,
        dedup_removed=3,
        by_platform={"hacker_news": 15},
        by_tier={"T3_search": 15},
        wave_stats=[],
        market_pulse="bullish",
        batch_confidence=0.90,
    )

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=swarm.model_dump_json())
    mock_redis.xadd = AsyncMock()

    candidate = TrendCandidate(
        trend_id="t-swarm-ok",
        title="Swarm context success test",
        signal_count=5,
        unique_authors=2,
        platforms=["hacker_news"],
        velocity_1h=0.1,
        velocity_6h=0.2,
        velocity_24h=0.3,
        sentiment=0.5,
        commercial_intent=0.6,
        novelty=0.7,
        coordination_risk=0.0,
    )

    result = await run_trend(
        candidate,
        use_llm=False,
        timeout_s=30.0,
        stream_client=mock_redis,
    )
    assert result is not None
    mock_redis.get.assert_called_once_with("aegis:swarm:latest")


# ---------------------------------------------------------------------------
# Phase 6: dashboard/app.py — swarm & platform endpoint coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_swarm_latest_no_data() -> None:
    from unittest.mock import AsyncMock, patch

    from httpx import ASGITransport, AsyncClient

    from aegis.dashboard.app import app

    mock_r = AsyncMock()
    mock_r.get = AsyncMock(return_value=None)
    mock_r.aclose = AsyncMock()

    with patch("aegis.dashboard.app._get_redis", return_value=mock_r):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/swarm/latest")

    assert resp.status_code == 200
    assert resp.json()["status"] == "no_data"


@pytest.mark.asyncio
async def test_dashboard_swarm_latest_with_data() -> None:
    from datetime import UTC
    from unittest.mock import AsyncMock, patch

    from httpx import ASGITransport, AsyncClient

    from aegis.dashboard.app import app
    from aegis.scrape.swarm_result import SwarmResult

    now = __import__("datetime").datetime.now(UTC)
    swarm = SwarmResult(
        started_at=now,
        finished_at=now,
        total_signals=20,
        unique_signals=18,
        dedup_removed=2,
        by_platform={"reddit": 20},
        by_tier={},
        wave_stats=[],
        market_pulse="bullish",
    )

    mock_r = AsyncMock()
    mock_r.get = AsyncMock(return_value=swarm.model_dump_json())
    mock_r.aclose = AsyncMock()

    with patch("aegis.dashboard.app._get_redis", return_value=mock_r):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/swarm/latest")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["data"]["total_signals"] == 20


@pytest.mark.asyncio
async def test_dashboard_swarm_latest_redis_error() -> None:
    from unittest.mock import AsyncMock, patch

    from httpx import ASGITransport, AsyncClient

    from aegis.dashboard.app import app

    mock_r = AsyncMock()
    mock_r.get = AsyncMock(side_effect=RuntimeError("redis down"))
    mock_r.aclose = AsyncMock()

    with patch("aegis.dashboard.app._get_redis", return_value=mock_r):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/swarm/latest")

    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


@pytest.mark.asyncio
async def test_dashboard_swarm_agents_no_data() -> None:
    from unittest.mock import AsyncMock, patch

    from httpx import ASGITransport, AsyncClient

    from aegis.dashboard.app import app

    mock_r = AsyncMock()
    mock_r.hgetall = AsyncMock(return_value={})
    mock_r.aclose = AsyncMock()

    with patch("aegis.dashboard.app._get_redis", return_value=mock_r):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/swarm/agents")

    assert resp.status_code == 200
    assert resp.json()["status"] == "no_data"


@pytest.mark.asyncio
async def test_dashboard_swarm_agents_with_data() -> None:
    import json as _json
    from unittest.mock import AsyncMock, patch

    from httpx import ASGITransport, AsyncClient

    from aegis.dashboard.app import app

    agent_data = {
        "flipkart": _json.dumps({"health": "UP", "consecutive_failures": 0, "avg_latency_ms": 150.0}),
    }
    mock_r = AsyncMock()
    mock_r.hgetall = AsyncMock(return_value=agent_data)
    mock_r.aclose = AsyncMock()

    with patch("aegis.dashboard.app._get_redis", return_value=mock_r):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/swarm/agents")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "flipkart" in data["agents"]


@pytest.mark.asyncio
async def test_dashboard_swarm_history_db_error() -> None:
    from unittest.mock import patch

    from httpx import ASGITransport, AsyncClient

    from aegis.dashboard.app import app

    with patch(
        "aegis.dashboard.app.asyncpg.connect",
        side_effect=ConnectionRefusedError("no db"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/swarm/history")

    assert resp.status_code == 200
    assert resp.json()["status"] == "error"
    assert resp.json()["runs"] == []


# DASH-3 [2026-06-11]: test_dashboard_platform_stats_db_error and
# test_dashboard_platform_trends_db_error removed with their endpoints
# (/api/platforms/stats superseded by /api/signals/platforms;
#  /api/platforms/trends superseded by /api/signals/velocity).
# The tombstone below guards against accidental route resurrection.
@pytest.mark.asyncio
async def test_dash3_deleted_routes_stay_deleted() -> None:
    from httpx import ASGITransport, AsyncClient

    from aegis.dashboard.app import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in ("/api/platforms/stats", "/api/platforms/trends", "/api/execute/alerts"):
            resp = await client.get(path)
            assert resp.status_code == 404, f"{path} was deleted in DASH-3 and must stay deleted"
