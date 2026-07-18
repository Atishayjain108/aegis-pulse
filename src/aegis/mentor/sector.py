"""SectorRouter + SectorPack protocol.

Two tiers of knowledge (see blueprint "Depth-first knowledge strategy"):

* **Broad baseline** — a wide, shallow map so AEGIS is never blind in any
  field. Backed by :class:`BaselinePack`.
* **Deep specialization** — one sector at a time gets a rich :class:`SectorPack`
  with real benchmarks, supplier landscape, regulations, failure patterns.

The router normalizes a user's free-text field → a sector tag, then resolves
the tag → a pack (deep pack when registered, baseline otherwise). The registry
is extensible; new deep packs are added one at a time. A half-built sector can
never degrade a finished one because each pack is self-contained.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

import structlog

from aegis.mentor.schemas import SECTOR_UNDECIDED

_log = structlog.get_logger("aegis.mentor.sector")


@runtime_checkable
class SectorPack(Protocol):
    """A pluggable bundle of domain intelligence for one sector.

    A pack is pure data + tuned behaviour — no I/O at construction. Methods
    return real, grounded reference numbers (never random).
    """

    sector_tag: str
    display_name: str
    region_default: str
    is_deep: bool

    def benchmarks(self) -> dict[str, float]:
        """Real reference numbers: margins, CAC, return rates, etc."""
        ...

    def knowledge(self) -> dict[str, object]:
        """Ground-reality facts: marketplaces, regulations, failure patterns."""
        ...

    def operator_tuning(self) -> dict[str, object]:
        """Per-operator behaviour overrides for this sector."""
        ...


# ---------------------------------------------------------------------------
# Broad baseline pack — competent first answer in ANY field.
# ---------------------------------------------------------------------------


class BaselinePack:
    """Wide, shallow knowledge so AEGIS can always frame a competent answer.

    Deliberately conservative: it exposes generic structural knowledge, never
    sector-specific numbers it cannot ground. Deep packs override it.
    """

    is_deep = False
    region_default = "IN"

    def __init__(self, sector_tag: str = SECTOR_UNDECIDED, display_name: str = "General business") -> None:
        self.sector_tag = sector_tag
        self.display_name = display_name

    def benchmarks(self) -> dict[str, float]:
        # No fabricated sector numbers — baseline only exposes structural priors.
        return {}

    def knowledge(self) -> dict[str, object]:
        return {
            "tier": "baseline",
            "note": (
                "Broad baseline knowledge only. For grounded sector benchmarks, "
                "a deep SectorPack must be registered for this field."
            ),
            "universal_levers": [
                "demand depth",
                "unit economics (margin after all costs)",
                "customer acquisition cost vs lifetime value",
                "supplier reliability",
                "channel fit",
            ],
        }

    def operator_tuning(self) -> dict[str, object]:
        return {}


# ---------------------------------------------------------------------------
# SectorRouter
# ---------------------------------------------------------------------------

# Keyword → normalized sector tag. Ordered most-specific first. These map a
# user's free-text field onto a tag; the tag then resolves to a pack.
_SECTOR_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("d2c_india", ("d2c", "dropship", "ecommerce", "e-commerce", "online store",
                   "shopify", "amazon seller", "flipkart", "meesho", "apparel brand",
                   "private label", "products online", "selling online",
                   "sell online", "sell products online", "online store")),
    ("local_retail", ("kirana", "shop", "store owner", "retail", "fmcg", "grocery",
                      "local business", "stockist", "wholesale")),
    ("creator_services", ("freelance", "designer", "creator", "consultant",
                          "service", "agency", "coaching", "content")),
    ("saas", ("saas", "software", "app", "platform", "developer tool", "api product")),
    ("research", ("research", "explore", "studying", "student", "learn about",
                  "exploring")),
)


class SectorRouter:
    """Normalize free-text field → sector tag → SectorPack.

    Deep packs are registered in ``_packs``. Anything without a deep pack falls
    back to the baseline pack so the system is never blind.
    """

    def __init__(self) -> None:
        self._packs: dict[str, SectorPack] = {}
        self._register_default_packs()

    def _register_default_packs(self) -> None:
        # Lazy import avoids a circular dependency at module import time.
        from aegis.mentor.packs.d2c_india import D2CIndiaPack

        self.register(D2CIndiaPack())

    def register(self, pack: SectorPack) -> None:
        """Add (or replace) a deep pack for its sector tag."""
        self._packs[pack.sector_tag] = pack
        _log.info("mentor.sector.pack_registered", sector=pack.sector_tag, deep=pack.is_deep)

    def normalize(self, free_text: str) -> str:
        """Map the user's words to a sector tag, or ``undecided`` if unclear.

        Never fabricates: when nothing matches, returns ``SECTOR_UNDECIDED`` so
        the caller asks a clarifying question.
        """
        if not free_text or not free_text.strip():
            return SECTOR_UNDECIDED

        text = free_text.lower()

        # An exact tag passthrough lets callers route a known tag directly.
        normalized = re.sub(r"[\s\-]+", "_", text.strip())
        if normalized in self._packs:
            return normalized

        for tag, keywords in _SECTOR_KEYWORDS:
            if any(kw in text for kw in keywords):
                return tag

        return SECTOR_UNDECIDED

    def route(self, sector_tag: str) -> SectorPack:
        """Resolve a sector tag to its pack (deep pack or baseline)."""
        pack = self._packs.get(sector_tag)
        if pack is not None:
            return pack
        # Baseline for undecided / unregistered sectors — competent, never blind.
        display = "Undecided" if sector_tag == SECTOR_UNDECIDED else sector_tag.replace("_", " ").title()
        return BaselinePack(sector_tag=sector_tag, display_name=display)

    def deep_sectors(self) -> tuple[str, ...]:
        """Sector tags that currently have a deep pack registered."""
        return tuple(tag for tag, p in self._packs.items() if p.is_deep)
