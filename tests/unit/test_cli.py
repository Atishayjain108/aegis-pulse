"""Unit tests for the CLI and base adapter layer."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from aegis.cli.main import main

# ---------------------------------------------------------------------------
# CLI — help and version (no infra needed)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "AEGIS" in result.output


@pytest.mark.unit
def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_cli_scrape_help():
    runner = CliRunner()
    result = runner.invoke(main, ["scrape", "--help"])
    assert result.exit_code == 0
    assert "--source" in result.output
    assert "--limit" in result.output
    assert "--dry-run" in result.output


@pytest.mark.unit
def test_cli_report_help():
    runner = CliRunner()
    result = runner.invoke(main, ["report", "--help"])
    assert result.exit_code == 0
    assert "daily" in result.output


@pytest.mark.unit
def test_cli_signals_help():
    runner = CliRunner()
    result = runner.invoke(main, ["signals", "--help"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_cli_up_help():
    runner = CliRunner()
    result = runner.invoke(main, ["up", "--help"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_cli_down_help():
    runner = CliRunner()
    result = runner.invoke(main, ["down", "--help"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_cli_migrate_help():
    runner = CliRunner()
    result = runner.invoke(main, ["migrate", "--help"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_cli_doctor_help():
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# _repo_root
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_repo_root_returns_path():
    from pathlib import Path

    from aegis.cli.main import _repo_root

    root = _repo_root()
    assert isinstance(root, Path)


# ---------------------------------------------------------------------------
# _build_adapter dispatch
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_adapter_hacker_news(monkeypatch):
    from aegis.cli.main import _build_adapter
    from aegis.scrape.sources.hacker_news import HackerNewsAdapter

    adapter = _build_adapter("hacker-news", HackerNewsAdapter, limit=10)
    assert isinstance(adapter, HackerNewsAdapter)


@pytest.mark.unit
def test_build_adapter_google_trends(monkeypatch):
    from aegis.cli.main import _build_adapter
    from aegis.scrape.sources.google_trends import GoogleTrendsAdapter

    adapter = _build_adapter("google-trends", GoogleTrendsAdapter, limit=10)
    assert isinstance(adapter, GoogleTrendsAdapter)


@pytest.mark.unit
def test_build_adapter_tiktok(monkeypatch):
    from aegis.cli.main import _build_adapter
    from aegis.scrape.sources.tiktok import TikTokAdapter

    adapter = _build_adapter("tiktok", TikTokAdapter, limit=10)
    assert isinstance(adapter, TikTokAdapter)


@pytest.mark.unit
def test_build_adapter_pinterest(monkeypatch):
    from aegis.cli.main import _build_adapter
    from aegis.scrape.sources.pinterest import PinterestAdapter

    adapter = _build_adapter("pinterest", PinterestAdapter, limit=10)
    assert isinstance(adapter, PinterestAdapter)


@pytest.mark.unit
def test_build_adapter_amazon(monkeypatch):
    from aegis.cli.main import _build_adapter
    from aegis.scrape.sources.amazon import AmazonAdapter

    adapter = _build_adapter("amazon", AmazonAdapter, limit=10)
    assert isinstance(adapter, AmazonAdapter)


@pytest.mark.unit
def test_build_adapter_instagram(monkeypatch):
    from aegis.cli.main import _build_adapter
    from aegis.scrape.sources.instagram import InstagramAdapter

    adapter = _build_adapter("instagram", InstagramAdapter, limit=10)
    assert isinstance(adapter, InstagramAdapter)


@pytest.mark.unit
def test_build_adapter_reddit_missing_credentials_raises(monkeypatch):
    import click

    from aegis.cli.main import _build_adapter
    from aegis.scrape.sources.reddit import RedditAdapter

    # Ensure Reddit credentials are absent
    monkeypatch.setenv("AEGIS_PG_DSN", "postgresql://localhost/test")
    monkeypatch.setenv("AEGIS_REDIS_URL", "redis://localhost/0")
    monkeypatch.delenv("AEGIS_REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("AEGIS_REDDIT_CLIENT_SECRET", raising=False)

    # Reload settings so the monkeypatched env takes effect
    import aegis.config as _cfg

    _cfg._settings_instance = None  # type: ignore[attr-defined]

    with pytest.raises(click.UsageError, match="AEGIS_REDDIT_CLIENT_ID"):
        _build_adapter("reddit", RedditAdapter, limit=10)


@pytest.mark.unit
def test_build_adapter_youtube_missing_key_raises(monkeypatch):
    import click

    from aegis.cli.main import _build_adapter
    from aegis.scrape.sources.youtube import YouTubeAdapter

    monkeypatch.setenv("AEGIS_PG_DSN", "postgresql://localhost/test")
    monkeypatch.setenv("AEGIS_REDIS_URL", "redis://localhost/0")
    monkeypatch.delenv("AEGIS_YOUTUBE_API_KEY", raising=False)

    import aegis.config as _cfg

    _cfg._settings_instance = None  # type: ignore[attr-defined]

    with pytest.raises(click.UsageError, match="AEGIS_YOUTUBE_API_KEY"):
        _build_adapter("youtube", YouTubeAdapter, limit=10)


# ---------------------------------------------------------------------------
# Token bucket
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_token_bucket_acquires_token():
    from aegis.scrape.base import _TokenBucket

    bucket = _TokenBucket(rate_per_sec=100.0)  # very fast
    await bucket.acquire()  # should not block


@pytest.mark.unit
@pytest.mark.asyncio
async def test_token_bucket_rejects_oversized_request():
    from aegis.scrape.base import _TokenBucket

    bucket = _TokenBucket(rate_per_sec=1.0, capacity=2.0)
    with pytest.raises(ValueError, match="capacity"):
        await bucket.acquire(5.0)


@pytest.mark.unit
def test_token_bucket_rejects_zero_rate():
    from aegis.scrape.base import _TokenBucket

    with pytest.raises(ValueError, match="rate_per_sec"):
        _TokenBucket(rate_per_sec=0.0)


# ---------------------------------------------------------------------------
# AdapterConfig
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_adapter_config_defaults():
    from aegis.scrape.base import AdapterConfig

    cfg = AdapterConfig(name="test-adapter")
    assert cfg.name == "test-adapter"
    assert cfg.max_signals == 100
    assert cfg.max_retries == 3
    assert cfg.use_cloudflare_bypass is False


@pytest.mark.unit
def test_adapter_config_custom():
    from aegis.scrape.base import AdapterConfig

    cfg = AdapterConfig(name="custom", per_source_rps=2.5, timeout_seconds=15.0)
    assert cfg.per_source_rps == 2.5
    assert cfg.timeout_seconds == 15.0


# ---------------------------------------------------------------------------
# SourceAdapter.classify_response
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_classify_response_ok():
    from aegis.scrape.sources.hacker_news import HackerNewsAdapter, HackerNewsConfig

    adapter = HackerNewsAdapter(HackerNewsConfig())
    assert adapter.classify_response(status_code=200, body="<html>...</html>", headers={}) == "ok"


@pytest.mark.unit
def test_classify_response_rate_limit():
    from aegis.scrape.sources.hacker_news import HackerNewsAdapter, HackerNewsConfig

    adapter = HackerNewsAdapter(HackerNewsConfig())
    assert adapter.classify_response(status_code=429, body=None, headers={}) == "rate_limit"


@pytest.mark.unit
def test_classify_response_banned():
    from aegis.scrape.sources.hacker_news import HackerNewsAdapter, HackerNewsConfig

    adapter = HackerNewsAdapter(HackerNewsConfig())
    assert adapter.classify_response(status_code=403, body=None, headers={}) == "banned"


@pytest.mark.unit
def test_classify_response_banned_451():
    from aegis.scrape.sources.hacker_news import HackerNewsAdapter, HackerNewsConfig

    adapter = HackerNewsAdapter(HackerNewsConfig())
    assert adapter.classify_response(status_code=451, body=None, headers={}) == "banned"
