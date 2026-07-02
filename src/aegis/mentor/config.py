"""MentorSettings — AEGIS Mentor configuration (AEGIS_MENTOR_* env vars).

The Mentor is heuristic-first: every setting here tunes *behaviour*, never
substitutes for real data. LLM enrichment is opt-out-able so the zero-API-key
test path stays green.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default tenant — shared across AEGIS for single-tenant dev / CI.
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


class MentorSettings(BaseSettings):
    """All Mentor configuration. Override via AEGIS_MENTOR_<NAME> env var."""

    model_config = SettingsConfigDict(env_prefix="AEGIS_MENTOR_", extra="ignore")

    # -------------------------------------------------------------------------
    # Region / currency defaults (India / INR first — see blueprint).
    # -------------------------------------------------------------------------
    default_region: str = Field(default="IN", description="ISO region tag for new profiles.")
    default_currency: str = Field(default="INR", description="Currency for new profiles.")

    # -------------------------------------------------------------------------
    # LLM enrichment. When disabled (or no keys), the Mentor still produces a
    # full deterministic answer — the LLM only enriches prose.
    # -------------------------------------------------------------------------
    llm_enrichment_enabled: bool = Field(
        default=True,
        description="Use the LLM gateway to enrich intent parsing + counsel prose.",
    )
    llm_temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    llm_max_tokens: int = Field(default=1024, ge=64)

    # -------------------------------------------------------------------------
    # Sector specialization. The deep pack is loaded for the active sector;
    # everything else falls back to the broad baseline pack.
    # -------------------------------------------------------------------------
    default_sector: str = Field(
        default="d2c_india",
        description="Sector tag whose deep SectorPack is preferred when none is inferred.",
    )

    # -------------------------------------------------------------------------
    # Tenancy.
    # -------------------------------------------------------------------------
    tenant_id: str = Field(default=DEFAULT_TENANT_ID)
