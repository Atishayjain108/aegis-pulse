"""GitHub Trending source adapter (no API key required).

Scrapes https://github.com/trending to discover trending repositories.
GitHub's trending page is fully public — no authentication required.

ToS Risk: GREEN — public page; scraping is not prohibited by GitHub's
robots.txt for reasonable read-only research traffic.

Useful for AEGIS because trending repos often correlate with emerging
technologies, frameworks, and market opportunities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from aegis.core.logging import get_logger
from aegis.schemas.enums import (
    ContentModality,
    IntentType,
    Platform,
    ScrapeMethod,
    SourceTier,
    ToSRisk,
)
from aegis.schemas.signal import (
    Author,
    ConfidenceMetadata,
    EngagementMetrics,
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter
from aegis.scrape.http_client import get_or_create_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = get_logger(__name__)

SCRAPER_VERSION = "github-trending-0.1.0"

_GITHUB_TRENDING_URL = "https://github.com/trending"


@dataclass(frozen=True, slots=True)
class GitHubTrendingConfig(AdapterConfig):
    """GitHub Trending adapter config."""

    name: str = "github-trending"
    per_source_rps: float = 0.5
    timeout_seconds: float = 20.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False

    language: str = ""
    """Filter by programming language (e.g. 'python', 'javascript'). Empty = all."""

    since: str = "daily"
    """Time window: 'daily' | 'weekly' | 'monthly'."""


class GitHubTrendingAdapter(SourceAdapter[dict[str, Any]]):
    """GitHub Trending adapter — no API key required."""

    def __init__(self, config: GitHubTrendingConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._gh_config = (
            config if isinstance(config, GitHubTrendingConfig) else GitHubTrendingConfig()
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "github-trending"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = await get_or_create_client(
            "github.com",
            http2=False,
            timeout=self._gh_config.timeout_seconds,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; aegis-pulse/0.1; research bot)",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        # Shared pooled client (PASS5-5B) — release the reference, never close.
        self._client = None

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        query: str | None = None,
        limit: int = 25,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("GitHubTrendingAdapter.setup() must run before fetch_raw()")

        cfg = self._gh_config
        params: dict[str, str] = {"since": cfg.since}
        if cfg.language:
            params["l"] = cfg.language
        elif query:
            params["l"] = query  # treat query as language filter

        await self._rate_limit()
        self._record_request_metric(method="github_html")

        try:
            resp = await self._client.get(_GITHUB_TRENDING_URL, params=params)
            resp.raise_for_status()
            html = resp.text
        except httpx.HTTPStatusError as e:
            log.warning("github_trending.fetch.http_error", status=e.response.status_code)
            return
        except httpx.RequestError as e:
            log.warning("github_trending.fetch.request_error", error=str(e))
            return

        repos = _parse_trending_page(html)
        for total_yielded, repo in enumerate(repos):
            if total_yielded >= limit or self.is_cancelled:
                return
            yield repo

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            owner: str = str(raw.get("owner") or "")
            repo_name: str = str(raw.get("repo") or "")
            if not (owner and repo_name):
                return None

            full_name = f"{owner}/{repo_name}"
            description: str = str(raw.get("description") or "")
            language: str = str(raw.get("language") or "")
            stars: int = int(raw.get("stars") or 0)
            stars_today: int = int(raw.get("stars_today") or 0)
            forks: int = int(raw.get("forks") or 0)

            url = f"https://github.com/{full_name}"

            author = Author(
                platform_user_id=owner,
                handle=owner,
                profile_url=f"https://github.com/{owner}",  # type: ignore[arg-type]
            )

            tags_raw: list[str] = [t for t in [language.lower()] if t]
            # Strip characters not allowed by the tag pattern (^[a-z0-9_\-\.]+$)
            # e.g. "c++" → "c", "c#" → "c"
            tags = frozenset(
                clean
                for t in tags_raw
                if (clean := re.sub(r"[^a-z0-9_\-.]", "", t.replace(" ", "_"))[:128])
            )

            signal_title = f"{full_name}: {description}"[:512] if description else full_name

            h = compute_content_hash(
                platform=Platform.GITHUB_TRENDING,
                external_id=full_name,
                url=url,
                title=signal_title,
                raw_text=description or None,
                posted_at=None,
            )

            return ProductSignal(
                platform=Platform.GITHUB_TRENDING,
                tier=SourceTier.TIER_5_ALTERNATIVE,
                external_id=full_name,
                url=url,  # type: ignore[arg-type]
                title=signal_title,
                raw_text=description or None,
                modality=ContentModality.STRUCTURED,
                tags=tags,
                intent=IntentType.ENGAGE,
                author=author,
                engagement=EngagementMetrics(
                    likes=stars,
                    shares=forks,
                ),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.85 if description else 0.55,
                    source_confidence=0.90,
                ),
                content_hash=h,
                platform_specific={
                    "owner": owner,
                    "repo": repo_name,
                    "language": language,
                    "stars": stars,
                    "stars_today": stars_today,
                    "forks": forks,
                },
            )
        except Exception as e:
            log.warning("github_trending.parse.failed", error=str(e))
            return None


def _parse_trending_page(html: str) -> list[dict[str, Any]]:
    """Extract repository data from GitHub trending HTML.

    GitHub uses ``<article class="Box-row">`` elements for each repo.
    We use lightweight regex to avoid a full HTML parser dep.
    """
    repos: list[dict[str, Any]] = []
    articles = re.split(r'<article\s+class="Box-row"', html)
    for block in articles[1:]:
        repo = _parse_repo_block(block)
        if repo:
            repos.append(repo)
    return repos


def _parse_repo_block(block: str) -> dict[str, Any] | None:
    try:
        # Repo full name from h2 link
        name_match = re.search(
            r'href="/([^/"]+)/([^/"]+)"',
            block,
        )
        if not name_match:
            return None
        owner = name_match.group(1)
        repo = name_match.group(2).split('"')[0]

        # Description
        desc_match = re.search(r'<p[^>]*class="[^"]*col-9[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL)
        description = _clean(desc_match.group(1)) if desc_match else ""

        # Language
        lang_match = re.search(r'itemprop="programmingLanguage"[^>]*>([^<]+)<', block)
        language = _clean(lang_match.group(1)) if lang_match else ""

        # Total stars (look for star icon + number)
        stars_match = re.search(r'href="[^"]+/stargazers[^"]*"[^>]*>\s*([\d,]+)', block)
        stars = _parse_int(stars_match.group(1)) if stars_match else 0

        # Stars today
        today_match = re.search(r"([\d,]+)\s+stars\s+today", block)
        stars_today = _parse_int(today_match.group(1)) if today_match else 0

        # Forks
        forks_match = re.search(r'href="[^"]+/forks[^"]*"[^>]*>\s*([\d,]+)', block)
        forks = _parse_int(forks_match.group(1)) if forks_match else 0

        return {
            "owner": owner,
            "repo": repo,
            "description": description,
            "language": language,
            "stars": stars,
            "stars_today": stars_today,
            "forks": forks,
        }
    except Exception:
        return None


def _clean(html_fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_fragment)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def _parse_int(s: str) -> int:
    try:
        return int(s.replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0


__all__ = ["SCRAPER_VERSION", "GitHubTrendingAdapter", "GitHubTrendingConfig"]
