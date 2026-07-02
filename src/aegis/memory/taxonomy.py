"""
aegis.memory.taxonomy — unified opportunity + failure vocabularies (Rule 9 / Rule 5).

These enums are the single source of truth for opportunity classification across
every domain (products, services, software, AI, manufacturing, logistics,
information / geographic / regulatory arbitrage) and for failure root-causing.

Kept as plain ``str`` enums so values persist directly to the TEXT columns in
``0018_knowledge_memory.sql`` and round-trip without conversion.
"""

from __future__ import annotations

from enum import Enum


class OpportunityType(str, Enum):
    """The global-opportunity framework (Rule 9). One unified schema."""

    PRODUCT = "product"
    SERVICE = "service"
    SOFTWARE = "software"
    AI = "ai"
    MANUFACTURING = "manufacturing"
    LOGISTICS = "logistics"
    INFO_ARB = "info_arb"          # information arbitrage — bare signal-trend claims
    GEO_ARB = "geo_arb"            # geographic / cross-market arbitrage (Phase 7)
    REGULATORY_ARB = "regulatory_arb"


class OpportunityOutcome(str, Enum):
    PENDING = "pending"
    REALIZED = "realized"
    FAILED = "failed"
    EXPIRED = "expired"


class EntityKind(str, Enum):
    """Long-lived entities AEGIS remembers across time (Rule 3)."""

    PRODUCT = "product"
    BRAND = "brand"
    COMPANY = "company"
    SUPPLIER = "supplier"
    MARKETPLACE = "marketplace"
    REGION = "region"
    COUNTRY = "country"
    REGULATION = "regulation"
    TECHNOLOGY = "technology"
    SOFTWARE = "software"


class RelationType(str, Enum):
    """Edges in the knowledge graph (Rule 7)."""

    SIGNAL_OF = "signal_of"          # signal → opportunity
    OPPORTUNITY_OF = "opportunity_of"  # opportunity → entity
    SOURCED_BY = "sourced_by"        # opportunity → source
    OUTCOME_OF = "outcome_of"        # outcome → opportunity
    TRUSTED_VIA = "trusted_via"      # entity → source
    CAUSED_BY = "caused_by"          # failure → root cause


class FailureCategory(str, Enum):
    """Why a settled opportunity failed (Rule 5). Reusable knowledge."""

    FALSE_BREAKOUT = "false_breakout"        # claimed rise, observed fall
    MISSED_BREAKOUT = "missed_breakout"      # claimed fall, observed rise
    DECAYED_OR_STALLED = "decayed_or_stalled"  # claimed movement, observed flat
    EVIDENCE_THIN = "evidence_thin"          # too few signals underpinning the claim
    OVERCONFIDENT = "overconfident"          # high confidence on a wrong call
    UNCLASSIFIED = "unclassified"
