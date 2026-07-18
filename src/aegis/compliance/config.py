"""ComplianceSettings — Phase 8 configuration (AEGIS_COMPLY_* env vars)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ComplianceSettings(BaseSettings):
    """All Phase 8 configuration.  Override any value via AEGIS_COMPLY_<NAME> env var."""

    model_config = SettingsConfigDict(env_prefix="AEGIS_COMPLY_", extra="ignore")

    # -------------------------------------------------------------------------
    # Optional API keys (graceful degradation when absent)
    # -------------------------------------------------------------------------
    fda_api_key: str = Field(
        default="",
        description="OpenFDA API key — 1 000 req/day without, 120 000 with.",
    )
    trade_gov_api_key: str = Field(
        default="",
        description="api.trade.gov Consolidated Screening List subscription key.",
    )

    # -------------------------------------------------------------------------
    # Risk decision thresholds (0–1)
    # -------------------------------------------------------------------------
    block_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    escalate_threshold: float = Field(default=0.50, ge=0.0, le=1.0)

    # -------------------------------------------------------------------------
    # Cache TTLs
    # -------------------------------------------------------------------------
    ipr_cache_ttl_hours: int = Field(default=24, ge=1)
    fda_cache_ttl_hours: int = Field(default=24, ge=1)
    ofac_cache_ttl_hours: int = Field(default=24, ge=1)
    result_cache_ttl_hours: int = Field(default=6, ge=1)

    # -------------------------------------------------------------------------
    # HTTP timeouts
    # -------------------------------------------------------------------------
    api_timeout_s: float = Field(default=15.0, ge=1.0)

    # -------------------------------------------------------------------------
    # Counterfeit detection
    # -------------------------------------------------------------------------
    clip_enabled: bool = Field(
        default=False,
        description="Enable CLIP image-similarity counterfeit detection (requires openai-clip + torch).",
    )
    clip_model: str = Field(default="ViT-B/32")
    clip_threshold: float = Field(default=0.85, ge=0.0, le=1.0)

    # -------------------------------------------------------------------------
    # FTC / advertising
    # -------------------------------------------------------------------------
    ftc_check_enabled: bool = True

    # -------------------------------------------------------------------------
    # Privacy
    # -------------------------------------------------------------------------
    privacy_check_enabled: bool = True
