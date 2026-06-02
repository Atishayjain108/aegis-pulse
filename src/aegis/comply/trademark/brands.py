"""Curated registry of protected / high-enforcement brands.

This is a deliberately small, well-known seed set used for the deterministic
local screen. It is not exhaustive; live lookups (USPTO/EUIPO/WIPO) extend it.
Each entry maps a canonical mark -> (owner, jurisdiction, category hint).
"""

from __future__ import annotations

from aegis.comply.schemas import Jurisdiction

# mark (lowercase) -> (owner, jurisdiction, category_hint)
PROTECTED_BRANDS: dict[str, tuple[str, Jurisdiction, str]] = {
    "nike": ("Nike, Inc.", Jurisdiction.GLOBAL, "footwear"),
    "adidas": ("adidas AG", Jurisdiction.GLOBAL, "footwear"),
    "gucci": ("Guccio Gucci S.p.A.", Jurisdiction.GLOBAL, "handbags"),
    "louis vuitton": ("Louis Vuitton Malletier", Jurisdiction.GLOBAL, "handbags"),
    "chanel": ("Chanel Limited", Jurisdiction.GLOBAL, "handbags"),
    "prada": ("Prada S.A.", Jurisdiction.GLOBAL, "handbags"),
    "hermes": ("Hermes International", Jurisdiction.GLOBAL, "handbags"),
    "burberry": ("Burberry Limited", Jurisdiction.GLOBAL, "apparel"),
    "rolex": ("Rolex SA", Jurisdiction.GLOBAL, "watches"),
    "omega": ("Omega SA", Jurisdiction.GLOBAL, "watches"),
    "apple": ("Apple Inc.", Jurisdiction.GLOBAL, "electronics"),
    "samsung": ("Samsung Electronics Co.", Jurisdiction.GLOBAL, "electronics"),
    "sony": ("Sony Group Corporation", Jurisdiction.GLOBAL, "electronics"),
    "disney": ("The Walt Disney Company", Jurisdiction.GLOBAL, "toys"),
    "lego": ("LEGO Juris A/S", Jurisdiction.GLOBAL, "toys"),
    "supreme": ("Chapter 4 Corp.", Jurisdiction.GLOBAL, "apparel"),
    "ray-ban": ("Luxottica Group", Jurisdiction.GLOBAL, "apparel"),
    "the north face": ("VF Corporation", Jurisdiction.GLOBAL, "apparel"),
    "yeezy": ("Yeezy LLC", Jurisdiction.GLOBAL, "footwear"),
    "balenciaga": ("Balenciaga S.A.", Jurisdiction.GLOBAL, "apparel"),
    "tata": ("Tata Sons Private Limited", Jurisdiction.IN, "general"),
    "reliance": ("Reliance Industries Limited", Jurisdiction.IN, "general"),
    "fabindia": ("Fabindia Limited", Jurisdiction.IN, "apparel"),
}

#: Marks whose categories carry the highest counterfeit exposure.
HIGH_COUNTERFEIT_CATEGORIES: frozenset[str] = frozenset(
    {"handbags", "watches", "footwear", "apparel"}
)
