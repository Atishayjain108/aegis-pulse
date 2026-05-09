"""Concrete source adapters."""

from __future__ import annotations

from aegis.scrape.sources.amazon import AmazonAdapter, AmazonConfig
from aegis.scrape.sources.google_trends import GoogleTrendsAdapter, GoogleTrendsConfig
from aegis.scrape.sources.hacker_news import HackerNewsAdapter, HackerNewsConfig
from aegis.scrape.sources.instagram import InstagramAdapter, InstagramConfig
from aegis.scrape.sources.pinterest import PinterestAdapter, PinterestConfig
from aegis.scrape.sources.reddit import RedditAdapter, RedditConfig
from aegis.scrape.sources.tiktok import TikTokAdapter, TikTokConfig
from aegis.scrape.sources.youtube import YouTubeAdapter, YouTubeConfig

__all__ = [
    "AmazonAdapter",
    "AmazonConfig",
    "GoogleTrendsAdapter",
    "GoogleTrendsConfig",
    "HackerNewsAdapter",
    "HackerNewsConfig",
    "InstagramAdapter",
    "InstagramConfig",
    "PinterestAdapter",
    "PinterestConfig",
    "RedditAdapter",
    "RedditConfig",
    "TikTokAdapter",
    "TikTokConfig",
    "YouTubeAdapter",
    "YouTubeConfig",
]
