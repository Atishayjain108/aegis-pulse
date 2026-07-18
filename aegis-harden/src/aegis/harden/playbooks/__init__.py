"""
Per-source playbook loader and matcher.

A playbook is a YAML file describing how to scrape one upstream source. The
loader:
  * Validates each file against `Playbook` schema (strict).
  * Enforces hard bounds (rate, delay, retries) from `constants.py`.
  * Builds a matcher that picks the right playbook for an incoming URL
    given a `(source, domain)` pair from Phase 1.

Two-level matching priority:
  1. Exact `source` match (e.g. "reddit-rss") — wins immediately.
  2. Domain glob match (e.g. "*.amazon.com") — first match by file load order.
  3. Fall back to `default` playbook if defined; else raise.

Source slugs are intentionally distinct from domains: Phase 1's adapters use
slugs like `reddit-rss` and `hacker-news` that don't correspond cleanly to a
domain. Domain matching is the fallback for ad-hoc URLs.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError

from aegis.harden.constants import PLAYBOOK_REQUIRED_KEYS
from aegis.harden.errors import PlaybookValidationError, make
from aegis.harden.metrics import M
from aegis.harden.schemas import Playbook, PlaybookMatch
from aegis.harden.utils.logging import get_logger

_log = get_logger("aegis.harden.playbooks")


class PlaybookRegistry:
    """In-memory registry of validated playbooks with O(1) source lookup."""

    __slots__ = ("_by_source", "_by_domain", "_default", "_all")

    def __init__(self) -> None:
        self._by_source: dict[str, Playbook] = {}
        self._by_domain: list[tuple[str, Playbook]] = []
        self._default: Playbook | None = None
        self._all: list[Playbook] = []

    # ---- Public API ---------------------------------------------------------

    @property
    def size(self) -> int:
        return len(self._all)

    @property
    def has_default(self) -> bool:
        return self._default is not None

    def all(self) -> tuple[Playbook, ...]:
        return tuple(self._all)

    def register(self, pb: Playbook) -> None:
        """Add a single (pre-validated) playbook to the registry."""
        self._all.append(pb)
        if pb.match.source:
            self._by_source[pb.match.source] = pb
        if pb.match.domain:
            self._by_domain.append((pb.match.domain, pb))
        if pb.name == "default":
            self._default = pb

    def match(self, source: str | None = None, url: str | None = None) -> Playbook:
        """Match a playbook for the given source slug and/or URL.

        Resolution order:
          1. Exact source match.
          2. Domain glob match against `url`'s hostname.
          3. Default playbook.

        Raises PlaybookValidationError(AEGIS-HARDEN-0004) if no match.
        """
        # 1) Source slug — preferred path (deterministic)
        if source and source in self._by_source:
            pb = self._by_source[source]
            M.playbook_matches_total.labels(name=pb.name).inc()
            return pb

        # 2) Domain glob — fallback for ad-hoc URLs
        if url:
            host = _host_of(url)
            if host:
                for pattern, pb in self._by_domain:
                    # Match against host, also try host with no trailing port
                    if fnmatch.fnmatchcase(host, pattern) or fnmatch.fnmatchcase(
                        host, pattern.lstrip("*.")
                    ):
                        M.playbook_matches_total.labels(name=pb.name).inc()
                        return pb

        # 3) Default
        if self._default is not None:
            M.playbook_matches_total.labels(name=self._default.name).inc()
            return self._default

        M.playbook_misses_total.inc()
        raise PlaybookValidationError(*make("AEGIS-HARDEN-0004", source=source, url=url))


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_playbook_dict(data: dict) -> Playbook:
    """Validate a single playbook dict and return a frozen `Playbook` model.

    Two-stage validation:
      * Required-keys check (gives a precise error code AEGIS-HARDEN-0001).
      * Full Pydantic schema validation (catches bounds, types, etc.).
    """
    if not isinstance(data, dict):
        raise PlaybookValidationError(*make("AEGIS-HARDEN-0001", reason="not a mapping"))

    missing = [k for k in PLAYBOOK_REQUIRED_KEYS if k not in data]
    if missing:
        raise PlaybookValidationError(*make("AEGIS-HARDEN-0001", missing=missing))

    # `match` is allowed to be a flat mapping OR a nested object.
    # Normalize so Pydantic sees the nested shape.
    if "match" in data and not isinstance(data["match"], dict):
        raise PlaybookValidationError(*make("AEGIS-HARDEN-0001", reason="match must be a mapping"))

    try:
        pb = Playbook.model_validate(data)
    except ValidationError as exc:
        # Pydantic's first error gives the offending field.
        err = exc.errors()[0]
        raise PlaybookValidationError(
            *make("AEGIS-HARDEN-0002", field=".".join(str(x) for x in err["loc"]), msg=err["msg"]),
        ) from exc

    return pb


def load_playbook_file(path: Path) -> Playbook:
    """Read one YAML file and return a validated `Playbook`."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PlaybookValidationError(
            *make("AEGIS-HARDEN-0001", path=str(path), error=str(exc))
        ) from exc
    if raw is None:
        raise PlaybookValidationError(
            *make("AEGIS-HARDEN-0001", path=str(path), reason="empty file")
        )
    return load_playbook_dict(raw)


def load_dir(path: Path) -> PlaybookRegistry:
    """Load every `*.yaml`/`*.yml` file in `path` into a registry.

    Files that fail validation are logged and skipped — partial registries
    are useful for ops who want to roll out playbooks incrementally.
    """
    registry = PlaybookRegistry()
    if not path.exists() or not path.is_dir():
        _log.warning("playbook_dir_missing", path=str(path))
        return registry

    for f in sorted(path.iterdir()):
        if not f.is_file() or f.suffix.lower() not in (".yaml", ".yml"):
            continue
        try:
            pb = load_playbook_file(f)
            registry.register(pb)
        except PlaybookValidationError as exc:
            _log.warning("playbook_invalid", path=str(f), code=exc.code, ctx=exc.context)
    _log.info("playbooks_loaded", count=registry.size, has_default=registry.has_default)
    return registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _host_of(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    return host or None


# ---------------------------------------------------------------------------
# Built-in fallback registry — works without any YAML files.
# Mirrors the working adapters in Phase 1's CLAUDE.md.
# ---------------------------------------------------------------------------


def builtin_registry() -> PlaybookRegistry:
    """Return a registry pre-populated with sensible Phase 1 defaults."""
    registry = PlaybookRegistry()
    for pb in (
        Playbook(
            name="default",
            version=1,
            match=PlaybookMatch(),
            delay_ms=1_000,
            jitter_ms=500,
            rate_per_min=30,
            retries=3,
            profile="standard",
            honor_robots=True,
            rotate_every=10,
            notes="Conservative default for unknown sources.",
        ),
        Playbook(
            name="reddit-rss",
            version=1,
            match=PlaybookMatch(source="reddit-rss"),
            delay_ms=600,
            jitter_ms=300,
            rate_per_min=60,
            retries=3,
            profile="standard",
            honor_robots=True,
            rotate_every=20,
            notes="Reddit public RSS — generous; no auth needed.",
        ),
        Playbook(
            name="hacker-news",
            version=1,
            match=PlaybookMatch(source="hacker-news"),
            delay_ms=300,
            jitter_ms=100,
            rate_per_min=120,
            retries=3,
            profile="minimal",
            honor_robots=True,
            rotate_every=30,
            notes="Algolia API — fast & friendly.",
        ),
        Playbook(
            name="github-trending",
            version=1,
            match=PlaybookMatch(source="github-trending"),
            delay_ms=500,
            jitter_ms=200,
            rate_per_min=60,
            retries=3,
            profile="standard",
            honor_robots=True,
            rotate_every=20,
            notes="GitHub trending HTML.",
        ),
        Playbook(
            name="amazon",
            version=1,
            match=PlaybookMatch(source="amazon", domain="*.amazon.com"),
            delay_ms=2_000,
            jitter_ms=1_000,
            rate_per_min=20,
            retries=4,
            profile="stealth",
            honor_robots=False,  # Amazon's robots.txt forbids commerce scraping
            use_flaresolverr=True,
            rotate_every=5,
            notes="High-friction; rotate aggressively.",
        ),
    ):
        registry.register(pb)
    return registry


__all__ = [
    "PlaybookRegistry",
    "builtin_registry",
    "load_dir",
    "load_playbook_dict",
    "load_playbook_file",
]
